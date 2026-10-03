//! Desktop target switching.
//!
//! One native window holds a trusted shell webview (the top bar, bundled
//! `dist/index.html`) plus one native child webview per connection target
//! (Local engine, each remote profile). Only the active target is shown;
//! inactive targets stay loaded and hidden, so switching back is instant.
//!
//! Security boundary:
//!   - The shell is the only webview granted IPC (`capabilities/default.json`
//!     lists `webviews: ["shell"]` and no `remote` URLs).
//!   - Target webviews load engine origins. Tauri refuses custom commands
//!     from non-local origins unless a `remote` capability names them, so an
//!     engine page can never switch targets or reach stored tokens.
//!   - Each target gets its own data directory / data store, so cookies and
//!     storage never mix between targets. Each target is also a top-level
//!     browsing context, so the engine's `SameSite=Lax` session cookie works
//!     (it would be withheld inside a cross-site iframe).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::time::Duration;

use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, Url, Webview, WebviewBuilder, WebviewUrl,
    Window, WindowBuilder, WindowEvent,
};

use crate::target_registry::{self, TargetList};

pub const WINDOW_LABEL: &str = "main";
pub const SHELL_LABEL: &str = "shell";
const TARGET_LABEL_PREFIX: &str = "target-";
/// Height of the shell's top bar in logical pixels. Keep in sync with
/// `--bar-height` in `dist/index.html`.
pub const BAR_HEIGHT: f64 = 44.0;
const SHELL_STORE_IDENTIFIER: [u8; 16] = *b"kompany-shell-01";
/// A switch waits this long for `/health`; the user can retry or pick
/// another target, so it is shorter than the 30s startup budget.
const SWITCH_HEALTH_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Default)]
pub struct TargetRuntime {
    pub active_id: Mutex<String>,
    pub statuses: Mutex<HashMap<String, (String, Option<String>)>>,
    pub local_base: Mutex<Option<String>>,
    /// Target id -> base URL its webview was last authenticated against.
    /// A missing entry forces the next pick to re-check health and log in.
    views: Mutex<HashMap<String, String>>,
    switching: AtomicBool,
    shell_expanded: AtomicBool,
}

fn lock<T>(mutex: &Mutex<T>) -> Result<MutexGuard<'_, T>, String> {
    mutex
        .lock()
        .map_err(|_| "target state lock poisoned".to_string())
}

pub fn set_status(
    state: &TargetRuntime,
    id: &str,
    status: &str,
    error: Option<String>,
) -> Result<(), String> {
    lock(&state.statuses)?.insert(id.to_string(), (status.to_string(), error));
    Ok(())
}

pub fn env_remote_url() -> Option<String> {
    std::env::var_os("KOMPANY_REMOTE_URL")
        .and_then(|value| target_registry::normalize_target_url(&value.to_string_lossy()).ok())
}

fn view_label(target_id: &str) -> String {
    format!("{TARGET_LABEL_PREFIX}{target_id}")
}

/// Stable 16-byte data-store identifier for a target id: two FNV-1a 64-bit
/// hashes with distinct offset bases. Deterministic across builds and
/// toolchains, so a target keeps its macOS data store (cookies, local
/// storage) after app updates.
pub fn store_identifier(target_id: &str) -> [u8; 16] {
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut halves = [0xcbf2_9ce4_8422_2325_u64, 0x6c62_272e_07bb_0142_u64];
    for half in halves.iter_mut() {
        for byte in target_id.bytes() {
            *half ^= u64::from(byte);
            *half = half.wrapping_mul(PRIME);
        }
    }
    let mut identifier = [0u8; 16];
    identifier[..8].copy_from_slice(&halves[0].to_be_bytes());
    identifier[8..].copy_from_slice(&halves[1].to_be_bytes());
    identifier
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Area {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Shell and target areas for a window of `width` x `height` logical pixels.
/// The expanded shell covers the whole window (profile manager open); target
/// webviews are hidden then, so the content area is only used when collapsed.
pub fn layout(width: f64, height: f64, shell_expanded: bool) -> (Area, Area) {
    let width = width.max(0.0);
    let height = height.max(0.0);
    let bar = BAR_HEIGHT.min(height);
    let shell_height = if shell_expanded { height } else { bar };
    (
        Area {
            x: 0.0,
            y: 0.0,
            width,
            height: shell_height,
        },
        Area {
            x: 0.0,
            y: bar,
            width,
            height: height - bar,
        },
    )
}

fn window_layout(window: &Window, shell_expanded: bool) -> (Area, Area) {
    let logical = match (window.inner_size(), window.scale_factor()) {
        (Ok(size), Ok(scale)) => size.to_logical::<f64>(scale),
        _ => LogicalSize::new(1200.0, 800.0),
    };
    layout(logical.width, logical.height, shell_expanded)
}

/// Position the shell and every target webview; show only the active target.
pub fn apply_layout(app: &AppHandle) {
    let Some(window) = app.get_window(WINDOW_LABEL) else {
        return;
    };
    let state = app.state::<TargetRuntime>();
    let expanded = state.shell_expanded.load(Ordering::SeqCst);
    let active = lock(&state.active_id)
        .map(|guard| guard.clone())
        .unwrap_or_default();
    let (shell_area, content) = window_layout(&window, expanded);
    if let Some(shell) = app.get_webview(SHELL_LABEL) {
        let _ = shell.set_position(LogicalPosition::new(shell_area.x, shell_area.y));
        let _ = shell.set_size(LogicalSize::new(shell_area.width, shell_area.height));
    }
    for (label, view) in app.webviews() {
        let Some(id) = label.strip_prefix(TARGET_LABEL_PREFIX) else {
            continue;
        };
        let _ = view.set_position(LogicalPosition::new(content.x, content.y));
        let _ = view.set_size(LogicalSize::new(content.width, content.height));
        if !expanded && id == active {
            let _ = view.show();
        } else {
            let _ = view.hide();
        }
    }
}

/// Create the main window with the shell bar. The shell picks the active
/// target itself once loaded, so an unreachable remote never blocks launch.
pub fn open_shell(app: &AppHandle) -> Result<(), String> {
    let title = format!("Kompany · tauri@{}", env!("KOMPANY_TAURI_COMMIT"));
    let window = WindowBuilder::new(app, WINDOW_LABEL)
        .title(title)
        .inner_size(1200.0, 800.0)
        .min_inner_size(900.0, 600.0)
        .resizable(true)
        .build()
        .map_err(|error| format!("window build failed: {error}"))?;
    let shell = WebviewBuilder::new(SHELL_LABEL, WebviewUrl::App("index.html".into()))
        .data_directory(
            crate::kompany_data_dir()
                .join("desktop-webview")
                .join(SHELL_LABEL),
        )
        .data_store_identifier(SHELL_STORE_IDENTIFIER);
    let (bar, _) = window_layout(&window, false);
    window
        .add_child(
            shell,
            LogicalPosition::new(bar.x, bar.y),
            LogicalSize::new(bar.width, bar.height),
        )
        .map_err(|error| format!("shell webview build failed: {error}"))?;

    let handle = app.clone();
    window.on_window_event(move |event| match event {
        WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => apply_layout(&handle),
        // Kill the sidecar we spawned (never an attached daemon), then exit.
        WindowEvent::CloseRequested { .. } => {
            crate::stop_spawned_sidecar(&handle);
            handle.exit(0);
        }
        _ => {}
    });
    Ok(())
}

pub fn target_list(state: &TargetRuntime) -> Result<TargetList, String> {
    let active = lock(&state.active_id)?.clone();
    let statuses = lock(&state.statuses)?.clone();
    let env_url = env_remote_url();
    let mut list = target_registry::list(&active, &statuses, env_url.as_deref())?;
    if let Some(local_base) = lock(&state.local_base)?.clone() {
        if let Some(local) = list
            .targets
            .iter_mut()
            .find(|target| target.id == target_registry::LOCAL_TARGET_ID)
        {
            local.url = local_base;
        }
    }
    Ok(list)
}

/// Base URL and stored token for a target id. The env target never uses a
/// stored token: it is ephemeral and may point anywhere.
fn resolve(state: &TargetRuntime, id: &str) -> Result<(String, Option<String>), String> {
    match id {
        target_registry::LOCAL_TARGET_ID => {
            let base = lock(&state.local_base)?
                .clone()
                .ok_or_else(|| "Local engine is not running".to_string())?;
            Ok((base, None))
        }
        target_registry::ENV_TARGET_ID => {
            let base = env_remote_url()
                .ok_or_else(|| "Environment remote target is unavailable".to_string())?;
            Ok((base, None))
        }
        _ => {
            let target = target_registry::target(id)?;
            Ok((target.url, target_registry::read_token(id)))
        }
    }
}

/// Health check, start path, and token-for-cookie exchange. Blocking HTTP;
/// run off the async runtime. The token travels only in this POST body.
fn prepare(base: &str, token: Option<&str>) -> Result<(String, Option<String>), String> {
    if !crate::wait_for_health(base, SWITCH_HEALTH_TIMEOUT) {
        return Err(format!(
            "{base} did not answer /health within {}s",
            SWITCH_HEALTH_TIMEOUT.as_secs()
        ));
    }
    let path =
        crate::fetch_start_path(base, token).unwrap_or_else(|| match crate::probe_root(base) {
            crate::ProbeResult::Redirect => "/ui/".to_string(),
            crate::ProbeResult::Board | crate::ProbeResult::Unreachable => "/".to_string(),
        });
    let cookie = match token {
        Some(value) => Some(crate::dashboard_login_cookie(base, value, &path)?),
        None => None,
    };
    Ok((format!("{}{}", base.trim_end_matches('/'), path), cookie))
}

fn set_dashboard_cookie(view: &Webview, base_url: &str, cookie_header: &str) -> Result<(), String> {
    let mut cookie = tauri::webview::cookie::Cookie::parse(cookie_header)
        .map_err(|error| format!("parse dashboard session cookie: {error}"))?
        .into_owned();
    let origin = Url::parse(base_url).map_err(|_| "target URL is invalid".to_string())?;
    let host = origin
        .host_str()
        .ok_or_else(|| "target URL has no host".to_string())?;
    cookie.set_domain(host.to_string());
    cookie.set_path("/");
    view.set_cookie(cookie)
        .map_err(|error| format!("set dashboard session cookie: {error}"))
}

fn create_view(app: &AppHandle, id: &str) -> Result<Webview, String> {
    let window = app
        .get_window(WINDOW_LABEL)
        .ok_or_else(|| "main window is not available".to_string())?;
    let blank: Url = "about:blank"
        .parse()
        .map_err(|error| format!("blank URL: {error}"))?;
    // Target ids are validated `[a-z0-9-]` (or fixed constants), so they are
    // safe path segments. `targets/` keeps them apart from the shell store.
    let builder = WebviewBuilder::new(view_label(id), WebviewUrl::External(blank))
        .data_directory(
            crate::kompany_data_dir()
                .join("desktop-webview")
                .join("targets")
                .join(id),
        )
        .data_store_identifier(store_identifier(id));
    let (_, content) = window_layout(&window, false);
    window
        .add_child(
            builder,
            LogicalPosition::new(content.x, content.y),
            LogicalSize::new(content.width, content.height),
        )
        .map_err(|error| format!("target webview build failed: {error}"))
}

fn load_view(
    app: &AppHandle,
    id: &str,
    base: &str,
    url: &str,
    cookie: Option<&str>,
) -> Result<(), String> {
    let view = match app.get_webview(&view_label(id)) {
        Some(view) => view,
        None => create_view(app, id)?,
    };
    if let Some(header) = cookie {
        set_dashboard_cookie(&view, base, header)?;
    }
    let parsed: Url = url
        .parse()
        .map_err(|error| format!("invalid target URL: {error}"))?;
    view.navigate(parsed)
        .map_err(|error| format!("navigate target: {error}"))
}

/// A webview already loaded for this target at the same base URL.
fn is_warm(app: &AppHandle, state: &TargetRuntime, id: &str, base: &str) -> Result<bool, String> {
    let loaded = lock(&state.views)?.get(id).map(String::as_str) == Some(base);
    Ok(loaded && app.get_webview(&view_label(id)).is_some())
}

/// Single-flight switching without holding a lock across `.await`.
struct SwitchGuard<'a>(&'a AtomicBool);

impl<'a> SwitchGuard<'a> {
    fn acquire(flag: &'a AtomicBool) -> Result<Self, String> {
        flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map_err(|_| "Another target switch is in progress".to_string())?;
        Ok(Self(flag))
    }
}

impl Drop for SwitchGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

#[tauri::command]
pub fn list_targets(app: AppHandle) -> Result<TargetList, String> {
    target_list(&app.state::<TargetRuntime>())
}

/// Show `id`. A warm target switches instantly; otherwise (or with
/// `reload`) the shell checks health, logs in, and (re)loads its webview.
#[tauri::command]
pub async fn pick_target(
    id: String,
    reload: Option<bool>,
    app: AppHandle,
) -> Result<TargetList, String> {
    let state = app.state::<TargetRuntime>();
    let _guard = SwitchGuard::acquire(&state.switching)?;
    let (base, token) = resolve(&state, &id)?;

    if reload.unwrap_or(false) || !is_warm(&app, &state, &id, &base)? {
        set_status(&state, &id, "connecting", None)?;
        let prepare_base = base.clone();
        let prepared =
            tauri::async_runtime::spawn_blocking(move || prepare(&prepare_base, token.as_deref()))
                .await
                .map_err(|error| format!("target preparation failed: {error}"))
                .and_then(|result| result)
                .and_then(|(url, cookie)| load_view(&app, &id, &base, &url, cookie.as_deref()));
        if let Err(error) = prepared {
            set_status(&state, &id, "error", Some(error.clone()))?;
            return Err(error);
        }
        lock(&state.views)?.insert(id.clone(), base);
        set_status(&state, &id, "connected", None)?;
    }

    // The env target is ephemeral: never persist it as the saved choice.
    if id != target_registry::ENV_TARGET_ID {
        target_registry::set_active(&id)?;
    }
    *lock(&state.active_id)? = id;
    apply_layout(&app);
    target_list(&state)
}

/// Add or edit a remote profile. The token is written to its 0600 file and
/// never echoed back. The next pick re-authenticates this target.
#[tauri::command]
pub fn save_target(
    id: Option<String>,
    name: String,
    url: String,
    token: Option<String>,
    app: AppHandle,
) -> Result<TargetList, String> {
    let target = target_registry::save_target(id.as_deref(), &name, &url)?;
    if let Some(value) = token {
        target_registry::save_token(&target.id, &value)?;
    }
    let state = app.state::<TargetRuntime>();
    lock(&state.views)?.remove(&target.id);
    target_list(&state)
}

#[tauri::command]
pub fn remove_target(id: String, app: AppHandle) -> Result<TargetList, String> {
    let state = app.state::<TargetRuntime>();
    if *lock(&state.active_id)? == id {
        return Err("Switch to another target before removing the active target".into());
    }
    target_registry::remove_target(&id)?;
    lock(&state.views)?.remove(&id);
    lock(&state.statuses)?.remove(&id);
    if let Some(view) = app.get_webview(&view_label(&id)) {
        let _ = view.close();
    }
    target_list(&state)
}

/// Expand the shell over the whole window (profile manager) or collapse it
/// back to the bar. Target webviews are hidden while expanded.
#[tauri::command]
pub fn set_shell_expanded(expanded: bool, app: AppHandle) {
    app.state::<TargetRuntime>()
        .shell_expanded
        .store(expanded, Ordering::SeqCst);
    apply_layout(&app);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_reserves_bar_and_fills_rest() {
        let (shell, content) = layout(1200.0, 800.0, false);
        assert_eq!(
            shell,
            Area {
                x: 0.0,
                y: 0.0,
                width: 1200.0,
                height: BAR_HEIGHT
            }
        );
        assert_eq!(
            content,
            Area {
                x: 0.0,
                y: BAR_HEIGHT,
                width: 1200.0,
                height: 800.0 - BAR_HEIGHT
            }
        );
    }

    #[test]
    fn expanded_shell_covers_window() {
        let (shell, _) = layout(1200.0, 800.0, true);
        assert_eq!(shell.height, 800.0);
    }

    #[test]
    fn tiny_window_never_goes_negative() {
        let (shell, content) = layout(10.0, 20.0, false);
        assert_eq!(shell.height, 20.0);
        assert_eq!(content.height, 0.0);
    }

    #[test]
    fn store_identifiers_are_stable_and_distinct() {
        assert_eq!(store_identifier("local"), store_identifier("local"));
        assert_ne!(store_identifier("local"), store_identifier("env-remote"));
        assert_ne!(store_identifier("remote-a"), store_identifier("remote-b"));
        assert_ne!(store_identifier("local"), SHELL_STORE_IDENTIFIER);
        // Pinned: changing the hash would orphan every saved login.
        assert_eq!(
            store_identifier(""),
            [
                0xcb, 0xf2, 0x9c, 0xe4, 0x84, 0x22, 0x23, 0x25, 0x6c, 0x62, 0x27, 0x2e, 0x07, 0xbb,
                0x01, 0x42
            ]
        );
    }
}
