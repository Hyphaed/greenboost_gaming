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
/// the canonicalized path), then hands off to `is_nvme_device` to walk any
/// dm-mapper stacking on top of it. Returns None (not a guess) when the
/// source isn't a local block device at all (network mount, overlay, etc.)
/// or the resolution chain can't be followed.
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
    is_nvme_device(&source, Path::new("/sys/class/block"))
}

/// Resolves a `/dev/...` mount source to whether it's ultimately backed by
/// NVMe. `sysfs_root` is parameterized (normally `/sys/class/block`) so the
/// slaves-walk below is testable against a fixture tree.
fn is_nvme_device(source: &str, sysfs_root: &Path) -> Option<bool> {
    let dev_name = source.strip_prefix("/dev/")?;
    if dev_name.starts_with("nvme") { return Some(true); }

    let real_name = if dev_name.starts_with("dm-") {
        dev_name.to_string()
    } else if source.starts_with("/dev/mapper/") {
        // /dev/mapper/<name> is a symlink to the real /dev/dm-N node.
        std::fs::canonicalize(source).ok()
            .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))?
    } else {
        // Plain partition or disk (e.g. sda1) , not a mapper node, so there's
        // no slaves chain underneath it to walk.
        return Some(false);
    };

    walk_slaves(&real_name, sysfs_root, 0, &mut std::collections::HashSet::new())
}

/// Recursively follows a mapper device's `slaves/` directory , a stacked
/// setup (LVM on top of a LUKS crypt target, for example) can be more than
/// one layer deep, and checking only the immediate slave misclassifies NVMe
/// storage sitting one hop further down as "not NVMe". Depth-limited and
/// cycle-guarded so a malformed sysfs tree can't loop forever.
///
/// Returns Some(true) on the first `nvme*` name found anywhere in the
/// chain, Some(false) if the walk completes with none, None if a slaves
/// directory is missing partway through (chain broke) or the depth cap hit.
fn walk_slaves(
    name: &str,
    sysfs_root: &Path,
    depth: u32,
    visited: &mut std::collections::HashSet<String>,
) -> Option<bool> {
    const MAX_DEPTH: u32 = 8;
    if depth > MAX_DEPTH { return None; }
    if !visited.insert(name.to_string()) { return Some(false); } // cycle guard

    let slaves_dir = sysfs_root.join(name).join("slaves");
    let entries = std::fs::read_dir(&slaves_dir).ok()?;

    let mut found_any = false;
    for entry in entries.flatten() {
        found_any = true;
        let slave_name = entry.file_name().to_string_lossy().to_string();
        if slave_name.starts_with("nvme") { return Some(true); }
        if let Some(true) = walk_slaves(&slave_name, sysfs_root, depth + 1, visited) {
            return Some(true);
        }
    }
    if found_any { Some(false) } else { None }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// A throwaway directory under the real temp dir, unique per test so
    /// parallel `cargo test` runs never collide. Not a fixture crate , just
    /// enough std::fs to build a fake /sys/class/block tree.
    struct TmpDir(PathBuf);
    impl TmpDir {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "gb_directstorage_test_{tag}_{}_{:?}",
                std::process::id(),
                std::thread::current().id(),
            ));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            TmpDir(dir)
        }
        fn path(&self) -> &Path { &self.0 }
    }
    impl Drop for TmpDir {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    /// `slaves/` entries are normally symlinks to the real device's sysfs
    /// dir; the walk only reads the entry's file name, so a plain empty
    /// file stands in fine.
    fn make_block_dev(root: &Path, name: &str, slaves: &[&str]) {
        let slaves_dir = root.join(name).join("slaves");
        fs::create_dir_all(&slaves_dir).unwrap();
        for s in slaves {
            fs::write(slaves_dir.join(s), b"").unwrap();
        }
    }

    #[test]
    fn direct_nvme_partition_is_nvme() {
        // Name-prefix short-circuit , no sysfs walk needed at all.
        assert_eq!(is_nvme_device("/dev/nvme0n1p3", Path::new("/nonexistent")), Some(true));
    }

    #[test]
    fn plain_sata_partition_is_not_nvme() {
        assert_eq!(is_nvme_device("/dev/sda1", Path::new("/nonexistent")), Some(false));
    }

    #[test]
    fn lvm_on_luks_on_nvme_is_nvme() {
        // Real shape confirmed on this machine: LVM (dm-1) on a LUKS crypt
        // target (dm-0) on an NVMe partition. A single-level slaves check
        // stops at dm-0 and misreports "not NVMe"; the walk must recurse.
        let tmp = TmpDir::new("lvm_on_luks_on_nvme");
        make_block_dev(tmp.path(), "dm-1", &["dm-0"]);
        make_block_dev(tmp.path(), "dm-0", &["nvme0n1p3"]);
        assert_eq!(is_nvme_device("/dev/dm-1", tmp.path()), Some(true));
    }

    #[test]
    fn dm_on_sata_is_not_nvme() {
        let tmp = TmpDir::new("dm_on_sata");
        make_block_dev(tmp.path(), "dm-0", &["sda1"]);
        assert_eq!(is_nvme_device("/dev/dm-0", tmp.path()), Some(false));
    }

    #[test]
    fn broken_chain_is_unknown() {
        // dm-1's slaves dir is missing entirely , resolution can't complete.
        let tmp = TmpDir::new("broken_chain");
        assert_eq!(is_nvme_device("/dev/dm-1", tmp.path()), None);
    }

    #[test]
    fn cycle_does_not_hang() {
        let tmp = TmpDir::new("cycle");
        make_block_dev(tmp.path(), "dm-0", &["dm-1"]);
        make_block_dev(tmp.path(), "dm-1", &["dm-0"]);
        assert_eq!(is_nvme_device("/dev/dm-0", tmp.path()), Some(false));
    }
}
