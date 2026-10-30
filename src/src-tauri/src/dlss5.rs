// Copyright 2026 Ferran Duarri , GPL v2
// Tauri command layer over gb_gaming/dlss5.py , DLSS 5 (Neural Rendering) as
// a one-toggle feature. This is a thin shell: every real decision (locating
// the DLSS5 runtime, picking a free DLL proxy slot, the preflight checks,
// turning the pass on in the runtime's own ini) lives in Python; this module
// only marshals JSON across the python3 -c boundary via py_bootstrap(). See
// gb_gaming/dlss5.py and gb_gaming/dlss5_runtime.py's module docstrings, and
// docs/dlss5-external-folders.md, for the mechanism and its history , this
// rewrite (2026-09-02) replaced an earlier mechanism built around patching a
// leaked RenoDX "Control" add-on's process-name check, which could never get
// Neural Rendering itself working outside Control , and dropped ReShade
// entirely, since this runtime needs neither it nor a Streamline DLL swap.

use std::path::{Path, PathBuf};
use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct PreflightCheck {
    pub check: String,
    pub ok: bool,
    pub level: String,
    pub detail: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Dlss5EnableResult {
    pub ok: bool,
    #[serde(default)]
    pub msg: String,
    /// The DLL name the DLSS5 runtime was installed as (e.g. "winmm.dll") ,
    /// the caller must append `<stem>=n,b` to WINEDLLOVERRIDES for it to
    /// load; see GameOverrides::dlss5_proxy_dll and its consumption in
    /// greenboost_proton/gb_proton_main.py.
    #[serde(default)]
    pub proxy_dll: String,
    #[serde(default)]
    pub files: Vec<String>,
}

/// One row of dlss5_list_assets_impl: a known filename and whether it's
/// actually sitting in asset_dir() right now. nvngx_dlssnr.dll is the only
/// one , NVIDIA's model DLL, which GreenBoost cannot supply or redistribute.
/// See gb_gaming.dlss5.list_assets().
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Dlss5AssetInfo {
    pub name: String,
    pub present: bool,
    pub required: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Dlss5AssetList {
    pub asset_dir: String,
    #[serde(default)]
    pub files: Vec<Dlss5AssetInfo>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Dlss5SimpleResult {
    pub ok: bool,
    #[serde(default)]
    pub msg: String,
}

/// Non-game helper binaries commonly found beside a real game exe , never
/// what DLSS 5 should be gated on. Substring match, lowercase.
pub(crate) const IGNORED_EXE_SUBSTRINGS: &[&str] = &[
    "crashhandler", "crashreporter", "crash_uploader", "installer", "setup",
    "unins", "vc_redist", "vcredist", "dotnetfx", "directx_setup", "redist",
    "battleye", "easyanticheat", "eac_launcher",
];

/// Best-effort resolution of a game's real executable name from its exe
/// directory. Filters out common non-game helper binaries by name. Returns
/// None , not a guess , when zero or more than one candidate remains.
/// Kept for any future exe-name display; DLSS 5 itself no longer needs an
/// exe name at all , the runtime's install and its Neural Rendering pass are
/// per-directory, not per-exe (see gb_gaming.dlss5.enable_for_game()'s
/// docstring). `resolve_game_exe_dir_impl` below is what DLSS 5 actually
/// uses to find where to install.
pub fn resolve_primary_exe_impl(game_dir: &Path) -> Option<String> {
    let entries = std::fs::read_dir(game_dir).ok()?;
    let candidates: Vec<String> = entries
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let lower = name.to_lowercase();
            if !lower.ends_with(".exe") {
                return None;
            }
            if IGNORED_EXE_SUBSTRINGS.iter().any(|s| lower.contains(s)) {
                return None;
            }
            Some(name)
        })
        .collect();
    match candidates.len() {
        1 => Some(candidates.into_iter().next().unwrap()),
        _ => None,
    }
}

/// Finds the directory holding the game's real executable, searching down
/// from `game_dir` (the Steam install root, which for an Unreal-style
/// launcher-stub layout is NOT where the real exe lives , confirmed
/// 2026-08-28 against a real "FINAL FANTASY VII REBIRTH" install, whose exe
/// sits three directories below the install root). This is what DLSS 5
/// installs the runtime into , unlike `resolve_primary_exe_impl` (which
/// only looks in a directory it's already given), this walks depth-bounded
/// like the scanner's own shortcut-game walk (`scanner.rs`'s
/// `max_depth(6)`) to find that directory in the first place.
///
/// Returns `None` , never a guess , when the tree has zero non-helper
/// `.exe` files, or more than one (even if they'd share a directory): a
/// game with a genuinely ambiguous layout needs the manual exe-name entry,
/// not an auto-pick. Matches `resolve_primary_exe_impl`'s own file-count
/// semantics, not a directory count , two candidate exes in the same
/// folder are exactly as ambiguous as two in different folders.
pub fn resolve_game_exe_dir_impl(game_dir: &Path) -> Option<PathBuf> {
    let exes: Vec<PathBuf> = WalkDir::new(game_dir)
        .max_depth(6)
        .into_iter()
        .flatten()
        .filter(|entry| {
            entry.file_type().is_file()
                && entry.file_name().to_str().is_some_and(|n| {
                    let lower = n.to_lowercase();
                    lower.ends_with(".exe")
                        && !IGNORED_EXE_SUBSTRINGS.iter().any(|s| lower.contains(s))
                })
        })
        .map(|entry| entry.path().to_path_buf())
        .collect();
    match exes.as_slice() {
        [exe] => exe.parent().map(|p| p.to_path_buf()),
        _ => None,
    }
}

fn run_python(script: &str) -> Result<Vec<u8>, String> {
    let out = std::process::Command::new("python3")
        .args(["-c", script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    Ok(out.stdout)
}

/// Real, measured preflight checks , GPU/driver via nvidia-smi, whether the
/// DLSS5 runtime is staged, whether a free DLL proxy slot exists. See
/// gb_gaming.dlss5.preflight() for what each row actually checks.
pub fn dlss5_preflight_impl(
    game_dir: &Path, appid: &str,
) -> Result<Vec<PreflightCheck>, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let appid_lit = serde_json::to_string(appid).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.preflight(Path({game_lit}), {appid_lit})))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// Installs the DLSS5 runtime into `game_dir`, and turns Neural Rendering on
/// directly in its own ini. The caller must save the returned `proxy_dll`
/// into GameOverrides::dlss5_proxy_dll alongside `dlss5_enabled: true` , the
/// Proton wrapper reads that field to append the matching WINEDLLOVERRIDES
/// entry. See gb_gaming.dlss5.enable_for_game().
pub fn dlss5_enable_impl(
    game_dir: &Path, appid: &str,
) -> Result<Dlss5EnableResult, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let appid_lit = serde_json::to_string(appid).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.enable_for_game(Path({game_lit}), {appid_lit})))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// The one file this feature needs the user to supply (nvngx_dlssnr.dll)
/// and whether it's actually present in asset_dir() right now , drives the
/// "files needed" modal shown before the user ever presses Enable. See
/// gb_gaming.dlss5.list_assets().
pub fn dlss5_list_assets_impl() -> Result<Dlss5AssetList, String> {
    let script = format!(
        "{bootstrap}import json\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.list_assets()))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// Fully removes what dlss5_enable_impl installed into `game_dir` , unlike
/// the previous per-appid staging mechanism, this one installs directly
/// into the game directory, so disable is a real uninstall, not a separate
/// "Restore" step. See gb_gaming.dlss5.disable_for_game().
pub fn dlss5_disable_impl(game_dir: &Path, appid: &str) -> Result<Dlss5EnableResult, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let appid_lit = serde_json::to_string(appid).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.disable_for_game(Path({game_lit}), {appid_lit})))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// Reads the Neural Rendering + adjacent-feature config keys (Preset,
/// Style, Intensity, LocalStructure, ... , see gb_gaming.dlss5.CONFIG_KEYS
/// for the full list) out of `game_dir/GreenBoostDLSS5.ini`. A key absent
/// from the returned map means "auto" (the runtime's own built-in default),
/// not zero , the panel must render that distinction, not paper over it.
pub fn dlss5_get_config_impl(game_dir: &Path) -> Result<HashMap<String, String>, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.read_config(Path({game_lit}))))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// Writes `changes` ("Section.Key" -> value) into `game_dir/GreenBoostDLSS5.ini`.
/// Rejects any key gb_gaming.dlss5.CONFIG_KEYS doesn't recognise rather than
/// writing an arbitrary ini entry on the panel's behalf.
pub fn dlss5_set_config_impl(
    game_dir: &Path, changes: &HashMap<String, String>,
) -> Result<Dlss5SimpleResult, String> {
    let game_lit = serde_json::to_string(game_dir.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    // changes_json is the JSON object text itself; changes_py_lit re-escapes
    // it as a Python string literal (same double-encoding as game_lit/
    // appid_lit elsewhere in this file) so the generated script can
    // json.loads() it back out unambiguously regardless of quotes or
    // backslashes in a value.
    let changes_json = serde_json::to_string(changes).map_err(|e| e.to_string())?;
    let changes_py_lit = serde_json::to_string(&changes_json).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.write_config(Path({game_lit}), json.loads({changes_py_lit}))))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}

/// The folder the user picked via the "Select DLSS5 folder" button, or ""
/// if none was ever picked. See gb_gaming.dlss5.get_asset_dir_override().
pub fn dlss5_get_asset_dir_impl() -> Result<String, String> {
    let script = format!(
        "{bootstrap}from gb_gaming import dlss5 as d\nprint(d.get_asset_dir_override())\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    Ok(String::from_utf8_lossy(&stdout).trim_end().to_string())
}

/// Persists the folder the user picked as the one to look for
/// nvngx_dlssnr.dll (and any other DLSS5 asset) in going forward. An empty
/// path clears the override. See gb_gaming.dlss5.set_asset_dir_override().
pub fn dlss5_set_asset_dir_impl(path: &str) -> Result<Dlss5SimpleResult, String> {
    let path_lit = serde_json::to_string(path).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom gb_gaming import dlss5 as d\nprint(json.dumps(d.set_asset_dir_override({path_lit})))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = run_python(&script)?;
    serde_json::from_slice(&stdout).map_err(|e| format!("bad JSON: {e}"))
}
