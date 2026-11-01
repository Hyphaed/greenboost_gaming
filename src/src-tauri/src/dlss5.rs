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

/// Where `greenboost_proton/gb_proton_main.py`'s `_record_dlss5_exe_dir_learned()`
/// writes ground truth every real launch: `sys.argv[2]`, the literal exe
/// path Steam itself told Proton to run , zero ambiguity, for every engine,
/// not just the ones a static heuristic happens to recognise. Same file both
/// processes read/write, so no IPC , this is a small, best-effort JSON
/// cache, not a database.
fn learned_exe_dir_cache_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".local/share/greenboost/dlss5_exe_dirs.json"))
}

/// Reads back what a real launch already proved about this game, if any.
/// Never trusted blindly , the game could have been reinstalled, updated,
/// or moved a Steam library since the cache entry was written, so the
/// directory has to still exist and still contain at least one real
/// (non-helper) `.exe` before this returns it. A stale or missing entry
/// falls through to the static heuristics below, exactly as if the cache
/// didn't exist , this is purely an accelerant for games that have already
/// launched once, never a hard dependency.
fn learned_exe_dir(appid: &str) -> Option<PathBuf> {
    let cache_path = learned_exe_dir_cache_path()?;
    learned_exe_dir_from_cache(&cache_path, appid)
}

/// The actual lookup, taking the cache file path as a parameter so it's
/// testable against a fixture instead of the real `$HOME`.
fn learned_exe_dir_from_cache(cache_path: &Path, appid: &str) -> Option<PathBuf> {
    if appid.is_empty() {
        return None;
    }
    let text = std::fs::read_to_string(cache_path).ok()?;
    let cache: serde_json::Value = serde_json::from_str(&text).ok()?;
    let exe_dir = cache.get(appid)?.get("exe_dir")?.as_str()?;
    let path = PathBuf::from(exe_dir);
    if !path.is_dir() {
        return None;
    }
    let still_has_an_exe = std::fs::read_dir(&path).ok()?.flatten().any(|e| {
        e.file_type().is_ok_and(|t| t.is_file())
            && e.file_name().to_str().is_some_and(|n| n.to_lowercase().ends_with(".exe"))
    });
    still_has_an_exe.then_some(path)
}

/// Unreal Engine's own, well-documented shipping-exe location , the OptiScaler
/// wiki's own install instructions name it explicitly: "look for the
/// *-Win64-Shipping.exe in <ProjectName>/Binaries/Win64/ or WinGDK/, ignore
/// the Engine folder". A root-level launcher stub (thin DRM/prereq wrapper
/// that hands off to this) is common alongside it, which is exactly the
/// ambiguity this resolves , not a guess, a documented, near-universal UE
/// convention. `Engine/Binaries/...` is excluded even though it matches the
/// same path shape, because that's the engine's OWN tooling (e.g.
/// CrashReportClient.exe , confirmed 2026-09-02 in a real "The First
/// Berserker Khazan" install), never the game.
fn looks_like_unreal_shipping_binary(path: &Path) -> bool {
    let mut saw_binaries = false;
    for component in path.components() {
        let Some(name) = component.as_os_str().to_str() else { continue };
        let lower = name.to_lowercase();
        if lower == "engine" {
            return false;
        }
        if saw_binaries && (lower == "win64" || lower == "wingdk") {
            return true;
        }
        saw_binaries = lower == "binaries";
    }
    false
}

/// Finds the directory holding the game's real executable. Three tiers, in
/// order, each one a fallback for when the one before it has nothing:
///
/// 1. **Learned** (`learned_exe_dir`) , ground truth from an actual launch,
///    if this game has ever run through GreenBoost Proton. No guessing
///    involved at all; works for every engine, including ones no static
///    heuristic here has ever heard of.
/// 2. **Single unambiguous `.exe`** , the original heuristic: if exactly one
///    non-helper `.exe` exists anywhere in the tree, that's it.
/// 3. **Unreal Engine shape** (`looks_like_unreal_shipping_binary`) , when
///    there's more than one candidate, prefer the one under a
///    `<Project>/Binaries/Win64|WinGDK/` path (excluding `Engine/`'s own
///    binaries), a well-documented, near-universal UE convention , confirmed
///    2026-09-02 against a real "FINAL FANTASY VII REBIRTH" install
///    (`ff7rebirth.exe` launcher stub at the root, the real
///    `ff7rebirth_.exe` at `End/Binaries/Win64/`), previously unresolvable by
///    tier 2 alone.
///
/// Returns `None` , never a guess beyond what these three tiers already
/// establish , when even the UE-aware filter leaves zero or more than one
/// candidate. A game that's never launched yet and has a layout no tier here
/// recognises needs the manual exe-name entry; once it launches once
/// (however that install gets configured), tier 1 takes over for good.
pub fn resolve_game_exe_dir_impl(appid: &str, game_dir: &Path) -> Option<PathBuf> {
    if let Some(learned) = learned_exe_dir(appid) {
        return Some(learned);
    }

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
        [] => None,
        _ => {
            let ue_shipping: Vec<&PathBuf> = exes.iter()
                .filter(|p| looks_like_unreal_shipping_binary(p))
                .collect();
            match ue_shipping.as_slice() {
                [exe] => exe.parent().map(|p| p.to_path_buf()),
                _ => None,
            }
        }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    struct TmpDir(PathBuf);
    impl TmpDir {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "gb_dlss5_test_{tag}_{}_{:?}",
                std::process::id(), std::thread::current().id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            TmpDir(dir)
        }
        fn path(&self) -> &Path { &self.0 }
    }
    impl Drop for TmpDir {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    fn write_cache(cache_path: &Path, appid: &str, exe_dir: &Path) {
        let json = serde_json::json!({ appid: { "exe_dir": exe_dir.to_string_lossy(), "last_seen": 0 } });
        fs::write(cache_path, serde_json::to_string(&json).unwrap()).unwrap();
    }

    // ── learned_exe_dir_from_cache ──────────────────────────────────────

    #[test]
    fn learned_cache_hit_when_dir_still_has_an_exe() {
        let tmp = TmpDir::new("cache_hit");
        let exe_dir = tmp.path().join("game/End/Binaries/Win64");
        fs::create_dir_all(&exe_dir).unwrap();
        fs::write(exe_dir.join("real.exe"), b"").unwrap();
        let cache_path = tmp.path().join("cache.json");
        write_cache(&cache_path, "3768760", &exe_dir);

        assert_eq!(learned_exe_dir_from_cache(&cache_path, "3768760"), Some(exe_dir));
    }

    #[test]
    fn learned_cache_miss_for_a_different_appid() {
        let tmp = TmpDir::new("cache_miss_appid");
        let exe_dir = tmp.path().join("game");
        fs::create_dir_all(&exe_dir).unwrap();
        fs::write(exe_dir.join("real.exe"), b"").unwrap();
        let cache_path = tmp.path().join("cache.json");
        write_cache(&cache_path, "3768760", &exe_dir);

        assert_eq!(learned_exe_dir_from_cache(&cache_path, "999999"), None);
    }

    #[test]
    fn learned_cache_stale_when_the_directory_is_gone() {
        // The game was uninstalled, or moved to a different Steam library,
        // since the cache entry was written , must not trust a dead path.
        let tmp = TmpDir::new("cache_stale_dir");
        let cache_path = tmp.path().join("cache.json");
        write_cache(&cache_path, "3768760", &tmp.path().join("no/such/dir"));

        assert_eq!(learned_exe_dir_from_cache(&cache_path, "3768760"), None);
    }

    #[test]
    fn learned_cache_stale_when_the_exe_is_gone_but_dir_remains() {
        // A game update removed/renamed the exe , the directory surviving
        // isn't enough on its own.
        let tmp = TmpDir::new("cache_stale_exe");
        let exe_dir = tmp.path().join("game");
        fs::create_dir_all(&exe_dir).unwrap(); // no .exe inside
        let cache_path = tmp.path().join("cache.json");
        write_cache(&cache_path, "3768760", &exe_dir);

        assert_eq!(learned_exe_dir_from_cache(&cache_path, "3768760"), None);
    }

    #[test]
    fn learned_cache_missing_file_is_a_clean_miss() {
        let tmp = TmpDir::new("cache_no_file");
        assert_eq!(
            learned_exe_dir_from_cache(&tmp.path().join("does_not_exist.json"), "3768760"),
            None);
    }

    // ── looks_like_unreal_shipping_binary ───────────────────────────────

    #[test]
    fn ue_shape_recognises_project_binaries_win64() {
        assert!(looks_like_unreal_shipping_binary(
            Path::new("/games/FF7/End/Binaries/Win64/ff7rebirth_.exe")));
    }

    #[test]
    fn ue_shape_excludes_the_engines_own_binaries() {
        assert!(!looks_like_unreal_shipping_binary(
            Path::new("/games/Khazan/Engine/Binaries/Win64/CrashReportClient.exe")));
    }

    #[test]
    fn ue_shape_rejects_a_root_level_launcher_stub() {
        assert!(!looks_like_unreal_shipping_binary(Path::new("/games/FF7/ff7rebirth.exe")));
    }

    // ── resolve_game_exe_dir_impl (tiers 2 and 3; tier 1 needs the real
    // learned-cache file, exercised via learned_exe_dir_from_cache above) ──
    // A nonexistent, random appid guarantees tier 1 misses without touching
    // the real user's cache at $HOME.

    const NO_SUCH_APPID: &str = "test-appid-never-cached-943210";

    #[test]
    fn resolver_tier2_single_unambiguous_exe() {
        let tmp = TmpDir::new("resolver_single");
        let exe_dir = tmp.path().join("game");
        fs::create_dir_all(&exe_dir).unwrap();
        fs::write(exe_dir.join("game.exe"), b"").unwrap();

        assert_eq!(resolve_game_exe_dir_impl(NO_SUCH_APPID, tmp.path()), Some(exe_dir));
    }

    #[test]
    fn resolver_tier3_prefers_the_ue_shipping_binary_over_the_launcher_stub() {
        let tmp = TmpDir::new("resolver_ue");
        fs::create_dir_all(tmp.path()).unwrap();
        fs::write(tmp.path().join("ff7rebirth.exe"), b"").unwrap();
        let shipping_dir = tmp.path().join("End/Binaries/Win64");
        fs::create_dir_all(&shipping_dir).unwrap();
        fs::write(shipping_dir.join("ff7rebirth_.exe"), b"").unwrap();

        assert_eq!(resolve_game_exe_dir_impl(NO_SUCH_APPID, tmp.path()), Some(shipping_dir));
    }

    #[test]
    fn resolver_gives_up_when_genuinely_ambiguous() {
        // Two candidates, neither in a Binaries/Win64 shape , no tier can
        // pick one without guessing, so this must stay None.
        let tmp = TmpDir::new("resolver_ambiguous");
        fs::create_dir_all(tmp.path()).unwrap();
        fs::write(tmp.path().join("a.exe"), b"").unwrap();
        fs::write(tmp.path().join("b.exe"), b"").unwrap();

        assert_eq!(resolve_game_exe_dir_impl(NO_SUCH_APPID, tmp.path()), None);
    }
}
