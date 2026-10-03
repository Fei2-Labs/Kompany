use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const LOCAL_TARGET_ID: &str = "local";
pub const LEGACY_TARGET_ID: &str = "legacy-remote";
pub const ENV_TARGET_ID: &str = "env-remote";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct TargetRecord {
    pub id: String,
    pub name: String,
    pub url: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct StoredRegistry {
    active_id: String,
    targets: Vec<TargetRecord>,
}

#[derive(Clone, Debug, Serialize)]
pub struct TargetView {
    pub id: String,
    pub name: String,
    pub url: String,
    pub kind: String,
    pub status: String,
    pub error: Option<String>,
    pub has_token: bool,
    pub from_env: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct TargetList {
    pub active_id: String,
    pub targets: Vec<TargetView>,
}

fn registry_path() -> PathBuf {
    if let Some(path) = std::env::var_os("KOMPANY_DESKTOP_TARGETS_FILE") {
        return PathBuf::from(path);
    }
    std::env::var_os("HOME")
        .map(|home| PathBuf::from(home).join(".kompany-desktop-targets.json"))
        .unwrap_or_else(|| PathBuf::from(".kompany-desktop-targets.json"))
}

fn data_dir() -> PathBuf {
    std::env::var_os("KOMPANY_DATA_DIR")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".kompany")))
        .unwrap_or_else(|| PathBuf::from(".kompany"))
}

fn legacy_url_path() -> PathBuf {
    data_dir().join("remote_url")
}

fn legacy_token_path() -> PathBuf {
    data_dir().join("remote_token")
}

fn secret_dir() -> PathBuf {
    data_dir().join("desktop-target-secrets")
}

fn secret_path(id: &str) -> PathBuf {
    secret_dir().join(format!("{id}.token"))
}

pub fn normalize_target_url(url: &str) -> Result<String, String> {
    let value = url.trim().trim_end_matches('/');
    if value.is_empty() {
        return Err("URL is required".into());
    }
    let parsed = reqwest::Url::parse(value).map_err(|_| "URL must be a valid HTTP(S) URL")?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("URL must be a valid HTTP(S) URL".into());
    }
    if parsed.username() != "" || parsed.password().is_some() {
        return Err("URL must not contain credentials".into());
    }
    Ok(value.to_string())
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        && id.as_bytes()[0] != b'-'
        && id.as_bytes()[id.len() - 1] != b'-'
}

pub fn validate_name(name: &str) -> Result<String, String> {
    let value = name.trim();
    if value.is_empty() {
        return Err("Name is required".into());
    }
    if value.chars().count() > 80 {
        return Err("Name must be 80 characters or fewer".into());
    }
    Ok(value.to_string())
}

pub fn validate_id(id: &str) -> Result<(), String> {
    if valid_id(id) {
        Ok(())
    } else {
        Err("ID must contain lowercase letters, numbers, and hyphens".into())
    }
}

pub fn validate_target(id: &str, name: &str, url: &str) -> Result<TargetRecord, String> {
    validate_id(id)?;
    if id == LOCAL_TARGET_ID {
        return Err("The local target cannot be edited".into());
    }
    if id == ENV_TARGET_ID {
        return Err("The environment target is managed by KOMPANY_REMOTE_URL".into());
    }
    Ok(TargetRecord {
        id: id.to_string(),
        name: validate_name(name)?,
        url: normalize_target_url(url)?,
    })
}

fn slug(name: &str) -> String {
    let mut result = String::from("remote-");
    let mut pending_separator = false;
    for byte in name.bytes() {
        if byte.is_ascii_alphanumeric() {
            if pending_separator && result.len() > 7 && !result.ends_with('-') {
                result.push('-');
            }
            result.push(byte.to_ascii_lowercase() as char);
            pending_separator = false;
        } else if result.len() > 7 {
            pending_separator = true;
        }
        if result.len() >= 48 {
            break;
        }
    }
    while result.ends_with('-') {
        result.pop();
    }
    if result == "remote" {
        result.push_str("target");
    }
    result
}

fn target_origin(url: &str) -> Result<String, String> {
    let parsed = reqwest::Url::parse(url).map_err(|_| "URL must be a valid HTTP(S) URL")?;
    let port = parsed
        .port_or_known_default()
        .ok_or_else(|| "URL must include a known HTTP(S) port".to_string())?;
    Ok(format!(
        "{}://{}:{port}",
        parsed.scheme(),
        parsed.host_str().unwrap_or_default()
    ))
}

fn unique_id(registry: &StoredRegistry, name: &str) -> String {
    let base = slug(name);
    if !registry.targets.iter().any(|target| target.id == base) {
        return base;
    }
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    let candidate = format!("{base}-{}", millis % 1_000_000_000);
    if !registry.targets.iter().any(|target| target.id == candidate) {
        candidate
    } else {
        format!("{base}-1")
    }
}

fn atomic_write(path: &Path, contents: &str, mode: u32) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "target path has no parent".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("create target directory: {error}"))?;
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, contents).map_err(|error| format!("write target file: {error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(mode))
            .map_err(|error| format!("secure target file: {error}"))?;
    }
    fs::rename(&temporary, path).map_err(|error| format!("replace target file: {error}"))?;
    Ok(())
}

fn remove_optional_file(path: &Path) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("remove target secret: {error}")),
    }
}

fn token_path_for(id: &str) -> PathBuf {
    if id == LEGACY_TARGET_ID {
        legacy_token_path()
    } else {
        secret_path(id)
    }
}

fn persist(registry: &StoredRegistry) -> Result<(), String> {
    let json = serde_json::to_string_pretty(registry)
        .map_err(|error| format!("serialize target registry: {error}"))?;
    atomic_write(&registry_path(), &json, 0o600)
}

fn read_legacy_url() -> Option<String> {
    fs::read_to_string(legacy_url_path())
        .ok()
        .and_then(|value| normalize_target_url(&value).ok())
}

fn load_stored() -> Result<StoredRegistry, String> {
    let path = registry_path();
    let mut migrated = false;
    let mut migrate_legacy = false;
    let mut registry = match fs::read_to_string(&path) {
        Ok(contents) => match serde_json::from_str::<StoredRegistry>(&contents) {
            Ok(registry) => registry,
            Err(_) => {
                migrated = true;
                migrate_legacy = true;
                StoredRegistry {
                    active_id: LOCAL_TARGET_ID.to_string(),
                    targets: Vec::new(),
                }
            }
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            migrated = true;
            migrate_legacy = true;
            StoredRegistry {
                active_id: LOCAL_TARGET_ID.to_string(),
                targets: Vec::new(),
            }
        }
        Err(error) => return Err(format!("read target registry: {error}")),
    };

    if migrate_legacy {
        if let Some(url) = read_legacy_url() {
            registry.targets.push(TargetRecord {
                id: LEGACY_TARGET_ID.to_string(),
                name: "Remote".to_string(),
                url,
            });
            registry.active_id = LEGACY_TARGET_ID.to_string();
            migrated = true;
        }
    }

    registry.targets.retain(|target| {
        target.id != LOCAL_TARGET_ID
            && valid_id(&target.id)
            && validate_name(&target.name).is_ok()
            && normalize_target_url(&target.url).is_ok()
    });
    if registry.active_id != LOCAL_TARGET_ID
        && !registry
            .targets
            .iter()
            .any(|target| target.id == registry.active_id)
    {
        registry.active_id = LOCAL_TARGET_ID.to_string();
        migrated = true;
    }
    if migrated {
        persist(&registry)?;
    }
    Ok(registry)
}

pub fn load() -> Result<(String, Vec<TargetRecord>), String> {
    let registry = load_stored()?;
    Ok((registry.active_id, registry.targets))
}

pub fn save_target(id: Option<&str>, name: &str, url: &str) -> Result<TargetRecord, String> {
    let mut registry = load_stored()?;
    let normalized_url = normalize_target_url(url)?;
    let origin = target_origin(&normalized_url)?;
    if registry.targets.iter().any(|entry| {
        entry.id != id.unwrap_or("")
            && target_origin(&entry.url).ok().as_deref() == Some(origin.as_str())
    }) {
        return Err("Another target already uses this origin; use one profile per origin".into());
    }
    let target_id = match id {
        Some(value) => {
            validate_id(value)?;
            value.to_string()
        }
        None => unique_id(&registry, name),
    };
    let target = validate_target(&target_id, name, &normalized_url)?;
    let url_changed = registry
        .targets
        .iter()
        .find(|entry| entry.id == target.id)
        .map(|entry| entry.url != target.url)
        .unwrap_or(false);
    if let Some(existing) = registry
        .targets
        .iter_mut()
        .find(|entry| entry.id == target.id)
    {
        *existing = target.clone();
    } else {
        registry.targets.push(target.clone());
    }
    persist(&registry)?;
    if url_changed {
        // Persist new metadata first. If secret invalidation fails, report it
        // instead of claiming old credentials remain usable.
        remove_optional_file(&token_path_for(&target.id))?;
    }
    Ok(target)
}

pub fn remove_target(id: &str) -> Result<(), String> {
    validate_id(id)?;
    if id == LOCAL_TARGET_ID {
        return Err("The local target cannot be removed".into());
    }
    let mut registry = load_stored()?;
    let previous_len = registry.targets.len();
    registry.targets.retain(|target| target.id != id);
    if registry.targets.len() == previous_len {
        return Err("Target not found".into());
    }
    if registry.active_id == id {
        registry.active_id = LOCAL_TARGET_ID.to_string();
    }
    persist(&registry)?;
    let token = if id == LEGACY_TARGET_ID {
        legacy_token_path()
    } else {
        secret_path(id)
    };
    remove_optional_file(&token)?;
    if id == LEGACY_TARGET_ID {
        remove_optional_file(&legacy_url_path())?;
    }
    Ok(())
}

pub fn set_active(id: &str) -> Result<(), String> {
    if id != LOCAL_TARGET_ID {
        validate_id(id)?;
    }
    let mut registry = load_stored()?;
    if id != LOCAL_TARGET_ID && !registry.targets.iter().any(|target| target.id == id) {
        return Err("Target not found".into());
    }
    registry.active_id = id.to_string();
    persist(&registry)
}

pub fn target(id: &str) -> Result<TargetRecord, String> {
    if id == LOCAL_TARGET_ID {
        return Ok(TargetRecord {
            id: LOCAL_TARGET_ID.to_string(),
            name: "Local".to_string(),
            url: String::new(),
        });
    }
    let (_, targets) = load()?;
    targets
        .into_iter()
        .find(|target| target.id == id)
        .ok_or_else(|| "Target not found".to_string())
}

pub fn save_token(id: &str, token: &str) -> Result<(), String> {
    validate_id(id)?;
    if id == LOCAL_TARGET_ID {
        return Err("The local target does not use a token".into());
    }
    let (_, targets) = load()?;
    if id == ENV_TARGET_ID || !targets.iter().any(|target| target.id == id) {
        return Err("Target not found".into());
    }
    let token = token.trim();
    if token.is_empty() {
        return Ok(());
    }
    let path = if id == LEGACY_TARGET_ID {
        legacy_token_path()
    } else {
        secret_path(id)
    };
    atomic_write(&path, token, 0o600)
}

pub fn read_token(id: &str) -> Option<String> {
    if id == LOCAL_TARGET_ID || validate_id(id).is_err() {
        return None;
    }
    let primary = if id == LEGACY_TARGET_ID {
        legacy_token_path()
    } else {
        secret_path(id)
    };
    fs::read_to_string(primary)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

pub fn list(
    active_id: &str,
    statuses: &std::collections::HashMap<String, (String, Option<String>)>,
    env_url: Option<&str>,
) -> Result<TargetList, String> {
    let (stored_active, mut targets) = load()?;
    if let Some(url) = env_url.filter(|value| !value.is_empty()) {
        targets.push(TargetRecord {
            id: ENV_TARGET_ID.to_string(),
            name: "Environment remote".to_string(),
            url: normalize_target_url(url)?,
        });
    }
    let effective_active = if active_id.is_empty() {
        stored_active
    } else {
        active_id.to_string()
    };
    let mut views = Vec::with_capacity(targets.len() + 1);
    let local_status = statuses
        .get(LOCAL_TARGET_ID)
        .cloned()
        .unwrap_or_else(|| ("offline".to_string(), None));
    views.push(TargetView {
        id: LOCAL_TARGET_ID.to_string(),
        name: "Local".to_string(),
        url: String::new(),
        kind: "local".to_string(),
        status: local_status.0,
        error: local_status.1,
        has_token: false,
        from_env: false,
    });
    for target in targets {
        let from_env = target.id == ENV_TARGET_ID;
        let status = statuses
            .get(&target.id)
            .cloned()
            .unwrap_or_else(|| ("offline".to_string(), None));
        views.push(TargetView {
            id: target.id.clone(),
            name: target.name,
            url: target.url,
            kind: "remote".to_string(),
            status: status.0,
            error: status.1,
            has_token: !from_env && read_token(&target.id).is_some(),
            from_env,
        });
    }
    Ok(TargetList {
        active_id: effective_active,
        targets: views,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn validates_http_urls_without_accepting_credentials() {
        assert_eq!(
            normalize_target_url(" https://example.test/// ").unwrap(),
            "https://example.test"
        );
        assert!(normalize_target_url("ftp://example.test").is_err());
        assert!(normalize_target_url("https://user:secret@example.test").is_err());
        assert_eq!(
            target_origin("https://example.test/path").unwrap(),
            "https://example.test:443"
        );
    }

    #[test]
    fn registry_round_trip_and_token_are_separate() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!("kompany-target-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        std::env::set_var("KOMPANY_DESKTOP_TARGETS_FILE", root.join("registry.json"));
        std::env::set_var("KOMPANY_DATA_DIR", root.join("data"));

        let target = save_target(None, "Production", "https://example.test").unwrap();
        save_token(&target.id, "do-not-put-this-in-registry").unwrap();
        let (_, targets) = load().unwrap();
        let registry = fs::read_to_string(root.join("registry.json")).unwrap();
        assert!(registry.contains("Production"));
        assert!(!registry.contains("do-not-put-this-in-registry"));
        assert_eq!(targets[0], target);
        assert_eq!(
            read_token(&target.id).as_deref(),
            Some("do-not-put-this-in-registry")
        );
        save_target(Some(&target.id), "Production", "https://changed.example").unwrap();
        assert!(read_token(&target.id).is_none());

        let _ = fs::remove_dir_all(&root);
        std::env::remove_var("KOMPANY_DESKTOP_TARGETS_FILE");
        std::env::remove_var("KOMPANY_DATA_DIR");
    }

    #[test]
    fn token_write_rejects_invalid_or_unknown_target_ids() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!(
            "kompany-token-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        std::env::set_var("KOMPANY_DESKTOP_TARGETS_FILE", root.join("registry.json"));
        std::env::set_var("KOMPANY_DATA_DIR", root.join("data"));

        assert!(save_token("../escape", "secret").is_err());
        assert!(save_token("missing", "secret").is_err());

        let _ = fs::remove_dir_all(&root);
        std::env::remove_var("KOMPANY_DESKTOP_TARGETS_FILE");
        std::env::remove_var("KOMPANY_DATA_DIR");
    }

    #[test]
    fn legacy_remote_url_is_migrated() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!("kompany-legacy-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        std::env::set_var("KOMPANY_DESKTOP_TARGETS_FILE", root.join("registry.json"));
        std::env::set_var("KOMPANY_DATA_DIR", root.join("data"));
        fs::create_dir_all(root.join("data")).unwrap();
        fs::write(root.join("data/remote_url"), "https://legacy.example/").unwrap();

        let (active, targets) = load().unwrap();
        assert_eq!(active, LEGACY_TARGET_ID);
        assert_eq!(targets[0].url, "https://legacy.example");
        assert!(registry_path().exists());

        let _ = fs::remove_dir_all(&root);
        std::env::remove_var("KOMPANY_DESKTOP_TARGETS_FILE");
        std::env::remove_var("KOMPANY_DATA_DIR");
    }
}
