// Tauri build script — generates platform glue at compile time and
// bakes the tauri shell's git commit into the binary as an env var so
// main.rs can stamp it into the window title next to the daemon commit
// fetched from /version at runtime.
fn main() {
    tauri_build::build();

    // Walk up from CARGO_MANIFEST_DIR to find a .git dir (the tauri
    // crate is a subdir of the kompany-core repo, so the git root is
    // the parent). Fall back to "unknown" when not in a git checkout
    // (e.g. a tarball build) — cargo:rustc-env must always be emitted
    // so the compile-time constant resolves.
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default();
    let commit = find_git_commit(&manifest_dir);
    println!("cargo:rustc-env=KOMPANY_TAURI_COMMIT={}", commit);
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/refs");
}

fn find_git_commit(start_dir: &str) -> String {
    use std::process::Command;

    let mut dir = std::path::PathBuf::from(start_dir);
    loop {
        if dir.join(".git").exists() {
            let out = Command::new("git")
                .arg("rev-parse")
                .arg("--short")
                .arg("HEAD")
                .current_dir(&dir)
                .output();
            if let Ok(o) = out {
                if o.status.success() {
                    let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
                    if !s.is_empty() {
                        return s;
                    }
                }
            }
            return "unknown".to_string();
        }
        if !dir.pop() {
            break;
        }
    }
    "unknown".to_string()
}
