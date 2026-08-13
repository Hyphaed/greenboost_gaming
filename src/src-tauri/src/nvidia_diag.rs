// Copyright 2026 Ferran Duarri , GPL v2
// Clean detection of the NVIDIA driver/library version mismatch condition
// and package-manager-agnostic update checking.

use serde::{Serialize, Deserialize};
use std::fs;
use std::process::Command;

// ── Mismatch detection ────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct NvidiaMismatchInfo {
    pub loaded:  String,   // running kernel module version (595.58.03)
    pub on_disk: String,   // installed userspace library version (595.71.05)
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct NvidiaDiag {
    pub loaded_kmod_version:   Option<String>,
    pub userspace_lib_version: Option<String>,
    pub mismatch: bool,
}

pub fn read() -> NvidiaDiag {
    let loaded_kmod_version   = read_loaded_kmod_version();
    let userspace_lib_version = read_userspace_lib_version();

    let mismatch = match (&loaded_kmod_version, &userspace_lib_version) {
        (Some(kmod), Some(lib)) => major_minor(kmod) != major_minor(lib),
        _ => false,
    };

    NvidiaDiag { loaded_kmod_version, userspace_lib_version, mismatch }
}

/// Parse the running kernel module version from /proc/driver/nvidia/version.
/// Format varies between Open and proprietary banners, so scan for the first
/// version-like token (e.g. "595.71.05") instead of using a positional index.
fn read_loaded_kmod_version() -> Option<String> {
    let contents = fs::read_to_string("/proc/driver/nvidia/version").ok()?;
    let first_line = contents.lines().next()?;
    first_line
        .split_whitespace()
        .find(|t| looks_like_version(t))
        .map(|s| s.to_string())
}

fn looks_like_version(t: &str) -> bool {
    let mut parts = t.split('.');
    let a = parts.next();
    let b = parts.next();
    match (a, b) {
        (Some(x), Some(y)) if !x.is_empty() && !y.is_empty()
            && x.bytes().all(|c| c.is_ascii_digit())
            && y.bytes().all(|c| c.is_ascii_digit()) => true,
        _ => false,
    }
}

/// Resolve the canonical libnvidia-ml.so.1 symlink target to extract version.
fn read_userspace_lib_version() -> Option<String> {
    let search_dirs = [
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib",
        "/usr/lib64",
    ];

    for dir in &search_dirs {
        let symlink = format!("{dir}/libnvidia-ml.so.1");
        if let Ok(target) = fs::read_link(&symlink) {
            let name = target.file_name()?.to_str()?;
            if let Some(ver) = name.strip_prefix("libnvidia-ml.so.") {
                return Some(ver.to_string());
            }
        }
    }

    // Fallback: scan for versioned .so files.
    for dir in &search_dirs {
        if let Ok(entries) = fs::read_dir(dir) {
            let mut found: Vec<String> = entries
                .flatten()
                .filter_map(|e| {
                    let n = e.file_name();
                    let name = n.to_string_lossy().into_owned();
                    let ver = name.strip_prefix("libnvidia-ml.so.")?.to_string();
                    if ver.starts_with(|c: char| c.is_ascii_digit()) {
                        Some(ver)
                    } else {
                        None
                    }
                })
                .collect();
            if let Some(v) = found.pop() {
                return Some(v);
            }
        }
    }

    None
}

fn major_minor(v: &str) -> String {
    let mut parts = v.splitn(3, '.');
    let major = parts.next().unwrap_or("0");
    let minor = parts.next().unwrap_or("0");
    format!("{major}.{minor}")
}

// ── Package-manager update check ─────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct NvidiaUpdateStatus {
    /// True when a newer driver version is available from the package repos.
    pub update_available: bool,
    /// The available newer version string (e.g. "595.71.05-0ubuntu0.26.04.1").
    pub available_version: Option<String>,
    /// The currently installed package version.
    pub installed_version: Option<String>,
    /// Human-readable command to run the upgrade (shown in the UI tooltip).
    pub update_command: Option<String>,
    /// Package manager that was used ("apt", "dnf", "pacman", "unknown").
    pub source: String,
    /// False when no supported package manager was found or the check failed.
    pub checked: bool,
}

pub fn check_update() -> NvidiaUpdateStatus {
    if which::which("apt-cache").is_ok() {
        return check_update_apt();
    }
    if which::which("dnf").is_ok() {
        return check_update_dnf();
    }
    if which::which("pacman").is_ok() {
        return check_update_pacman();
    }
    NvidiaUpdateStatus {
        update_available: false, available_version: None, installed_version: None,
        update_command: None, source: "unknown".into(), checked: false,
    }
}

fn check_update_apt() -> NvidiaUpdateStatus {
    // `apt list --upgradable` reads only from the local package cache (fast, no
    // network fetch).  Output line format when upgradable:
    //   "pkg/suite version arch [upgradable from: old_ver]"
    let out = Command::new("apt")
        .args(["list", "--upgradable"])
        .env("LANG", "C")
        .env("DEBIAN_FRONTEND", "noninteractive")
        .output();

    let Ok(out) = out else {
        return NvidiaUpdateStatus {
            checked: false, source: "apt".into(), update_available: false,
            available_version: None, installed_version: None, update_command: None,
        };
    };

    let stdout = String::from_utf8_lossy(&out.stdout);
    for line in stdout.lines() {
        let ll = line.to_lowercase();
        if !ll.contains("nvidia") { continue; }
        // Pick the first NVIDIA entry; parse candidate version (field 1) and
        // installed version from the "[upgradable from: X]" trailer.
        let parts: Vec<&str> = line.split_whitespace().collect();
        let candidate = parts.get(1).map(|s| s.to_string());
        let installed = line.find("[upgradable from: ").and_then(|i| {
            let rest = &line[i + 18..];
            rest.find(']').map(|j| rest[..j].to_string())
        });
        return NvidiaUpdateStatus {
            update_available: true,
            available_version: candidate,
            installed_version: installed,
            update_command: Some("sudo apt-get upgrade".into()),
            source: "apt".into(),
            checked: true,
        };
    }

    // No NVIDIA upgradable , find the installed version from the package db.
    NvidiaUpdateStatus {
        update_available: false,
        available_version: None,
        installed_version: find_apt_installed_nvidia_version(),
        update_command: None,
        source: "apt".into(),
        checked: true,
    }
}

fn find_apt_installed_nvidia_version() -> Option<String> {
    let out = Command::new("dpkg").arg("-l").output().ok()?;
    let txt = String::from_utf8_lossy(&out.stdout);
    for line in txt.lines() {
        if !line.starts_with("ii") { continue; }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 3 { continue; }
        let name = parts[1].to_lowercase();
        if name.contains("libnvidia") && (name.contains("extra") || name.contains("utils"))
            && !name.contains("dev") && !name.contains("doc") {
            return Some(parts[2].to_string());
        }
    }
    None
}

fn check_update_dnf() -> NvidiaUpdateStatus {
    // `dnf check-update` exits 100 when updates are available, 0 when none.
    let out = Command::new("dnf")
        .args(["check-update", "--quiet"])
        .env("LANG", "C")
        .output();

    let Ok(out) = out else {
        return NvidiaUpdateStatus {
            checked: false, source: "dnf".into(), update_available: false,
            available_version: None, installed_version: None, update_command: None,
        };
    };

    let stdout = String::from_utf8_lossy(&out.stdout);
    for line in stdout.lines() {
        if !line.to_lowercase().contains("nvidia") { continue; }
        let parts: Vec<&str> = line.split_whitespace().collect();
        return NvidiaUpdateStatus {
            update_available: true,
            available_version: parts.get(1).map(|s| s.to_string()),
            installed_version: None,
            update_command: Some("sudo dnf upgrade".into()),
            source: "dnf".into(),
            checked: true,
        };
    }
    NvidiaUpdateStatus {
        update_available: false, available_version: None, installed_version: None,
        update_command: None, source: "dnf".into(), checked: true,
    }
}

fn check_update_pacman() -> NvidiaUpdateStatus {
    // `checkupdates` (pacman-contrib) lists available updates without modifying
    // the package db.  Exits 2 when no updates are available, 0 when there are.
    let out = Command::new("checkupdates")
        .env("LANG", "C")
        .output();

    let Ok(out) = out else {
        return NvidiaUpdateStatus {
            checked: false, source: "pacman".into(), update_available: false,
            available_version: None, installed_version: None, update_command: None,
        };
    };

    let stdout = String::from_utf8_lossy(&out.stdout);
    for line in stdout.lines() {
        if !line.to_lowercase().contains("nvidia") { continue; }
        // Format: "package old_ver -> new_ver"
        let parts: Vec<&str> = line.split_whitespace().collect();
        return NvidiaUpdateStatus {
            update_available: true,
            available_version: parts.get(3).map(|s| s.to_string()),
            installed_version: parts.get(1).map(|s| s.to_string()),
            update_command: Some("sudo pacman -Syu".into()),
            source: "pacman".into(),
            checked: true,
        };
    }
    NvidiaUpdateStatus {
        update_available: false, available_version: None, installed_version: None,
        update_command: None, source: "pacman".into(), checked: true,
    }
}

#[cfg(test)]
mod tests {
    use super::looks_like_version;

    fn extract(line: &str) -> Option<String> {
        line.split_whitespace().find(|t| looks_like_version(t)).map(String::from)
    }

    #[test]
    fn open_kernel_module_banner() {
        let s = "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  595.71.05  Release Build";
        assert_eq!(extract(s).as_deref(), Some("595.71.05"));
    }

    #[test]
    fn proprietary_banner() {
        let s = "NVRM version: NVIDIA UNIX x86_64 Kernel Module  570.86.16  Wed Jan 01 00:00:00 2025";
        assert_eq!(extract(s).as_deref(), Some("570.86.16"));
    }

    #[test]
    fn rejects_arch_tokens() {
        assert!(!looks_like_version("x86_64"));
        assert!(!looks_like_version("aarch64"));
        assert!(!looks_like_version("NVRM"));
        assert!(!looks_like_version("version:"));
    }
}
