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

use std::sync::atomic::{AtomicBool, Ordering};
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
    /// `idle` | `checking` | `downloading` | `verifying` | `installing` | `ready` | `error`
    pub state: String,
    /// Version installed on disk and applied by the next restart.
    pub ready_version: Option<String>,
    pub downloaded_bytes: u64,
    pub total_bytes: Option<u64>,
    pub error: Option<String>,
}

#[derive(Default)]
pub struct UpdateState(Mutex<UpdateStatus>, AtomicBool);

impl UpdateState {
    fn set(&self, state: &str, ready_version: Option<String>, error: Option<String>) {
        if let Ok(mut status) = self.0.lock() {
            status.state = state.to_string();
            status.ready_version = ready_version;
            status.error = error;
            if state == "checking" {
                status.downloaded_bytes = 0;
                status.total_bytes = None;
            }
        }
    }

    fn downloaded(&self, chunk: usize, total: Option<u64>) {
        if let Ok(mut status) = self.0.lock() {
            status.downloaded_bytes = status.downloaded_bytes.saturating_add(chunk as u64);
            status.total_bytes = total.filter(|size| *size > 0);
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
    let bytes = update
        .download(
            |chunk, total| state.downloaded(chunk, total),
            || state.set("verifying", None, None),
        )
        .await
        .map_err(|error| error.to_string())?;
    state.set("installing", None, None);
    update.install(bytes).map_err(|error| error.to_string())?;
    Ok(Some(update.version))
}

// Manual and periodic checks share the same gate; a downloaded update must
// never be overwritten by another check before the founder restarts.
async fn run_check(app: &AppHandle) {
    let state = app.state::<UpdateState>();
    if state
        .1
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }
    if state.snapshot().state != "ready" {
        match check_and_install(app).await {
            Ok(Some(version)) => state.set("ready", Some(version), None),
            Ok(None) => {}
            Err(error) => {
                eprintln!("kompany: update check failed: {error}");
                state.set("error", None, Some(error));
            }
        }
    }
    state.1.store(false, Ordering::Release);
}

#[tauri::command]
pub async fn check_desktop_update(app: AppHandle) -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Err("Desktop updates are currently available on macOS only".into());
    }
    tauri::async_runtime::spawn(async move { run_check(&app).await });
    Ok(())
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
            tauri::async_runtime::block_on(run_check(&app));
            if app.state::<UpdateState>().snapshot().state == "ready" {
                return;
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_accumulates_chunks_and_resets_for_a_new_check() {
        let state = UpdateState::default();
        state.set("downloading", None, None);
        state.downloaded(25, Some(100));
        state.downloaded(30, Some(100));
        assert_eq!(state.snapshot().downloaded_bytes, 55);
        assert_eq!(state.snapshot().total_bytes, Some(100));
        state.set("verifying", None, None);
        assert_eq!(state.snapshot().downloaded_bytes, 55);
        state.set("checking", None, None);
        assert_eq!(state.snapshot().downloaded_bytes, 0);
        assert_eq!(state.snapshot().total_bytes, None);
    }

    #[test]
    fn unknown_or_zero_length_stays_indeterminate() {
        let state = UpdateState::default();
        state.downloaded(5, None);
        assert_eq!(state.snapshot().total_bytes, None);
        state.downloaded(10, Some(0));
        assert_eq!(state.snapshot().total_bytes, None);
        assert_eq!(state.snapshot().downloaded_bytes, 15);
    }

    #[test]
    fn update_gate_rejects_overlapping_checks() {
        let state = UpdateState::default();
        assert!(state
            .1
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok());
        assert!(state
            .1
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err());
        state.1.store(false, Ordering::Release);
        assert!(state
            .1
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok());
    }
}
