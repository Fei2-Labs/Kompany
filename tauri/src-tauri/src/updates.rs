//! Desktop self-update.
//!
//! A background thread checks the signed update feed (`plugins.updater` in
//! `tauri.conf.json`) shortly after launch and then every few hours. A newer
//! version is downloaded, its minisign signature verified against the
//! bundled public key, and installed in place; the shell bar then offers
//! "Restart to update". Nothing is applied to a running app without that
//! click, and an unsigned or tampered bundle is rejected by the plugin.
//!
//! Only macOS installs in the background: there `install` swaps the `.app`
//! bundle on disk and the running process is untouched until restart. On
//! Windows `install` launches the installer and exits the app, which must
//! never happen unprompted, and no feed entries are published for other
//! platforms yet.

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

const FIRST_CHECK_DELAY: Duration = Duration::from_secs(20);
const CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);

#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct UpdateStatus {
    /// Version of the running app.
    pub current: String,
    /// `idle` | `checking` | `downloading` | `ready` | `error`
    pub state: String,
    /// Version installed on disk and applied by the next restart.
    pub ready_version: Option<String>,
    pub error: Option<String>,
}

#[derive(Default)]
pub struct UpdateState(Mutex<UpdateStatus>);

impl UpdateState {
    fn set(&self, state: &str, ready_version: Option<String>, error: Option<String>) {
        if let Ok(mut status) = self.0.lock() {
            status.state = state.to_string();
            status.ready_version = ready_version;
            status.error = error;
        }
    }

    fn snapshot(&self) -> UpdateStatus {
        self.0
            .lock()
            .map(|status| status.clone())
            .unwrap_or_default()
    }
}

/// Check, and if a newer signed version exists, download and install it.
/// Returns the installed version.
async fn check_and_install(app: &AppHandle) -> Result<Option<String>, String> {
    let state = app.state::<UpdateState>();
    state.set("checking", None, None);
    let updater = app.updater().map_err(|error| error.to_string())?;
    let Some(update) = updater.check().await.map_err(|error| error.to_string())? else {
        state.set("idle", None, None);
        return Ok(None);
    };
    state.set("downloading", None, None);
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;
    Ok(Some(update.version))
}

/// Start the periodic background check. No-op outside macOS (see module doc).
pub fn spawn_background_checks(app: AppHandle) {
    if !cfg!(target_os = "macos") {
        return;
    }
    if let Ok(mut status) = app.state::<UpdateState>().0.lock() {
        status.current = app.package_info().version.to_string();
        status.state = "idle".to_string();
    }
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK_DELAY);
        loop {
            match tauri::async_runtime::block_on(check_and_install(&app)) {
                Ok(Some(version)) => {
                    // Staged on disk; stop checking until the user restarts.
                    app.state::<UpdateState>().set("ready", Some(version), None);
                    return;
                }
                Ok(None) => {}
                Err(error) => {
                    eprintln!("kompany: update check failed: {error}");
                    app.state::<UpdateState>().set("error", None, Some(error));
                }
            }
            std::thread::sleep(CHECK_INTERVAL);
        }
    });
}

#[tauri::command]
pub fn update_status(app: AppHandle) -> UpdateStatus {
    let mut status = app.state::<UpdateState>().snapshot();
    if status.current.is_empty() {
        status.current = app.package_info().version.to_string();
    }
    status
}

/// Relaunch into the installed update. Restarting from the main thread skips
/// `RunEvent::ExitRequested`, so the sidecar is stopped here explicitly.
#[tauri::command]
pub fn restart_to_update(app: AppHandle) -> Result<(), String> {
    if app.state::<UpdateState>().snapshot().state != "ready" {
        return Err("No update is ready to install".into());
    }
    crate::stop_spawned_sidecar(&app);
    app.restart()
}
