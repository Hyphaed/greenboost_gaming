// Copyright 2026 Ferran Duarri , GPL v2
// In-app folder browser and folder inspection (via the Python DLL
// updater). Companion to game_overrides.rs's per-game external_dll_dir /
// external_dll_targets, which stay the single values the Proton wrapper
// reads , this module is a browser and copy-in/restore layer on top of
// that contract, not a new one.
//
// The reusable named-folder library this module used to also own
// (list_dll_folders_impl/save_dll_folders_impl, ~/.config/greenboost-gaming/
// dll-folders.json) was removed 2026-09-02 when the External DLLs section
// merged into the DLSS 5 (Neural Rendering) panel , see Dlss5Panel.tsx.
// One button ("Select DLSS5 folder") now drives both the DLSS5 runtime's
// asset folder and this module's overlay/copy-in, so a separate
// multi-folder picker had nothing left to serve.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};

// ── In-app folder browser , no dialog plugin, no new dependency ─────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct DirEntryInfo {
    pub name: String,
    pub path: String,
    pub dll_count: u32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct BrowseResult {
    pub path: String,
    pub parent: Option<String>,
    pub home: String,
    pub entries: Vec<DirEntryInfo>,
    /// *.dll directly inside `path` itself , lets the UI say "this folder
    /// is the one you want" without descending into it.
    pub dll_count: u32,
}

const MAX_BROWSE_ENTRIES: usize = 2000;

fn count_dlls(dir: &Path) -> u32 {
    let Ok(read) = std::fs::read_dir(dir) else { return 0 };
    let mut n = 0u32;
    for entry in read.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.to_lowercase().ends_with(".dll") {
            if let Ok(ft) = entry.file_type() {
                if ft.is_file() {
                    n += 1;
                }
            }
        }
    }
    n
}

/// Lists the subdirectories of `path` (or $HOME when `path` is None/empty/
/// unreadable) plus a shallow `.dll` count per entry and for `path` itself.
/// Never errors outward , a stale saved path that no longer exists falls
/// back to $HOME rather than leaving the modal stuck.
pub fn browse_folder_impl(path: Option<&str>) -> Result<BrowseResult, String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/".to_string());
    let requested = path.filter(|p| !p.is_empty()).map(|p| {
        if let Some(rest) = p.strip_prefix('~') {
            format!("{home}{rest}")
        } else {
            p.to_string()
        }
    });

    let resolved = requested
        .as_deref()
        .map(Path::new)
        .and_then(|p| std::fs::canonicalize(p).ok())
        .filter(|p| p.is_dir())
        .unwrap_or_else(|| PathBuf::from(&home));

    let read = std::fs::read_dir(&resolved)
        .map_err(|e| format!("cannot read {}: {e}", resolved.display()))?;

    let mut entries: Vec<DirEntryInfo> = Vec::new();
    for entry in read.flatten().take(MAX_BROWSE_ENTRIES) {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.') {
            continue;
        }
        let Ok(ft) = entry.file_type() else { continue };
        if !ft.is_dir() {
            continue;
        }
        let child = entry.path();
        entries.push(DirEntryInfo {
            dll_count: count_dlls(&child),
            name,
            path: child.to_string_lossy().to_string(),
        });
    }
    entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));

    let parent = resolved.parent().map(|p| p.to_string_lossy().to_string());
    let dll_count = count_dlls(&resolved);

    Ok(BrowseResult {
        path: resolved.to_string_lossy().to_string(),
        parent,
        home,
        entries,
        dll_count,
    })
}

// ── Folder inspection , shells out to the same PE-version reader the
// DLSS updater uses, via the established py_bootstrap() pattern ────────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ExternalDll {
    pub name: String,
    pub version: String,
    pub known_type: String,
    pub size_bytes: u64,
    /// PE VS_VERSIONINFO FileDescription, when the file carries one , e.g.
    /// "SL.INTERPOSER MODULE - PRODUCTION" for a genuine Streamline build.
    /// "" when absent (many ReShade add-ons don't embed one at all, verified
    /// against a real one 2026-08-28) , this is informational only, never
    /// load-bearing for any placement decision.
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub product: String,
}

pub fn inspect_dll_folder_impl(path: &str) -> Result<Vec<ExternalDll>, String> {
    let path_lit = serde_json::to_string(path).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.inspect_external_folder(Path({path_lit}))))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = std::process::Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| format!("bad JSON: {e}"))
}

// ── Copy-into-game / restore , mirrors dlss.rs's shell-out shape ──────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct FileResult {
    pub name: String,
    pub ok: bool,
    pub msg: String,
    #[serde(default)]
    pub added: bool,
}

fn targets_literal(targets: &HashMap<String, Vec<String>>) -> Result<String, String> {
    serde_json::to_string(targets).map_err(|e| e.to_string())
}

pub fn install_external_folder_impl(
    src_dir: &str,
    game_dir: &Path,
    targets: &HashMap<String, Vec<String>>,
    disabled: &[String],
) -> Result<Vec<FileResult>, String> {
    let src_lit = serde_json::to_string(src_dir).map_err(|e| e.to_string())?;
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let targets_lit = targets_literal(targets)?;
    let disabled_lit = serde_json::to_string(disabled).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.install_external_folder_into_game(Path({src_lit}), Path({game_lit}), targets=json.loads({targets_json}), disabled=json.loads({disabled_json}))))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap(),
        targets_json = serde_json::to_string(&targets_lit).map_err(|e| e.to_string())?,
        disabled_json = serde_json::to_string(&disabled_lit).map_err(|e| e.to_string())?,
    );
    let out = std::process::Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| format!("bad JSON: {e}"))
}

pub fn restore_external_folder_impl(
    game_dir: &Path,
    targets: &HashMap<String, Vec<String>>,
) -> Result<Vec<FileResult>, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let targets_lit = targets_literal(targets)?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.restore_external_folder_from_game(Path({game_lit}), targets=json.loads({targets_json}))))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap(),
        targets_json = serde_json::to_string(&targets_lit).map_err(|e| e.to_string())?,
    );
    let out = std::process::Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| format!("bad JSON: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browse_falls_back_to_home_on_bad_path() {
        let home = std::env::var("HOME").unwrap_or_default();
        let result = browse_folder_impl(Some("/does/not/exist/at/all")).unwrap();
        assert_eq!(result.path, std::fs::canonicalize(&home).unwrap().to_string_lossy());
    }

    #[test]
    fn browse_skips_hidden_dirs_and_counts_dlls() {
        let tmp = std::env::temp_dir().join(format!("gb-test-browse-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("visible")).unwrap();
        std::fs::create_dir_all(tmp.join(".hidden")).unwrap();
        std::fs::write(tmp.join("visible").join("a.dll"), b"x").unwrap();
        std::fs::write(tmp.join("visible").join("b.DLL"), b"x").unwrap();
        std::fs::write(tmp.join("visible").join("c.txt"), b"x").unwrap();

        let result = browse_folder_impl(Some(tmp.to_str().unwrap())).unwrap();
        assert_eq!(result.entries.len(), 1);
        assert_eq!(result.entries[0].name, "visible");
        assert_eq!(result.entries[0].dll_count, 2);

        std::fs::remove_dir_all(&tmp).ok();
    }
}
