// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

use std::path::{Path, PathBuf};
use serde::{Serialize, Deserialize};
use walkdir::WalkDir;

// NVIDIA-only DLLs , explicitly excludes AMD (amd_fidelityfx_*) and Intel (libxess*).
// All Streamline plugins must be listed so update_game_dlss treats the bundle
// atomically via dlss_updater.install_streamline_bundle_into_game().
const NVIDIA_DLLS: &[&str] = &[
    "nvngx_dlss.dll",
    "nvngx_dlssg.dll",
    "nvngx_dlssd.dll",
    "sl.common.dll",
    "sl.dlss.dll",
    "sl.dlss_d.dll",
    "sl.dlss_g.dll",
    "sl.interposer.dll",
    "sl.nis.dll",
    "sl.pcl.dll",
    "sl.reflex.dll",
];

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct DlssUpdateResult {
    pub updated: Vec<String>,
    pub already_current: Vec<String>,
    pub errors: Vec<String>,
}

/// PR-(UI fusion): restore the most-recent backup of each NVIDIA DLL
/// in `game_path`.  Recognises two backup formats:
///   • `<dll>.gdlss_bak`        , single-slot, written by update_game_dlss.
///   • `<dll>.dll.bak.<unix_ts>` , timestamped, written by the Python
///                                  `gb_gaming.dlss_updater` path.
///
/// The timestamped form wins on tie (newest backup restored).  Successful
/// restores show up in `result.updated` ("restored" semantically), files
/// with no backup go into `result.already_current`, anything else into
/// `result.errors`.
pub fn restore_game_dlss(game_path: &Path) -> DlssUpdateResult {
    let mut result = DlssUpdateResult {
        updated: Vec::new(),
        already_current: Vec::new(),
        errors: Vec::new(),
    };

    // Walk the game dir for any NVIDIA DLL we know about.
    let mut dll_paths: Vec<PathBuf> = Vec::new();
    for entry in WalkDir::new(game_path).max_depth(10).into_iter().flatten() {
        let p = entry.path();
        if !p.is_file() { continue; }
        if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
            if NVIDIA_DLLS.contains(&name.to_lowercase().as_str()) {
                dll_paths.push(p.to_path_buf());
            }
        }
    }

    if dll_paths.is_empty() {
        result.already_current.push("No NVIDIA DLLs found in game directory".to_string());
        return result;
    }

    for dll_path in dll_paths {
        let name = dll_path.file_name()
            .and_then(|n| n.to_str()).unwrap_or("dll").to_string();
        // Locate the best backup candidate.
        let parent = match dll_path.parent() {
            Some(p) => p,
            None => {
                result.errors.push(format!("{}: no parent directory", name));
                continue;
            }
        };

        // Candidate 1: single-slot .gdlss_bak.
        let single = dll_path.with_extension("gdlss_bak");
        // Candidate 2..N: timestamped <basename>.bak.<digits>
        let mut timestamped: Vec<(u64, PathBuf)> = Vec::new();
        let needle = format!("{}.bak.", name);
        if let Ok(rd) = std::fs::read_dir(parent) {
            for entry in rd.flatten() {
                if let Some(fname) = entry.file_name().to_str() {
                    if let Some(ts_part) = fname.strip_prefix(&needle) {
                        if let Ok(ts) = ts_part.parse::<u64>() {
                            timestamped.push((ts, entry.path()));
                        }
                    }
                }
            }
        }
        timestamped.sort_by(|a, b| b.0.cmp(&a.0));

        // Pick the newest backup.  Compare mtime of the .gdlss_bak (if
        // present) against the highest timestamp in `timestamped`.
        let chosen: Option<PathBuf> = match (single.exists(), timestamped.first()) {
            (true, Some((ts_ms, ts_path))) => {
                let single_mtime = std::fs::metadata(&single).ok()
                    .and_then(|m| m.modified().ok())
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs()).unwrap_or(0);
                if single_mtime > *ts_ms { Some(single.clone()) }
                else                     { Some(ts_path.clone()) }
            }
            (true, None)               => Some(single.clone()),
            (false, Some((_, p)))      => Some(p.clone()),
            (false, None)              => None,
        };

        match chosen {
            None => result.already_current.push(format!("{}: no backup found", name)),
            Some(src) => {
                // Copy backup over the current DLL.  Use copy+rename to
                // be atomic on the same FS.
                let tmp = dll_path.with_extension("restore_tmp");
                if std::fs::copy(&src, &tmp).is_err() {
                    result.errors.push(format!("{}: failed to stage backup", name));
                    continue;
                }
                if std::fs::rename(&tmp, &dll_path).is_err() {
                    result.errors.push(format!("{}: failed to swap in backup", name));
                    let _ = std::fs::remove_file(&tmp);
                    continue;
                }
                let from = src.file_name().and_then(|n| n.to_str()).unwrap_or("?");
                result.updated.push(format!("{} restored from {}", name, from));
            }
        }
    }
    result
}

/// Restore every NVIDIA DLL in `game_path` to the version the GAME
/// actually shipped with , distinct from `restore_game_dlss` above, which
/// only ever undoes the single most recent update. Reads from the
/// `.gdlss_original` snapshot `update_game_dlss` writes once, the first
/// time it ever touches a given DLL, and never deletes it: safe to call
/// this repeatedly, and it stays available no matter how many times the
/// DLL has been updated since.
pub fn restore_game_dlss_to_original(game_path: &Path) -> DlssUpdateResult {
    let mut result = DlssUpdateResult {
        updated: Vec::new(),
        already_current: Vec::new(),
        errors: Vec::new(),
    };

    let mut dll_paths: Vec<PathBuf> = Vec::new();
    for entry in WalkDir::new(game_path).max_depth(10).into_iter().flatten() {
        let p = entry.path();
        if !p.is_file() { continue; }
        if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
            if NVIDIA_DLLS.contains(&name.to_lowercase().as_str()) {
                dll_paths.push(p.to_path_buf());
            }
        }
    }

    if dll_paths.is_empty() {
        result.already_current.push("No NVIDIA DLLs found in game directory".to_string());
        return result;
    }

    for dll_path in dll_paths {
        let name = dll_path.file_name()
            .and_then(|n| n.to_str()).unwrap_or("dll").to_string();
        let original = dll_path.with_extension("gdlss_original");
        if !original.exists() {
            // Never updated through GreenBoost, or updated before this
            // snapshot mechanism existed , nothing to restore to.
            result.already_current.push(format!("{name}: no original snapshot on record"));
            continue;
        }
        let tmp = dll_path.with_extension("restore_tmp");
        if std::fs::copy(&original, &tmp).is_err() {
            result.errors.push(format!("{name}: failed to stage original"));
            continue;
        }
        if std::fs::rename(&tmp, &dll_path).is_err() {
            result.errors.push(format!("{name}: failed to swap in original"));
            let _ = std::fs::remove_file(&tmp);
            continue;
        }
        result.updated.push(format!("{name} restored to shipped version"));
    }
    result
}

/// PR-SSS: DLSS update , delegates to the Python backend
/// `gb_gaming.dlss_updater`, which speaks the hybrid sourcing model
/// (Streamline → NVIDIA's official GitHub, nvngx_*.dll → bundled
/// `dlls/` directory or the user-selected community mirror).
///
/// The previous Rust implementation hit a hard-coded mirror manifest
/// (`Recol/DLSS-Updater-DLLs/manifest.json`) and looked up filenames
/// in a flat key/value style that no longer matches the real shape of
/// that manifest , every DLL ended up "not in manifest".  Rather than
/// keep two updaters in sync, we now have a single source of truth
/// (Python module) and the Tauri command is a thin bridge.
pub fn update_game_dlss(game_path: &Path) -> DlssUpdateResult {
    let mut result = DlssUpdateResult {
        updated: Vec::new(),
        already_current: Vec::new(),
        errors: Vec::new(),
    };

    // Walk the game dir to enumerate which NVIDIA DLLs are present.
    // We could let the Python side rglob, but doing it in Rust here
    // matches the scanner.rs pattern (max_depth 5) and lets us bail
    // early when no DLLs exist.
    let mut found: Vec<PathBuf> = Vec::new();
    for entry in WalkDir::new(game_path).max_depth(10).into_iter().flatten() {
        let p = entry.path();
        if !p.is_file() { continue; }
        if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
            if NVIDIA_DLLS.contains(&name.to_lowercase().as_str()) {
                found.push(p.to_path_buf());
            }
        }
    }
    if found.is_empty() {
        result.already_current.push("No NVIDIA DLLs found in game directory".to_string());
        return result;
    }

    // Process each DLL via the Python backend.
    for dll_path in found {
        let dll_name_lower = dll_path.file_name()
            .and_then(|n| n.to_str())
            .map(|s| s.to_lowercase())
            .unwrap_or_else(|| "dll".to_string());
        let current_ver = crate::scanner::get_dll_version(&dll_path);

        // Stage a fresh download via the Python module. `staged` lives in
        // LIBRARIES_DIR , the SAME directory `list_cached_dlls()` lists
        // for the per-game version-picker dropdown , so it must be left
        // in place, not consumed. Found live 2026-08-07: this previously
        // `rename`d (moved) the file into the game on success, or
        // `remove_file`d it outright when already-current, so EVERY DLL
        // `update_game_dlss` touched vanished from LIBRARIES_DIR right
        // after being downloaded into it , the dropdown showed "not in
        // cache" immediately after a successful "DLSS fetched + updated."
        // because Sync (step 1) and this step-2 cleanup were fighting
        // over the same directory for two different purposes (persistent
        // cache vs. scratch space). Copy into the game now; keep the
        // cache copy untouched either way.
        match python_download_dll(&dll_name_lower) {
            Err(e) => {
                result.errors.push(format!("{dll_name_lower}: {e}"));
            }
            Ok(staged) => {
                let new_ver = crate::scanner::get_dll_version(&staged);
                // An "Unknown" version reader result must never read as
                // "already current" , that silently skips every DLL whose
                // version resource this build's reader couldn't parse,
                // while still reporting overall success. Treat either side
                // being unreadable as needing the update; the game copy is
                // recoverable via the `.gdlss_original` snapshot below.
                let skip = current_ver != "Unknown" && new_ver != "Unknown"
                    && version_lex_compare(&current_ver, &new_ver) != std::cmp::Ordering::Less;
                if skip {
                    result.already_current.push(format!("{dll_name_lower} ({current_ver})"));
                    continue;
                }
                // Snapshot the file the GAME actually shipped, once, before
                // the very first update ever touches it , never overwritten
                // again after this. Found live 2026-08-07: the `.gdlss_bak`
                // slot below is overwritten on every update, so after two
                // updates the true shipped version was already gone with no
                // way back to it. `.gdlss_original` is the durable record;
                // `.gdlss_bak` remains what it always was, "one step back".
                // Confirmed live 2026-08-07: both backup copies previously
                // discarded their Result with `let _ = ...` , a failed
                // backup (disk full, permission denied) didn't stop the
                // swap below from proceeding, so a user could lose their
                // only recovery copy and the update would still report
                // "updated" as if everything was fine. Bail out instead;
                // the game's original DLL is still untouched at this point.
                let original = dll_path.with_extension("gdlss_original");
                if !original.exists() {
                    if let Err(e) = std::fs::copy(&dll_path, &original) {
                        result.errors.push(format!(
                            "{dll_name_lower}: failed to snapshot original ({e}) , update skipped"));
                        continue;
                    }
                }
                let bak = dll_path.with_extension("gdlss_bak");
                if let Err(e) = std::fs::copy(&dll_path, &bak) {
                    result.errors.push(format!(
                        "{dll_name_lower}: failed to write backup ({e}) , update skipped"));
                    continue;
                }
                // Swap via copy-to-temp-sibling + rename, not a direct
                // fs::copy onto dll_path. fs::copy truncates and writes the
                // destination in place , a failure partway (disk full,
                // permission revoked mid-write) leaves dll_path corrupted
                // even though the backup above already succeeded. rename()
                // on the same filesystem is a single atomic syscall: either
                // dll_path becomes the new file whole, or it's untouched.
                let tmp = dll_path.with_extension("gdlss_tmp");
                let swap_result = std::fs::copy(&staged, &tmp)
                    .and_then(|_| std::fs::rename(&tmp, &dll_path));
                match swap_result {
                    Ok(_) => {
                        result.updated.push(format!(
                            "{dll_name_lower} ({current_ver} → {new_ver})"));
                    }
                    Err(e) => {
                        let _ = std::fs::remove_file(&tmp);
                        result.errors.push(format!(
                            "{dll_name_lower}: failed to swap downloaded copy into place ({e})"));
                    }
                }
            }
        }
    }
    result
}

/// Compare two dotted version strings lexicographically by numeric
/// component (matches the original `compare_versions` semantics).
fn version_lex_compare(a: &str, b: &str) -> std::cmp::Ordering {
    let parse = |s: &str| -> Vec<u64> {
        s.split('.').map(|p| p.parse::<u64>().unwrap_or(0)).collect()
    };
    let av = parse(a); let bv = parse(b);
    let n = av.len().max(bv.len());
    for i in 0..n {
        let x = av.get(i).copied().unwrap_or(0);
        let y = bv.get(i).copied().unwrap_or(0);
        match x.cmp(&y) {
            std::cmp::Ordering::Equal => continue,
            other => return other,
        }
    }
    std::cmp::Ordering::Equal
}

/// Shell-out to `python3 -c "..."` that calls
/// gb_gaming.dlss_updater.download_latest() and prints the resulting
/// file path.  Returns the staged Path or an error string suitable to
/// surface verbatim in the UI.
fn python_download_dll(dll_name: &str) -> Result<PathBuf, String> {
    let bootstrap = crate::py_bootstrap::py_bootstrap();
    let dll_lit = serde_json::to_string(dll_name)
        .map_err(|e| e.to_string())?;
    let script = format!(r#"
{bootstrap}
from gb_gaming import dlss_updater as d
ok, payload = d.download_latest({dll_lit})
if ok:
    print("OK", str(payload))
else:
    print("ERR", payload)
"#);
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
    let stdout = String::from_utf8_lossy(&out.stdout);
    let first = stdout.lines().next().unwrap_or("").trim();
    if let Some(rest) = first.strip_prefix("OK ") {
        Ok(PathBuf::from(rest))
    } else if let Some(rest) = first.strip_prefix("ERR ") {
        Err(rest.to_string())
    } else {
        Err(format!("unexpected backend reply: {first:?}"))
    }
}

// ── PR-VVV: streaming Update / Restore for the InstallStreamModal ───
//
// The non-streaming `update_game_dlss` / `restore_game_dlss` paths run
// synchronously and return a result struct , fine for a CLI tool but
// the GUI has just adopted the streaming modal pattern for layer +
// Proton.  These wrappers narrate each step through a Tauri Channel
// so the user sees the same live-output modal experience for DLSS
// actions.
//
// We don't change the underlying logic , we wrap each DLL action in
// channel sends.  Exit code = 0 on full success, 1 if any errors.

use tauri::ipc::Channel;

/// PR-AAAA: per-game DLL picker , one entry per file in libraries/.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct CachedDll {
    pub name:       String,
    pub version:    String,
    pub sha256:     String,
    pub source:     String,
    pub fetched_at: u64,
    pub size_bytes: u64,
    // Multiple entries can now share the same `name` , one per cached
    // version (libraries/versions/<dll>/<version>.dll) , so the frontend
    // version picker needs the exact file path to tell them apart when
    // asking to install one.
    #[serde(default)]
    pub path: String,
}

pub fn list_cached_dlls_impl() -> Result<Vec<CachedDll>, String> {
    let script = format!(
        "{bootstrap}import json\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.list_cached_dlls()))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = std::process::Command::new("python3").args(["-c", &script]).output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!("python3 exited {}: {}", out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("bad JSON: {e}"))
}

pub fn install_cached_into_game_impl(
    dll_name: &str, game_path: &Path, version: Option<&str>,
) -> Result<String, String> {
    let game_lit = serde_json::to_string(game_path.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let dll_lit  = serde_json::to_string(dll_name).map_err(|e| e.to_string())?;
    // serde_json would emit `null`, not valid Python , map to the
    // literal `None` by hand for the None case.
    let version_lit = match version {
        Some(v) => serde_json::to_string(v).map_err(|e| e.to_string())?,
        None => "None".to_string(),
    };
    let script = format!(
        "{bootstrap}from pathlib import Path\nfrom gb_gaming import dlss_updater as d\nok, msg = d.install_cached_into_game({dll_lit}, Path({game_lit}), version={version_lit})\nprint((\"OK \" if ok else \"ERR \") + msg)\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = std::process::Command::new("python3").args(["-c", &script]).output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    // Confirmed: python_download_dll and list_cached_dlls_impl above both
    // check out.status.success() before trusting stdout; this function and
    // restore_single_dll_to_original_impl below didn't. A python3 process
    // that dies mid-script (e.g. an uncaught exception after partial print
    // output, or a segfault in a C extension) can still leave *something*
    // on stdout that fails to match "OK "/"ERR " and gets reported as the
    // generic "unexpected reply" , masking a real crash as a formatting
    // quirk instead of surfacing the actual interpreter failure.
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let first = stdout.lines().next().unwrap_or("").trim();
    if let Some(rest) = first.strip_prefix("OK ") { Ok(rest.to_string()) }
    else if let Some(rest) = first.strip_prefix("ERR ") { Err(rest.to_string()) }
    else { Err(format!("unexpected reply: {first:?}")) }
}

/// Restore ONE DLL from a specific backup file , the dropdown's
/// "Backup: vX (<date>)" choices, distinct from `restore_single_dll_to_
/// original_impl`'s single "Shipped: vX" option. `backup_path` comes from
/// the frontend verbatim (sourced from Python's scan, `restore_points` in
/// `DllFinding.to_dict()`), so it's validated as an actual sibling backup
/// of `dll_name` inside `game_path` before anything is touched , refuses
/// anything outside the game directory or not named like a backup this
/// module itself would have written (path-traversal guard).
pub fn restore_dll_from_backup_impl(
    dll_name: &str, game_path: &Path, backup_path: &str,
) -> Result<String, String> {
    let dll_lower = dll_name.to_lowercase();
    if !NVIDIA_DLLS.contains(&dll_lower.as_str()) {
        return Err(format!("not a recognised NVIDIA DLL: {dll_name}"));
    }

    let canon_game = std::fs::canonicalize(game_path)
        .map_err(|e| format!("game path: {e}"))?;
    let canon_backup = std::fs::canonicalize(backup_path)
        .map_err(|e| format!("backup not found: {e}"))?;
    if !canon_backup.starts_with(&canon_game) {
        return Err("backup path is outside the game directory".to_string());
    }
    let backup_name = canon_backup.file_name().and_then(|n| n.to_str()).unwrap_or("");
    let is_recognised_backup = backup_name.eq_ignore_ascii_case(&format!("{dll_name}.gdlss_bak"))
        || backup_name.to_lowercase().starts_with(&format!("{dll_lower}.bak."));
    if !is_recognised_backup {
        return Err(format!("{backup_name} is not a recognised backup of {dll_name}"));
    }

    let mut target: Option<PathBuf> = None;
    for entry in WalkDir::new(game_path).max_depth(10).into_iter().flatten() {
        let p = entry.path();
        if p.is_file() {
            if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                if name.eq_ignore_ascii_case(dll_name) {
                    target = Some(p.to_path_buf());
                    break;
                }
            }
        }
    }
    let target = target.ok_or_else(|| format!("{dll_name} not present in {}", game_path.display()))?;

    let tmp = target.with_extension("restore_tmp");
    std::fs::copy(&canon_backup, &tmp).map_err(|e| format!("failed to stage backup: {e}"))?;
    if let Err(e) = std::fs::rename(&tmp, &target) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("failed to swap in backup: {e}"));
    }
    Ok(format!("{dll_name} restored from {backup_name}"))
}

/// Restore ONE DLL to the version the game shipped with , scoped to just
/// this file, unlike `restore_game_dlss_to_original` above which restores
/// every DLL in the game at once. The dropdown's "Shipped: vX" choice.
pub fn restore_single_dll_to_original_impl(dll_name: &str, game_path: &Path)
    -> Result<String, String>
{
    let game_lit = serde_json::to_string(game_path.to_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let dll_lit  = serde_json::to_string(dll_name).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}from pathlib import Path\nfrom gb_gaming import dlss_updater as d\nok, msg = d.restore_single_dll_to_original({dll_lit}, Path({game_lit}))\nprint((\"OK \" if ok else \"ERR \") + msg)\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = std::process::Command::new("python3").args(["-c", &script]).output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)));
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let first = stdout.lines().next().unwrap_or("").trim();
    if let Some(rest) = first.strip_prefix("OK ") { Ok(rest.to_string()) }
    else if let Some(rest) = first.strip_prefix("ERR ") { Err(rest.to_string()) }
    else { Err(format!("unexpected reply: {first:?}")) }
}

pub fn update_game_dlss_streaming(
    game_path: &Path, channel: &Channel<String>
) -> Result<i32, String> {
    let _ = channel.send(format!("Scanning {} for NVIDIA upscaler DLLs…",
                                  game_path.display()));
    let result = update_game_dlss(game_path);
    for line in &result.updated {
        let _ = channel.send(format!("[updated]  {line}"));
    }
    for line in &result.already_current {
        let _ = channel.send(format!("[skipped]  {line}"));
    }
    for line in &result.errors {
        let _ = channel.send(format!("[error]    {line}"));
    }
    let summary = format!(
        "Done , {} updated, {} already current, {} errors.",
        result.updated.len(), result.already_current.len(),
        result.errors.len());
    let _ = channel.send(summary);
    Ok(if result.errors.is_empty() { 0 } else { 1 })
}

/// PR-VVV: populate the runtime cache at `libraries/` with every
/// DLL in KNOWN.  Streams per-DLL status via the Channel so users
/// see live progress while NVIDIA's GitHub releases / configured
/// nvngx mirror are queried.  Emits a version-diff summary at the end
/// showing old → new for each DLL so the user can see exactly what changed.
pub fn sync_dlss_library_streaming(channel: &Channel<String>)
    -> Result<i32, String>
{
    // Snapshot current cached versions before the sync.
    let before: std::collections::HashMap<String, String> =
        list_cached_dlls_impl().unwrap_or_default()
            .into_iter().map(|d| (d.name, d.version)).collect();

    let bootstrap = crate::py_bootstrap::py_bootstrap();
    let script = format!(r#"
{bootstrap}
from gb_gaming import dlss_updater as d
def _p(line):
    print(line, flush=True)
result = d.download_all_known(progress=_p)
ok = sum(1 for v in result.values() if v[0])
err = len(result) - ok
print(f"::summary:: ok={{ok}} err={{err}}", flush=True)
sys.exit(0 if err == 0 else 1)
"#);
    let mut child = std::process::Command::new("python3")
        .args(["-c", &script])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("python3 spawn failed: {e}"))?;

    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");
    let ch_out = channel.clone();
    let ch_err = channel.clone();
    let t_out = std::thread::spawn(move || {
        use std::io::{BufRead, BufReader};
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = ch_out.send(line);
        }
    });
    let t_err = std::thread::spawn(move || {
        use std::io::{BufRead, BufReader};
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = ch_err.send(format!("[py-err]  {line}"));
        }
    });
    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
    let _ = t_out.join();
    let _ = t_err.join();

    // Emit version-diff summary so the user can see old → new.
    let after = list_cached_dlls_impl().unwrap_or_default();
    if !after.is_empty() {
        let _ = channel.send(String::new());
        let _ = channel.send("─────────────────────────────────── version summary ───".to_string());
        for dll in &after {
            match before.get(&dll.name) {
                Some(old) if old == &dll.version =>
                    { let _ = channel.send(format!("[unchanged]  {}  {}", dll.name, dll.version)); }
                Some(old) =>
                    { let _ = channel.send(format!("[UPDATED]    {}  {} → {}", dll.name, old, dll.version)); }
                None =>
                    { let _ = channel.send(format!("[NEW]        {}  {}", dll.name, dll.version)); }
            }
        }
    }

    Ok(status.code().unwrap_or(-1))
}

/// Convenience wrapper: sync the library cache from NVIDIA GitHub, then
/// update the game's DLLs in one streaming operation.  Used by "Update DLSS"
/// in the game action bar so a single click always fetches the latest release.
pub fn sync_and_update_dlss_streaming(
    game_path: &Path, channel: &Channel<String>
) -> Result<i32, String> {
    let _ = channel.send("=== Step 1 / 2 , Fetching latest DLSS libraries from NVIDIA GitHub ===".to_string());
    let sync_code = sync_dlss_library_streaming(channel)?;
    if sync_code != 0 {
        let _ = channel.send(String::new());
        let _ = channel.send("[error]  Sync failed , skipping game DLL update.".to_string());
        return Ok(sync_code);
    }
    let _ = channel.send(String::new());
    let _ = channel.send(format!("=== Step 2 / 2 , Updating game DLLs in {} ===",
                                 game_path.display()));
    update_game_dlss_streaming(game_path, channel)
}

pub fn restore_game_dlss_streaming(
    game_path: &Path, channel: &Channel<String>
) -> Result<i32, String> {
    let _ = channel.send(format!("Looking for DLSS backups in {}…",
                                  game_path.display()));
    let result = restore_game_dlss(game_path);
    for line in &result.updated {
        let _ = channel.send(format!("[restored] {line}"));
    }
    for line in &result.already_current {
        let _ = channel.send(format!("[skipped]  {line}"));
    }
    for line in &result.errors {
        let _ = channel.send(format!("[error]    {line}"));
    }
    let summary = format!(
        "Done , {} restored, {} skipped, {} errors.",
        result.updated.len(), result.already_current.len(),
        result.errors.len());
    let _ = channel.send(summary);
    Ok(if result.errors.is_empty() { 0 } else { 1 })
}

pub fn restore_game_dlss_to_original_streaming(
    game_path: &Path, channel: &Channel<String>
) -> Result<i32, String> {
    let _ = channel.send(format!("Looking for the version {} shipped with…",
                                  game_path.display()));
    let result = restore_game_dlss_to_original(game_path);
    for line in &result.updated {
        let _ = channel.send(format!("[restored] {line}"));
    }
    for line in &result.already_current {
        let _ = channel.send(format!("[skipped]  {line}"));
    }
    for line in &result.errors {
        let _ = channel.send(format!("[error]    {line}"));
    }
    let summary = format!(
        "Done , {} restored to shipped version, {} skipped, {} errors.",
        result.updated.len(), result.already_current.len(),
        result.errors.len());
    let _ = channel.send(summary);
    Ok(if result.errors.is_empty() { 0 } else { 1 })
}
