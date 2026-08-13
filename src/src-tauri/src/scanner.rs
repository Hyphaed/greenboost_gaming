// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

use std::path::{Path, PathBuf};
use std::collections::{HashMap, HashSet};
use walkdir::WalkDir;
use regex::Regex;
use serde::{Serialize, Deserialize};
use crate::optimizer::GameSetting;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct GameDll {
    pub name: String,
    pub path: String,
    pub version: String,
    pub tech_type: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Game {
    pub name: String,
    pub path: String,
    pub appid: Option<String>,
    pub image: Option<String>,
    pub dlls: Vec<GameDll>,
    pub optimizations: Vec<GameSetting>,
    pub is_optimized: bool,
    pub has_backup: bool,
}

// ── Hidden games ──────────────────────────────────────────────────────
// scan_games() is a live filesystem walk with no persisted cache , an
// uninstalled game normally just stops appearing on the next scan. But
// Steam frequently leaves an orphaned (near-)empty directory behind after
// uninstall, which keeps matching the walk forever with no way to clear
// it from disk alone. This lets the user dismiss an entry from the Suite
// without needing to touch the filesystem; keyed on the same `path` used
// as the dedup key in scan_games() below.

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct HiddenGame {
    pub path: String,
    pub name: String,
}

fn hidden_games_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".config").join("greenboost-gaming").join("hidden_games.json")
}

pub fn load_hidden_games() -> Vec<HiddenGame> {
    std::fs::read_to_string(hidden_games_path()).ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_hidden_games(list: &[HiddenGame]) -> Result<(), String> {
    let path = hidden_games_path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("mkdir config dir: {e}"))?;
    }
    let json = serde_json::to_string_pretty(list).map_err(|e| format!("serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write hidden_games.json: {e}"))
}

pub fn hide_game_impl(path: String, name: String) -> Result<(), String> {
    let mut list = load_hidden_games();
    if !list.iter().any(|g| g.path == path) {
        list.push(HiddenGame { path, name });
        save_hidden_games(&list)?;
    }
    Ok(())
}

pub fn unhide_game_impl(path: String) -> Result<(), String> {
    let mut list = load_hidden_games();
    list.retain(|g| g.path != path);
    save_hidden_games(&list)
}

/// NVIDIA DLL filename -> display type, shared by every scanner (Steam,
/// Heroic, Lutris). Was duplicated three times as a 7-entry literal that
/// only had the original DLSS + core Streamline DLLs , missing
/// sl.dlss_d.dll, sl.interposer.dll, sl.nis.dll, sl.pcl.dll, the same four
/// dlss.rs::NVIDIA_DLLS and dlss_updater.py's KNOWN registry have tracked
/// and updated for a while. A game shipping only those four would have had
/// its DLLs actually updated by "Update DLSS" (that path already used the
/// full list) but never SHOWN in its own detected-DLL list. One shared
/// source of truth so the three scanners can't drift again.
fn nvidia_dll_target_map() -> HashMap<&'static str, &'static str> {
    [
        ("nvngx_dlss.dll",    "DLSS Super Resolution"),
        ("nvngx_dlssg.dll",   "DLSS Frame Generation"),
        ("nvngx_dlssd.dll",   "DLSS Ray Reconstruction"),
        ("sl.common.dll",     "Streamline Core"),
        ("sl.dlss.dll",       "Streamline DLSS SR"),
        ("sl.dlss_g.dll",     "Streamline DLSS FG"),
        ("sl.dlss_d.dll",     "Streamline DLSS RR"),
        ("sl.reflex.dll",     "Streamline Reflex"),
        ("sl.interposer.dll", "Streamline Interposer"),
        ("sl.nis.dll",        "Streamline NIS"),
        ("sl.pcl.dll",        "Streamline PCL"),
    ].iter().cloned().collect()
}

pub fn game_has_backup(game_path: &Path) -> bool {
    use crate::optimizer::find_config_files;
    let files = find_config_files(game_path);
    files.iter().any(|p| {
        let bak = PathBuf::from(format!("{}.gbak", p.to_string_lossy()));
        bak.exists()
    })
}

/// Was hard-coded `false` at every call site , a dead field nothing read.
/// Computed the same way the Games view's "Optimized"/"Not optimized"
/// badge is (no setting in scan_game_settings needs_change), so the Games
/// list can badge a game without a separate per-game settings scan.
/// detect_hardware_tier() is memoized (optimizer.rs), so this doesn't add
/// a second nvidia-smi spawn on top of the walk game_has_backup already
/// does during a library scan.
pub fn game_is_optimized(game_path: &Path) -> bool {
    crate::optimizer::scan_game_settings(game_path).iter()
        .all(|g| g.settings.iter().all(|s| !s.needs_change))
}

pub fn get_steam_libraries() -> Vec<PathBuf> {
    let mut libraries = Vec::new();
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() { return libraries; }

    let roots = vec![
        PathBuf::from(&home).join(".local/share/Steam"),
        PathBuf::from(&home).join(".steam/steam"),
    ];

    for root in roots {
        if root.exists() {
            let steamapps = root.join("steamapps");
            if steamapps.exists() {
                libraries.push(steamapps.clone());

                let vdf_path = steamapps.join("libraryfolders.vdf");
                if vdf_path.exists() {
                    if let Ok(content) = std::fs::read_to_string(vdf_path) {
                        let re = Regex::new(r#""path"\s+"([^"]+)""#).unwrap();
                        for cap in re.captures_iter(&content) {
                            let lib_path = PathBuf::from(&cap[1]).join("steamapps");
                            if lib_path.exists() && !libraries.contains(&lib_path) {
                                libraries.push(lib_path);
                            }
                        }
                    }
                }
            }
        }
    }
    // Deduplicate by canonical (resolved) path to avoid scanning symlinked duplicates
    // (e.g. ~/.local/share/Steam and ~/.steam/steam often point to the same directory)
    let mut seen: HashSet<PathBuf> = HashSet::new();
    libraries.retain(|p| {
        let canon = std::fs::canonicalize(p).unwrap_or_else(|_| p.clone());
        seen.insert(canon)
    });

    libraries
}

/// Reads the real Win32 `VS_FIXEDFILEINFO.dwFileVersion{MS,LS}` out of a PE's
/// version resource, instead of grep-ing for the first `\d.\d.\d`-looking
/// string in the binary (the old `strings`-based approach , unreliable
/// because DLLs embed plenty of unrelated numeric strings, e.g. SDK/build
/// tags, that happen to match a version-shaped pattern and get picked up
/// first). The signature `0xFEEF04BD` (little-endian bytes `BD 04 EF FE`)
/// marks the start of `VS_FIXEDFILEINFO`, but that exact byte pattern can
/// also occur by coincidence in unrelated binary data, so every occurrence
/// is checked and only one with `dwStrucVersion == 0x00010000` (the fixed
/// version-resource format tag) is accepted , matches the validation added
/// to the Python reader (`gb_gaming/dlss_updater.py::_read_pe_fileversion`).
pub fn get_dll_version(path: &Path) -> String {
    match read_pe_fileversion(path) {
        Some((a, b, c, d)) => format!("{a}.{b}.{c}.{d}"),
        None => "Unknown".to_string(),
    }
}

fn read_pe_fileversion(path: &Path) -> Option<(u16, u16, u16, u16)> {
    let data = std::fs::read(path).ok()?;
    const SIG: [u8; 4] = [0xBD, 0x04, 0xEF, 0xFE];
    let mut start = 0usize;
    while let Some(rel) = data[start..].windows(4).position(|w| w == SIG) {
        let idx = start + rel;
        if idx + 16 <= data.len() {
            let struc_version = u32::from_le_bytes(data[idx + 4..idx + 8].try_into().ok()?);
            if struc_version == 0x0001_0000 {
                let ms = u32::from_le_bytes(data[idx + 8..idx + 12].try_into().ok()?);
                let ls = u32::from_le_bytes(data[idx + 12..idx + 16].try_into().ok()?);
                return Some((
                    (ms >> 16) as u16, (ms & 0xFFFF) as u16,
                    (ls >> 16) as u16, (ls & 0xFFFF) as u16,
                ));
            }
        }
        start = idx + 1;
    }
    None
}

/// Find a game's box art in Steam's local library cache, preferring the
/// widescreen hero banner (what `GameHeroBanner` renders) over smaller
/// variants, and only falling back to a network CDN fetch if nothing is
/// cached locally at all.
///
/// Confirmed live 2026-08-07 this was returning a URL that never actually
/// resolved to real art for either game tested: the old flat-file pattern
/// `librarycache/<appid>_header.jpg` is LEGACY , current Steam clients
/// cache art under `librarycache/<appid>/<content-hash>/<name>.jpg`, one
/// hash-named subdirectory per asset, filenames stable
/// (`library_hero.jpg` 1920x620, `library_header.jpg` 460x215,
/// `library_capsule.jpg` 300x450, `logo.png`) but the hash directory name
/// is NOT derivable from the appid, so it has to be discovered by walking
/// the directory. Neither legacy flat file existed for either game tested
/// (both real, both installed), so every prior load silently skipped
/// straight to the CDN fallback , explains the "showed once then never
/// again" symptom: that fallback depended on the webview's network fetch
/// succeeding on every single render, with no local cache to fall back on
/// when it didn't.
pub fn get_steam_image(appid: &str) -> Option<String> {
    let home = std::env::var("HOME").unwrap_or_default();
    // Priority order: widest/highest-res first , a wide image still crops
    // fine into the small list thumbnail via CSS, but a portrait/small one
    // looks wrong stretched across the hero banner.
    const WANTED: &[&str] = &["library_hero.jpg", "library_header.jpg", "library_capsule.jpg", "logo.png"];

    for root in ["Steam", ".steam/steam"] {
        let dir = PathBuf::from(&home).join(if root == "Steam" { ".local/share/Steam" } else { root })
            .join("appcache/librarycache").join(appid);
        if !dir.is_dir() { continue; }
        // Each asset lives one hash-named subdirectory down; the hash isn't
        // derivable from the appid, so scan for it by filename.
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        let subdirs: Vec<PathBuf> = entries.flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        for wanted in WANTED {
            for sub in &subdirs {
                let candidate = sub.join(wanted);
                if candidate.is_file() {
                    return Some(candidate.to_string_lossy().to_string());
                }
            }
        }
    }
    // Nothing cached locally yet , fall back to Steam's CDN, same asset
    // name so the shape actually matches the hero banner.
    Some(format!("https://cdn.akamai.steamstatic.com/steam/apps/{}/library_hero.jpg", appid))
}

/// Scan games installed via Heroic Games Launcher (Epic via Legendary + GOG).
pub fn scan_heroic_games() -> Vec<Game> {
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() { return Vec::new(); }

    let target_dlls: HashMap<&str, &str> = nvidia_dll_target_map();

    let mut games: Vec<Game> = Vec::new();

    // ── 1. Legendary (Epic) installed.json ──────────────────────────────────
    let legendary_paths = vec![
        PathBuf::from(&home).join(".config/heroic/legendaryConfig/legendary/installed.json"),
        PathBuf::from(&home).join(".config/legendary/installed.json"),
    ];

    for json_path in &legendary_paths {
        if !json_path.exists() { continue; }
        let Ok(content) = std::fs::read_to_string(json_path) else { continue };
        let Ok(root) = serde_json::from_str::<serde_json::Value>(&content) else { continue };
        let Some(obj) = root.as_object() else { continue };

        for (_key, entry) in obj {
            let title = match entry.get("title").and_then(|v| v.as_str()) {
                Some(t) => t.to_string(),
                None => continue,
            };
            let install_path = match entry.get("install_path").and_then(|v| v.as_str()) {
                Some(p) if !p.is_empty() => p.to_string(),
                _ => continue,
            };
            let app_name = entry.get("app_name").and_then(|v| v.as_str())
                .unwrap_or("").to_string();

            let game_dir = PathBuf::from(&install_path);
            if !game_dir.exists() { continue; }

            let mut found_dlls = Vec::new();
            for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().flatten() {
                let f_path = walk_entry.path();
                if !f_path.is_file() { continue; }
                if let Some(f_name) = f_path.file_name().and_then(|n| n.to_str()) {
                    let f_low = f_name.to_lowercase();
                    if let Some(&tech) = target_dlls.get(f_low.as_str()) {
                        found_dlls.push(GameDll {
                            name: f_name.to_string(),
                            path: f_path.to_string_lossy().to_string(),
                            version: get_dll_version(f_path),
                            tech_type: tech.to_string(),
                        });
                    }
                }
            }

            games.push(Game {
                name: title,
                path: install_path,
                appid: if app_name.is_empty() { None } else { Some(app_name) },
                image: None,
                dlls: found_dlls,
                optimizations: Vec::new(),
                is_optimized: game_is_optimized(&game_dir),
                has_backup: game_has_backup(&game_dir),
            });
        }
    }

    // ── 2. Heroic GOG installed.json ─────────────────────────────────────────
    let gog_path = PathBuf::from(&home).join(".config/heroic/gog_store/installed.json");
    if gog_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&gog_path) {
            if let Ok(root) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(list) = root.get("installed").and_then(|v| v.as_array()) {
                    for entry in list {
                        let title = match entry.get("title").and_then(|v| v.as_str()) {
                            Some(t) => t.to_string(),
                            None => continue,
                        };
                        let install_path = match entry.get("install_path").and_then(|v| v.as_str()) {
                            Some(p) if !p.is_empty() => p.to_string(),
                            _ => continue,
                        };
                        let app_name = entry.get("appName").and_then(|v| v.as_str())
                            .unwrap_or("").to_string();

                        let game_dir = PathBuf::from(&install_path);
                        if !game_dir.exists() { continue; }

                        let mut found_dlls = Vec::new();
                        for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().flatten() {
                            let f_path = walk_entry.path();
                            if !f_path.is_file() { continue; }
                            if let Some(f_name) = f_path.file_name().and_then(|n| n.to_str()) {
                                let f_low = f_name.to_lowercase();
                                if let Some(&tech) = target_dlls.get(f_low.as_str()) {
                                    found_dlls.push(GameDll {
                                        name: f_name.to_string(),
                                        path: f_path.to_string_lossy().to_string(),
                                        version: get_dll_version(f_path),
                                        tech_type: tech.to_string(),
                                    });
                                }
                            }
                        }

                        games.push(Game {
                            name: title,
                            path: install_path,
                            appid: if app_name.is_empty() { None } else { Some(app_name) },
                            image: None,
                            dlls: found_dlls,
                            optimizations: Vec::new(),
                            is_optimized: game_is_optimized(&game_dir),
                            has_backup: game_has_backup(&game_dir),
                        });
                    }
                }
            }
        }
    }

    games
}

/// Scan games managed by Lutris.
///
/// `rusqlite` is not available, so we use the YAML config files under
/// `~/.config/lutris/games/`.  Each file contains an `exe:` line whose
/// value is an absolute path; the surrounding prefix directory (or the
/// parent of the exe) is used as the game directory.
pub fn scan_lutris_games() -> Vec<Game> {
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() { return Vec::new(); }

    let target_dlls: HashMap<&str, &str> = nvidia_dll_target_map();

    let games_dir = PathBuf::from(&home).join(".config/lutris/games");
    if !games_dir.exists() { return Vec::new(); }

    // Regexes for the YAML fields we care about (no YAML parser dependency).
    let re_name    = Regex::new(r"(?m)^name:\s*(.+)$").unwrap();
    let re_exe     = Regex::new(r"(?m)^\s+exe:\s*(/[^\s#]+)").unwrap();
    let re_prefix  = Regex::new(r"(?m)^\s+prefix:\s*(/[^\s#]+)").unwrap();
    let re_game_id = Regex::new(r"(?m)^game_id:\s*(.+)$").unwrap();

    let mut games: Vec<Game> = Vec::new();

    let Ok(entries) = std::fs::read_dir(&games_dir) else { return Vec::new() };
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(ext) = path.extension().and_then(|e| e.to_str()) else { continue };
        if ext != "yml" && ext != "yaml" { continue; }

        let Ok(content) = std::fs::read_to_string(&path) else { continue };

        // Game title , use filename stem as fallback
        let name: String = re_name.captures(&content)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().trim().to_string())
            .unwrap_or_else(|| {
                path.file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("Unknown")
                    .to_string()
            });

        let game_id: Option<String> = re_game_id.captures(&content)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().trim().to_string());

        // Prefer the Wine prefix as the game root; fall back to the exe's parent dir.
        let game_dir: Option<PathBuf> = re_prefix.captures(&content)
            .and_then(|c| c.get(1))
            .map(|m| PathBuf::from(m.as_str().trim()))
            .filter(|p| p.exists())
            .or_else(|| {
                re_exe.captures(&content)
                    .and_then(|c| c.get(1))
                    .and_then(|m| PathBuf::from(m.as_str().trim()).parent().map(|p| p.to_path_buf()))
                    .filter(|p| p.exists())
            });

        let game_dir = match game_dir {
            Some(d) => d,
            None => continue,
        };

        let mut found_dlls = Vec::new();
        for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().flatten() {
            let f_path = walk_entry.path();
            if !f_path.is_file() { continue; }
            if let Some(f_name) = f_path.file_name().and_then(|n| n.to_str()) {
                let f_low = f_name.to_lowercase();
                if let Some(&tech) = target_dlls.get(f_low.as_str()) {
                    found_dlls.push(GameDll {
                        name: f_name.to_string(),
                        path: f_path.to_string_lossy().to_string(),
                        version: get_dll_version(f_path),
                        tech_type: tech.to_string(),
                    });
                }
            }
        }

        games.push(Game {
            name,
            path: game_dir.to_string_lossy().to_string(),
            appid: game_id,
            image: None,
            dlls: found_dlls,
            optimizations: Vec::new(),
            is_optimized: game_is_optimized(&game_dir),
            has_backup: game_has_backup(&game_dir),
        });
    }

    games
}

/// Returns a map from Steam AppID (as String) to game name, built by
/// reading appmanifest_*.acf files in every Steam library.
/// Fast: only parses ACF metadata, doesn't scan game directories.
pub fn steam_game_name_map() -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    let name_re = Regex::new(r#""name"\s+"([^"]+)""#).unwrap();
    let appid_re = Regex::new(r#""appid"\s+"([^"]+)""#).unwrap();
    for lib in get_steam_libraries() {
        let Ok(entries) = std::fs::read_dir(&lib) else { continue };
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname_str = fname.to_string_lossy();
            if fname_str.starts_with("appmanifest_") && fname_str.ends_with(".acf") {
                let Ok(content) = std::fs::read_to_string(entry.path()) else { continue };
                if let (Some(n_cap), Some(id_cap)) = (name_re.captures(&content), appid_re.captures(&content)) {
                    map.insert(id_cap[1].to_string(), n_cap[1].to_string());
                }
            }
        }
    }
    map
}

/// Returns a map from the on-disk install directory name (ACF's
/// "installdir" field) to Steam AppID, across every Steam library.
///
/// Keyed by installdir, NOT the ACF "name" field , found live 2026-08-07:
/// "The First Berserker: Khazan" (name, has a colon) installs into a
/// directory literally named "The First Berserker Khazan" (installdir,
/// colon stripped by Steam). scan_games() below looks up this map using
/// the real directory name (entry.file_name()), which only ever matches
/// installdir , keying by "name" silently dropped the appid (and with it
/// the Launch button, GreenBoost overrides panel, and box art) for any
/// game whose display name and installdir diverge, which is common
/// whenever the title has punctuation Steam won't put in a path.
pub fn installdir_appid_map() -> HashMap<String, String> {
    let mut map = HashMap::new();
    let installdir_re = Regex::new(r#""installdir"\s+"([^"]+)""#).unwrap();
    let appid_re = Regex::new(r#""appid"\s+"([^"]+)""#).unwrap();
    for lib in get_steam_libraries() {
        let Ok(entries) = std::fs::read_dir(&lib) else { continue };
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname_str = fname.to_string_lossy();
            if fname_str.starts_with("appmanifest_") && fname_str.ends_with(".acf") {
                let Ok(content) = std::fs::read_to_string(entry.path()) else { continue };
                if let (Some(dir_cap), Some(id_cap)) = (installdir_re.captures(&content), appid_re.captures(&content)) {
                    map.insert(dir_cap[1].to_string(), id_cap[1].to_string());
                }
            }
        }
    }
    map
}

pub fn scan_games() -> Vec<Game> {
    let libraries = get_steam_libraries();
    let mut games: HashMap<String, Game> = HashMap::new();

    let target_dlls: HashMap<&str, &str> = nvidia_dll_target_map();
    let appid_map = installdir_appid_map();

    for lib in libraries {
        let common = lib.join("common");
        if !common.exists() { continue; }

        let Ok(entries) = std::fs::read_dir(common) else { continue };
        for entry in entries.flatten() {
            let game_dir = entry.path();
            if !game_dir.is_dir() { continue; }

            let game_name = entry.file_name().to_string_lossy().to_string();

            // Skip Proton runtimes, Steam tools, and non-game directories
            let name_lower = game_name.to_lowercase();
            let is_non_game = name_lower.starts_with("proton")
                || name_lower.starts_with("steamlinuxruntime")
                || name_lower.contains("steam controller")
                || name_lower.contains("steamworks shared")
                || name_lower.contains("easyanticheat runtime")
                || name_lower.contains("hotfix")
                || name_lower == "steam.dll"
                || name_lower.contains("redistributable");
            if is_non_game { continue; }
            let mut found_dlls = Vec::new();

            // Scan for NVIDIA tech DLLs (limited depth for speed)
            for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().flatten() {
                let f_path = walk_entry.path();
                if !f_path.is_file() { continue; }
                if let Some(f_name) = f_path.file_name().and_then(|n| n.to_str()) {
                    let f_low = f_name.to_lowercase();
                    if let Some(&tech) = target_dlls.get(f_low.as_str()) {
                        found_dlls.push(GameDll {
                            name: f_name.to_string(),
                            path: f_path.to_string_lossy().to_string(),
                            version: get_dll_version(f_path),
                            tech_type: tech.to_string(),
                        });
                    }
                }
            }

            let appid = appid_map.get(&game_name).cloned();
            let image = appid.as_ref().and_then(|id| get_steam_image(id));

            let has_bak = game_has_backup(&game_dir);
            games.insert(game_dir.to_string_lossy().to_string(), Game {
                name: game_name,
                path: game_dir.to_string_lossy().to_string(),
                appid,
                image,
                dlls: found_dlls,
                optimizations: Vec::new(),
                is_optimized: game_is_optimized(&game_dir),
                has_backup: has_bak,
            });
        }
    }

    // Merge Heroic and Lutris games (deduplicate by path)
    for g in scan_heroic_games().into_iter().chain(scan_lutris_games()) {
        games.entry(g.path.clone()).or_insert(g);
    }

    let hidden: HashSet<String> = load_hidden_games().into_iter().map(|g| g.path).collect();
    let mut result: Vec<Game> = games.into_values()
        .filter(|g| !hidden.contains(&g.path))
        .collect();
    result.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    result
}
