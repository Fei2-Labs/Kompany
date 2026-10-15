// Kompany desktop shell.
//
// Responsibilities:
//   1. Local engine. Attach-if-running (06-12-daemon-tick-loop D2): if
//      `<data_dir>/server.json` points at a healthy live server (e.g.
//      the `kompany daemon` LaunchAgent), use it and spawn nothing —
//      exactly one engine process ever ticks. Otherwise pick a free
//      loopback port and spawn the bundled `kompany-server` with
//      `--port` and `--data-dir` (KOMPANY_DATA_DIR, default ~/.kompany),
//      then poll `/health` until 200 OK (or 30s). Remote-only installs
//      may ship without a sidecar; Local then shows as offline.
//   2. Connection targets (`desktop.rs`): a trusted top-bar shell plus
//      one native child webview per target — Local, saved remote
//      profiles (`target_registry.rs`), and the one-shot
//      `KOMPANY_REMOTE_URL`. Switching shows a warm webview; data never
//      mixes between targets.
//   3. Self-update (`updates.rs`): signed builds are fetched from the
//      update feed in the background and applied on "Restart to update".
//   4. On window close: kill the sidecar we spawned (never an attached
//      foreign server or a remote engine), exit the app.
//
// We intentionally keep all business logic in the Python side — the
// Rust shell is a thin process supervisor.

#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::net::TcpListener;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};

mod desktop;
mod target_registry;
mod updates;

/// Wrapper so we can stash the sidecar handle on Tauri's state manager
/// and kill it from the window-close event.
struct SidecarHandle(Mutex<Option<Child>>);

fn stop_child(mut child: Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn stop_spawned_sidecar(handle: &AppHandle) {
    if let Some(state) = handle.try_state::<SidecarHandle>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                stop_child(child);
            }
        }
    }
}

fn kompany_data_dir() -> std::path::PathBuf {
    std::env::var_os("KOMPANY_DATA_DIR")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".kompany")))
        .unwrap_or_else(|| std::path::PathBuf::from("./.kompany"))
}

fn pick_free_port() -> std::io::Result<u16> {
    // Bind to port 0 to let the kernel pick a free one, then drop the
    // listener immediately. There's a tiny TOCTOU window where another
    // process could grab the port before the sidecar starts; the
    // sidecar's bind failure surfaces as a health-check timeout so the
    // user sees a clear error rather than a silent hang.
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn sidecar_binary_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    // Tauri 2.x bundles `resources/...` into the app. The Python sidecar
    // is shipped as a PyInstaller --onedir directory, so we look for an
    // inner executable whose name matches its parent dir.
    let resource_dir = app.path().resource_dir().ok()?;
    let bin_root = resource_dir.join("binaries");

    // Direct hit (single-file form).
    for direct in [
        bin_root.join("kompany-server"),
        resource_dir.join("kompany-server"),
    ] {
        if direct.is_file() {
            return Some(direct);
        }
    }

    // PyInstaller --onedir form: `binaries/kompany-server-<triple>/kompany-server-<triple>`.
    if let Ok(entries) = std::fs::read_dir(&bin_root) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if name.starts_with("kompany-server") {
                    let inner = path.join(name);
                    if inner.is_file() {
                        return Some(inner);
                    }
                }
            }
        }
    }
    None
}

fn wait_for_health(base_url: &str, timeout: Duration) -> bool {
    let url = format!("{}/health", base_url.trim_end_matches('/'));
    let start = Instant::now();
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(500))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    while start.elapsed() < timeout {
        if let Ok(resp) = client.get(&url).send() {
            if resp.status().is_success() {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

/// Discover a healthy already-running Kompany server via the discovery
/// file `<data_dir>/server.json` (written by the Python side: Tauri
/// sidecar or `kompany daemon run`). The discovery file is the
/// single-server lock (PRD 06-12 D2) — same validation idea as the
/// Python reader (`interfaces/mcp_proxy._validate_sidecar`): parse
/// `{port, pid}`, then probe `/health` and require Kompany's exact
/// `{"status": "ok"}` shape so a recycled port serving some other local
/// app is never mistaken for our server. The health probe doubles as
/// the liveness check (a dead pid can't answer), which keeps this
/// portable without a libc dependency.
fn discover_running_server(data_dir: &std::path::Path) -> Option<(u16, i64)> {
    let raw = std::fs::read_to_string(data_dir.join("server.json")).ok()?;
    let info: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let port = info.get("port")?.as_u64()?;
    if !(1..65536).contains(&port) {
        return None;
    }
    let port = port as u16;
    let pid = info.get("pid")?.as_i64()?;
    if pid <= 0 {
        return None;
    }
    let base = format!("http://127.0.0.1:{}", port);
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(1))
        .build()
        .ok()?;
    let resp = client.get(format!("{}/health", base)).send().ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let body: serde_json::Value = serde_json::from_str(&resp.text().ok()?).ok()?;
    if body.get("status").and_then(|s| s.as_str()) != Some("ok") {
        return None;
    }
    Some((port, pid))
}

fn spawn_sidecar(
    binary_path: &std::path::Path,
    port: u16,
    data_dir: &std::path::Path,
) -> std::io::Result<Child> {
    Command::new(binary_path)
        .arg("--port")
        .arg(port.to_string())
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--data-dir")
        .arg(data_dir.as_os_str())
        .spawn()
}

enum ProbeResult {
    /// `/` returned 200 OK — the board SPA is present, load it directly.
    Board,
    /// `/` returned a redirect (3xx) — board bundle absent, load `/ui/`.
    Redirect,
    /// `/` did not respond or returned an error — fall back to `/` and
    /// let the WebView surface whatever error it can.
    Unreachable,
}

/// Probe `<base_url>/` to decide which path to load in the WebView.
///
/// We disable redirect-following so a 307 is distinguishable from a
/// real 200. The probe is best-effort: any failure maps to
/// `Unreachable` and the caller falls back to `/` (the WebView will
/// show its own error page, which is more informative than a white
/// screen from a redirect it didn't follow).
fn probe_root(base_url: &str) -> ProbeResult {
    let url = format!("{}/", base_url.trim_end_matches('/'));
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
    {
        Ok(c) => c,
        Err(_) => return ProbeResult::Unreachable,
    };
    let resp = match client.get(&url).send() {
        Ok(r) => r,
        Err(_) => return ProbeResult::Unreachable,
    };
    let status = resp.status().as_u16();
    if status == 200 {
        ProbeResult::Board
    } else if status >= 300 && status < 400 {
        ProbeResult::Redirect
    } else {
        ProbeResult::Unreachable
    }
}

/// Ask the engine which path the founder wants opened at launch
/// (`GET /start` → `{"path": "/#/talk", ...}`). `None` on any failure so the
/// caller can fall back to probing `/`. Only same-origin relative paths are
/// accepted — the engine must never be able to redirect the shell elsewhere.
fn fetch_start_path(base_url: &str, token: Option<&str>) -> Option<String> {
    let url = format!("{}/start", base_url.trim_end_matches('/'));
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()?;
    let mut req = client.get(&url);
    if let Some(t) = token {
        req = req.bearer_auth(t);
    }
    let resp = req.send().ok()?;
    if resp.status().as_u16() != 200 {
        return None;
    }
    let body: serde_json::Value = serde_json::from_str(&resp.text().ok()?).ok()?;
    let path = body.get("path")?.as_str()?.trim().to_string();
    if path.starts_with('/') && !path.starts_with("//") && !path.contains("://") {
        Some(path)
    } else {
        None
    }
}

/// Exchange token through POST before navigation. Token never enters URL,
/// redirect location, WebView history, or browser/network logs.
fn dashboard_login_cookie(base_url: &str, token: &str, path: &str) -> Result<String, String> {
    let url = format!("{}/dashboard/login", base_url.trim_end_matches('/'));
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("build dashboard login client: {error}"))?;
    let response = client
        .post(url)
        .form(&[("dashboard_token", token), ("next", path)])
        .send()
        .map_err(|error| format!("dashboard login request failed: {error}"))?;
    if !response.status().is_redirection() {
        return Err(format!(
            "dashboard login failed with status {}",
            response.status()
        ));
    }
    response
        .headers()
        .get(reqwest::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string)
        .ok_or_else(|| "dashboard login did not return a session cookie".to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(SidecarHandle(Mutex::new(None)))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(desktop::TargetRuntime::default())
        .manage(updates::UpdateState::default())
        .invoke_handler(tauri::generate_handler![
            desktop::list_targets,
            desktop::pick_target,
            desktop::save_target,
            desktop::remove_target,
            desktop::set_shell_expanded,
            updates::update_status,
            updates::restart_to_update,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // ---- Resolve data dir --------------------------------------
            // Match every other interface (CLI/REST/MCP/SDK): honor
            // KOMPANY_DATA_DIR, else default to ~/.kompany. A private
            // app_data_dir() here split the desktop into its own database,
            // so the MCP proxy never found server.json and headless work
            // was invisible in the app panel.
            let data_dir = std::env::var_os("KOMPANY_DATA_DIR")
                .map(std::path::PathBuf::from)
                .or_else(|| {
                    std::env::var_os("HOME")
                        .map(|h| std::path::PathBuf::from(h).join(".kompany"))
                })
                .ok_or("cannot resolve data dir: neither KOMPANY_DATA_DIR nor HOME is set")?;
            std::fs::create_dir_all(&data_dir).ok();

            // ---- Active target -----------------------------------------
            // KOMPANY_REMOTE_URL is a one-shot override: it appears as an
            // ephemeral "Environment remote" target and starts active, but
            // is never persisted. Otherwise the saved registry choice wins
            // (a legacy `remote_url` file is migrated into the registry).
            let (saved_active, saved_targets) = target_registry::load().unwrap_or_else(|_| {
                (target_registry::LOCAL_TARGET_ID.to_string(), Vec::new())
            });
            let env_remote = desktop::env_remote_url();
            let has_remote = env_remote.is_some() || !saved_targets.is_empty();
            let runtime = app.state::<desktop::TargetRuntime>();
            if let Ok(mut active) = runtime.active_id.lock() {
                *active = if env_remote.is_some() {
                    target_registry::ENV_TARGET_ID.to_string()
                } else {
                    saved_active
                };
            }

            // ---- Local engine ------------------------------------------
            // Attach to a healthy running server (06-12 D2: exactly one
            // engine process ever ticks), else spawn the bundled sidecar.
            // Remote-only installs may ship without a sidecar; Local then
            // shows as offline instead of blocking launch.
            let local_base = if let Some((port, pid)) = discover_running_server(&data_dir) {
                eprintln!(
                    "kompany: attaching to existing server on port {} (pid {}), not spawning a sidecar",
                    port, pid
                );
                Some(format!("http://127.0.0.1:{}", port))
            } else if let Some(binary) = sidecar_binary_path(&handle) {
                let mut port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
                let mut child = spawn_sidecar(&binary, port, &data_dir)
                    .map_err(|e| format!("spawn sidecar failed: {}", e))?;
                let mut base = format!("http://127.0.0.1:{}", port);
                if !wait_for_health(&base, Duration::from_secs(30)) {
                    // Retry once: maybe the port was grabbed between bind/drop.
                    stop_child(child);
                    port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
                    child = spawn_sidecar(&binary, port, &data_dir)
                        .map_err(|e| format!("spawn sidecar (retry) failed: {}", e))?;
                    base = format!("http://127.0.0.1:{}", port);
                    if !wait_for_health(&base, Duration::from_secs(30)) {
                        stop_child(child);
                        return Err("Kompany sidecar failed to become healthy within 30s".into());
                    }
                }
                if let Ok(mut guard) = app.state::<SidecarHandle>().0.lock() {
                    *guard = Some(child);
                }
                Some(base)
            } else if has_remote {
                None
            } else {
                return Err("kompany-server sidecar binary not found in resources".into());
            };

            match local_base {
                Some(base) => {
                    if let Ok(mut local) = runtime.local_base.lock() {
                        *local = Some(base);
                    }
                    desktop::set_status(&runtime, target_registry::LOCAL_TARGET_ID, "connected", None)?;
                }
                None => desktop::set_status(
                    &runtime,
                    target_registry::LOCAL_TARGET_ID,
                    "offline",
                    Some("Local engine is not bundled".to_string()),
                )?,
            }

            // The shell picks the active target once loaded, so an
            // unreachable remote never blocks launch.
            if let Err(error) = desktop::open_shell(&handle) {
                stop_spawned_sidecar(&handle);
                return Err(error.into());
            }
            updates::spawn_background_checks(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Belt-and-braces: if the app exits for any reason other
                // than the window close handler firing, still try to
                // reap the sidecar.
                stop_spawned_sidecar(app_handle);
            }
        });
}
