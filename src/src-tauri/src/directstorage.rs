// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
//
// DirectStorage awareness , detection + diagnostics only. DirectStorage
// itself (including GPU-accelerated GDeflate decompression) is already
// implemented inside vkd3d-proton, the D3D12-on-Vulkan layer Proton ships
// (real support since vkd3d-proton v2.10, Sept 2023, confirmed shipping in
// e.g. Ratchet & Clank: Rift Apart). This module does not reimplement any
// of that , it answers three honest, checkable questions: does this game
// ship DirectStorage at all, is the Proton build that will actually launch
// it new enough to have that support, and does the install sit on storage
// fast enough (NVMe) for DirectStorage to have a real effect. No lever
// exists to force-enable/disable DirectStorage behavior itself , see
// enhance_gaming.md and the session plan for why one isn't fabricated here.

use std::path::{Path, PathBuf};
use regex::Regex;
use serde::{Serialize, Deserialize};
use walkdir::WalkDir;

const DIRECTSTORAGE_DLLS: &[&str] = &["dstorage.dll", "dstoragecore.dll"];

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct DirectStorageInfo {
    pub detected:      bool,
    pub dlls_found:    Vec<String>,
    /// None = couldn't confirm either way (unknown/unparseable Proton build).
    pub proton_capable: Option<bool>,
    pub proton_build:   String,
    /// None = couldn't determine the underlying storage device (network
    /// mount, exotic dm-mapper stack, etc.) , reported as "unknown" in the
    /// UI, never guessed.
    pub nvme_storage:   Option<bool>,
}

/// Walks `game_path` for DirectStorage DLLs , same shape as the
/// NVIDIA_DLLS scan in dlss.rs (WalkDir, max_depth 10, case-insensitive
/// filename match).
pub fn detect_directstorage(game_path: &Path) -> Vec<String> {
    let mut found = Vec::new();
    for entry in WalkDir::new(game_path).max_depth(10).into_iter().flatten() {
        let p = entry.path();
        if !p.is_file() { continue; }
        if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
            let lower = name.to_lowercase();
            if DIRECTSTORAGE_DLLS.contains(&lower.as_str()) {
                found.push(name.to_string());
            }
        }
    }
    found
}

/// Finds the best available Proton build across all Steam libraries and
/// judges whether it's new enough to carry vkd3d-proton's DirectStorage
/// support. Reuses `scanner::get_steam_libraries()` (already used for game
/// discovery) as the search roots, and mirrors the same
/// Valve-numbered/GE-Proton naming patterns the Python wrapper's own
/// `_find_proton_stable` matches (greenboost_proton/proton) , duplicated
/// here rather than shelled into, because that function depends on
/// STEAM_COMPAT_CLIENT_INSTALL_PATH/STEAM_COMPAT_LIBRARY_PATHS, env vars
/// Steam only sets at actual game-launch time and that don't exist when
/// this diagnostic runs standalone from the Tauri UI.
pub fn check_proton_directstorage_capable() -> (Option<bool>, String) {
    let valve_re = Regex::new(r"^Proton\s+(\d+)\.(\d+)$").unwrap();
    let ge_re = Regex::new(r"(\d+)[.\-_](\d+)(?:[.\-_]GE[.\-_]?(\d+))?").unwrap();

    let mut best: Option<(String, Option<bool>, (i64, i64, i64))> = None;
    let mut consider = |name: &str, capable: Option<bool>, key: (i64, i64, i64)| {
        let better = match &best {
            None => true,
            Some((_, _, best_key)) => key > *best_key,
        };
        if better {
            best = Some((name.to_string(), capable, key));
        }
    };

    for steamapps in crate::scanner::get_steam_libraries() {
        let steam_root = steamapps.parent().map(PathBuf::from);
        let mut candidate_dirs = vec![steamapps.join("common")];
        if let Some(root) = steam_root {
            candidate_dirs.push(root.join("compatibilitytools.d"));
        }
        for dir in candidate_dirs {
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if !entry.path().join("proton").is_file() { continue; }

                if name == "Proton - Experimental" {
                    // Always tracks upstream vkd3d-proton HEAD , confidently capable.
                    consider(&name, Some(true), (i64::MAX, 0, 0));
                    continue;
                }
                if let Some(caps) = valve_re.captures(&name) {
                    let major: i64 = caps[1].parse().unwrap_or(0);
                    let minor: i64 = caps[2].parse().unwrap_or(0);
                    // vkd3d-proton 2.10 (DirectStorage/GDeflate) landed Sept
                    // 2023; Proton 9.0 (Feb 2024) is comfortably past that
                    // pin. Earlier majors may or may not have picked it up
                    // via a point release , not confirmed either way, so
                    // report unknown rather than guess.
                    let capable = if major >= 9 { Some(true) } else { None };
                    consider(&name, capable, (major, minor, 0));
                    continue;
                }
                if let Some(caps) = ge_re.captures(&name) {
                    let major: i64 = caps[1].parse().unwrap_or(0);
                    let minor: i64 = caps[2].parse().unwrap_or(0);
                    let ge_n: i64 = caps.get(3).and_then(|m| m.as_str().parse().ok()).unwrap_or(0);
                    // GE-Proton tracks upstream vkd3d-proton aggressively;
                    // treated as capable, distinct from the Valve-numbered
                    // case above where the exact pin per point release
                    // isn't confirmed.
                    consider(&name, Some(true), (major, minor, ge_n));
                }
            }
        }
    }

    match best {
        Some((name, capable, _)) => (capable, name),
        None => (None, String::new()),
    }
}

/// Best-effort: is the block device backing `path` an NVMe drive?
/// Resolves the mount source via /proc/mounts (longest-prefix match against
/// the canonicalized path), then follows dm-mapper/LVM/LUKS through
/// /sys/class/block/<name>/slaves/ if present. Returns None (not a guess)
/// when the source isn't a local block device at all (network mount,
/// overlay, etc.) or the resolution chain can't be followed.
pub fn is_nvme_storage(path: &Path) -> Option<bool> {
    let canon = std::fs::canonicalize(path).ok()?;
    let mounts = std::fs::read_to_string("/proc/mounts").ok()?;

    let mut best_match: Option<(PathBuf, String)> = None;
    for line in mounts.lines() {
        let mut fields = line.split_whitespace();
        let source = fields.next()?;
        let mountpoint = fields.next()?;
        if !source.starts_with("/dev/") { continue; }
        let mp = PathBuf::from(mountpoint);
        if canon.starts_with(&mp) {
            let better = match &best_match {
                None => true,
                Some((cur, _)) => mp.components().count() > cur.components().count(),
            };
            if better { best_match = Some((mp, source.to_string())); }
        }
    }
    let (_, source) = best_match?;
    let dev_name = source.strip_prefix("/dev/")?;

    if dev_name.starts_with("nvme") { return Some(true); }

    if dev_name.starts_with("dm-") || source.starts_with("/dev/mapper/") {
        // LVM/LUKS: resolve to the real underlying device(s) and check those.
        let slaves_dir = PathBuf::from(format!("/sys/class/block/{dev_name}/slaves"));
        let real_name = if slaves_dir.is_dir() {
            dev_name.to_string()
        } else {
            // /dev/mapper/<name> is a symlink to the real /dev/dm-N node.
            std::fs::canonicalize(&source).ok()
                .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
                .unwrap_or_else(|| dev_name.to_string())
        };
        let slaves_dir = PathBuf::from(format!("/sys/class/block/{real_name}/slaves"));
        if let Ok(entries) = std::fs::read_dir(&slaves_dir) {
            for entry in entries.flatten() {
                if entry.file_name().to_string_lossy().starts_with("nvme") {
                    return Some(true);
                }
            }
            return Some(false);
        }
        return None;
    }

    // Plain partition (e.g. sda1) → strip trailing partition digits to get
    // the parent disk name for a definitive non-NVMe classification.
    let base: String = dev_name.trim_end_matches(|c: char| c.is_ascii_digit()).to_string();
    if base.is_empty() { return None; }
    Some(false)
}

pub fn get_directstorage_info(game_path: &Path) -> DirectStorageInfo {
    let dlls_found = detect_directstorage(game_path);
    let (proton_capable, proton_build) = check_proton_directstorage_capable();
    DirectStorageInfo {
        detected: !dlls_found.is_empty(),
        dlls_found,
        proton_capable,
        proton_build,
        nvme_storage: is_nvme_storage(game_path),
    }
}
