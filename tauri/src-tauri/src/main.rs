// Kompany desktop shell.
//
// Responsibilities:
//   1. Pick a free loopback port for the Python sidecar.
//   2. Spawn the bundled `kompany-server` binary with `--port` and
//      `--data-dir` pointing at the platform's app-data directory.
//   3. Poll `http://127.0.0.1:<port>/health` until 200 OK (or 30s).
//   4. Load the WebView at `http://127.0.0.1:<port>/ui/` — the SPA
//      handles the onboarding redirect itself.
//   5. On window close: kill the sidecar, exit the app.
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
    // Tauri 2.x resolves bundled sidecar binaries via the resource
    // directory; in dev mode we fall back to the repo-local path.
    let resource_dir = app.path().resource_dir().ok()?;
    let candidates = [
        resource_dir.join("binaries").join("kompany-server"),
        resource_dir.join("kompany-server"),
    ];
    for candidate in &candidates {
        if candidate.exists() {
            return Some(candidate.clone());
        }
    }
    None
}

fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{}/health", port);
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

fn main() {
    tauri::Builder::default()
        .manage(SidecarHandle(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            // ---- Resolve data dir + sidecar binary --------------------
            let data_dir = handle
                .path()
                .app_data_dir()
                .map_err(|e| format!("app_data_dir failed: {}", e))?;
            std::fs::create_dir_all(&data_dir).ok();

            let binary = sidecar_binary_path(&handle)
                .ok_or("kompany-server sidecar binary not found in resources")?;

            // ---- Pick port + spawn ------------------------------------
            let mut port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
            let mut child = match spawn_sidecar(&binary, port, &data_dir) {
                Ok(c) => c,
                Err(e) => return Err(format!("spawn sidecar failed: {}", e).into()),
            };

            // ---- Health check (with one retry on port collision) ------
            if !wait_for_health(port, Duration::from_secs(30)) {
                let _ = child.kill();
                // Retry once: maybe the port was grabbed between bind/drop.
                port = pick_free_port().map_err(|e| format!("port pick failed: {}", e))?;
                child = spawn_sidecar(&binary, port, &data_dir)
                    .map_err(|e| format!("spawn sidecar (retry) failed: {}", e))?;
                if !wait_for_health(port, Duration::from_secs(30)) {
                    let _ = child.kill();
                    return Err("Kompany sidecar failed to become healthy within 30s".into());
                }
            }

            // Stash the child handle so we can kill it on close.
            if let Some(state) = app.try_state::<SidecarHandle>() {
                *state.0.lock().unwrap() = Some(child);
            }

            // ---- Open the WebView --------------------------------------
            let url = format!("http://127.0.0.1:{}/ui/", port);
            let webview_url = WebviewUrl::External(
                url.parse().map_err(|e| format!("invalid url: {}", e))?,
            );
            let window = WebviewWindowBuilder::new(&handle, "main", webview_url)
                .title("Kompany")
                .inner_size(1200.0, 800.0)
                .min_inner_size(900.0, 600.0)
                .resizable(true)
                .visible(true)
                .build()
                .map_err(|e| format!("window build failed: {}", e))?;

            // CloseRequested → kill sidecar then exit.
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
