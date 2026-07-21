// Kompany desktop shell.
//
// Responsibilities:
//   0. Remote mode (07-20 VPS deploy): if `KOMPANY_REMOTE_URL` is set,
//      probe `<url>/health` and load the WebView at `<url>/` — no
//      discovery, no sidecar. The desktop app becomes a pure client of
//      a remote engine (e.g. on a tailnet VPS). SidecarHandle stays
//      empty so app exit never touches the remote server.
//   1. Attach-if-running (06-12-daemon-tick-loop D2): if
//      `<data_dir>/server.json` points at a healthy live server (e.g.
//      the `kompany daemon` LaunchAgent), point the WebView at it and
//      spawn nothing — exactly one engine process ever ticks, and the
//      panel sees daemon-driven activity live.
//   2. Otherwise pick a free loopback port for the Python sidecar and
//      spawn the bundled `kompany-server` binary with `--port` and
//      `--data-dir` from KOMPANY_DATA_DIR, defaulting to ~/.kompany
//      (same resolution as every other Kompany interface).
//   3. Poll `http://127.0.0.1:<port>/health` until 200 OK (or 30s).
//   4. Load the WebView at `http://127.0.0.1:<port>/` — the new board
//      (falls back to `/ui/` when the board bundle is unbuilt).
//   5. On window close: kill the sidecar we spawned (never an attached
//      foreign server), exit the app.
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

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// Wrapper so we can stash the sidecar handle on Tauri's state manager
/// and kill it from the window-close event.
struct SidecarHandle(Mutex<Option<Child>>);

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
    let resp = client
        .get(format!("{}/health", base))
        .send()
        .ok()?;
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

/// Open the main WebView window against a healthy server at `base_url`.
///
/// `base_url` is the origin (scheme + host + port, no path) of either:
///   - a local sidecar (`http://127.0.0.1:<port>`)
///   - an attached local daemon (`http://127.0.0.1:<port>` from server.json)
///   - a remote engine (`http://dokploy-kosonen-server.tail298133.ts.net:55352`
///     from `KOMPANY_REMOTE_URL`)
///
/// The close handler only kills what we stashed in `SidecarHandle` —
/// in attach/remote mode that state is empty, so an attached foreign
/// server (e.g. the launchd daemon, or a remote VPS) is never touched
/// on app exit.
fn open_main_window(handle: &AppHandle, base_url: &str) -> Result<(), String> {
    // Open the operations board at the site root. FastAPI serves the React
    // board at `/` and gracefully redirects to `/ui/` (cyberpunk terminal)
    // when the board hasn't been built yet, so this is safe pre-build.
    //
    // EXCEPTION: when the board bundle is absent (e.g. a VPS deployed from
    // source without `npm run build`), `/` returns a 307 to `/ui/`. Tauri's
    // WebView does not reliably follow that redirect on initial load
    // (observed white screen), so we probe `/` first and fall back to
    // `/ui/` if it doesn't return 200 OK. This keeps the local sidecar
    // path (board present) on the fast path and fixes the remote VPS path.
    let base = base_url.trim_end_matches('/');
    let path = match probe_root(base) {
        ProbeResult::Board => "/",
        ProbeResult::Redirect => "/ui/",
        ProbeResult::Unreachable => "/",
    };
    let url = format!("{}{}", base, path);
    let webview_url =
        WebviewUrl::External(url.parse().map_err(|e| format!("invalid url: {}", e))?);
    let window = WebviewWindowBuilder::new(handle, "main", webview_url)
        .title("Kompany")
        .inner_size(1200.0, 800.0)
        .min_inner_size(900.0, 600.0)
        .resizable(true)
        .visible(true)
        .build()
        .map_err(|e| format!("window build failed: {}", e))?;

    // CloseRequested → kill the sidecar we spawned (if any) then exit.
    let close_handle = handle.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { .. } = event {
            if let Some(state) = close_handle.try_state::<SidecarHandle>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
            close_handle.exit(0);
        }
    });
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(SidecarHandle(Mutex::new(None)))
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

            // ---- Remote mode (07-20 VPS deploy) ------------------------
            // If KOMPANY_REMOTE_URL is set, the desktop app becomes a
            // pure client of a remote engine (typically on a tailnet
            // VPS). No discovery, no sidecar — just probe /health and
            // load the URL. SidecarHandle stays empty so app exit never
            // touches the remote server. The remote engine must already
            // be running (e.g. as a systemd service on the VPS).
            if let Some(remote_url) = std::env::var_os("KOMPANY_REMOTE_URL") {
                let base = remote_url
                    .to_str()
                    .ok_or("KOMPANY_REMOTE_URL is not valid UTF-8")?
                    .trim()
                    .trim_end_matches('/')
                    .to_string();
                if base.is_empty() {
                    return Err("KOMPANY_REMOTE_URL is empty".into());
                }
                eprintln!(
                    "kompany: remote mode — probing {} (KOMPANY_REMOTE_URL set, skipping sidecar)",
                    base
                );
                if !wait_for_health(&base, Duration::from_secs(30)) {
                    return Err(format!(
                        "Kompany remote server at {} did not become healthy within 30s",
                        base
                    )
                    .into());
                }
                open_main_window(&handle, &base)?;
                return Ok(());
            }

            // ---- Attach to an existing healthy server (06-12 D2) -------
            // One server process ever: if the discovery file points at a
            // live Kompany server (typically the `kompany daemon`
            // LaunchAgent), use it instead of spawning a second engine.
            // SidecarHandle stays empty, so app exit leaves it running.
            if let Some((port, pid)) = discover_running_server(&data_dir) {
                let base = format!("http://127.0.0.1:{}", port);
                eprintln!(
                    "kompany: attaching to existing server on port {} (pid {}), not spawning a sidecar",
                    port, pid
                );
                open_main_window(&handle, &base)?;
                return Ok(());
            }

            // ---- Spawn path (unchanged): binary + port + health --------
            let binary = sidecar_binary_path(&handle)
                .ok_or("kompany-server sidecar binary not found in resources")?;

            let mut port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
            let mut child = match spawn_sidecar(&binary, port, &data_dir) {
                Ok(c) => c,
                Err(e) => return Err(format!("spawn sidecar failed: {}", e).into()),
            };

            // ---- Health check (with one retry on port collision) ------
            let base = format!("http://127.0.0.1:{}", port);
            if !wait_for_health(&base, Duration::from_secs(30)) {
                let _ = child.kill();
                // Retry once: maybe the port was grabbed between bind/drop.
                port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
                child = spawn_sidecar(&binary, port, &data_dir)
                    .map_err(|e| format!("spawn sidecar (retry) failed: {}", e))?;
                let retry_base = format!("http://127.0.0.1:{}", port);
                if !wait_for_health(&retry_base, Duration::from_secs(30)) {
                    let _ = child.kill();
                    return Err("Kompany sidecar failed to become healthy within 30s".into());
                }
            }

            // Stash the child handle so we can kill it on close.
            if let Some(state) = app.try_state::<SidecarHandle>() {
                *state.0.lock().unwrap() = Some(child);
            }

            let final_base = format!("http://127.0.0.1:{}", port);
            open_main_window(&handle, &final_base)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Belt-and-braces: if the app exits for any reason other
                // than the window close handler firing, still try to
                // reap the sidecar.
                if let Some(state) = app_handle.try_state::<SidecarHandle>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}
