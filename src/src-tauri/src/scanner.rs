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
    // Whether a Steam manifest with StateFlags bit 0x4 ("fully installed")
    // backs this entry. false for a directory Steam's uninstaller left
    // behind , real incident 2026-08-31: FF7 Rebirth and FF16 both kept
    // showing as playable games after being uninstalled, because the old
    // scan only checked "is this a directory under common/", never whether
    // Steam still considers it installed. Heroic/Lutris/shortcut entries
    // are always true , those scanners already check their own source
    // (installed.json / prefix / shortcut target) for existence.
    pub installed: bool,
    // Size in bytes of a not-installed entry's leftover files, so the user
    // can see what they'd reclaim by deleting it. None when installed.
    pub leftover_bytes: Option<u64>,
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
    // Added 2026-08-31. Absent (None, via serde default) on every entry
    // written before this field existed. Lets scan_games() tell "hidden
    // because it was an orphaned leftover" (no appid at hide time , should
    // be reconsidered once a manifest reappears) apart from "hidden because
    // the user doesn't want to see an installed game" (has an appid , stays
    // hidden regardless of manifest state). See the self-heal pass in
    // scan_games() for why this matters: Khazan and FF16 were both hidden
    // on 2026-08-22 while uninstalled leftovers, and stayed hidden forever
    // after Khazan was reinstalled, because the old filter had no expiry.
    #[serde(default)]
    pub appid: Option<String>,
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

pub fn hide_game_impl(path: String, name: String, appid: Option<String>) -> Result<(), String> {
    let mut list = load_hidden_games();
    if !list.iter().any(|g| g.path == path) {
        list.push(HiddenGame { path, name, appid });
        save_hidden_games(&list)?;
    }
    Ok(())
}

pub fn unhide_game_impl(path: String) -> Result<(), String> {
    let mut list = load_hidden_games();
    list.retain(|g| g.path != path);
    save_hidden_games(&list)
}

/// Whether a hidden entry should stay hidden, given whether Steam now
/// considers its path fully installed. An entry hidden with no `appid`
/// recorded was hidden while it was an uninstalled leftover (either it
/// predates the `appid` field and is always `None`, or `hide_game_impl` saw
/// no appid at hide time) , if a manifest now backs that path, the reason
/// it was hidden no longer holds, so it should reappear. An entry hidden
/// WITH an appid was a deliberate "I don't want to see this installed
/// game" and stays hidden regardless of manifest state.
///
/// Real incident 2026-08-31: Khazan and FINAL FANTASY XVI were both hidden
/// on 2026-08-22 while uninstalled leftovers. Khazan was later reinstalled
/// but stayed invisible , the old filter had no expiry at all.
fn should_stay_hidden(hidden: &HiddenGame, now_installed: bool) -> bool {
    !(hidden.appid.is_none() && now_installed)
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
        // DLSS 5 Neural Rendering + the FF7 Rebirth-only extras below ,
        // added 2026-08-28 so the scanner stops treating them as
        // unrecognised files. See gb_gaming/dlss_updater.py KNOWN for why
        // NR has no official fetch source yet.
        ("nvngx_dlssnr.dll",  "DLSS Neural Rendering"),
        ("nvngx_deepdvc.dll", "Deep DVC"),
        ("sl.common.dll",     "Streamline Core"),
        ("sl.dlss.dll",       "Streamline DLSS SR"),
        ("sl.dlss_g.dll",     "Streamline DLSS FG"),
        ("sl.dlss_d.dll",     "Streamline DLSS RR"),
        ("sl.dlss_nr.dll",    "Streamline DLSS Neural Rendering"),
        ("sl.reflex.dll",     "Streamline Reflex"),
        ("sl.interposer.dll", "Streamline Interposer"),
        ("sl.nis.dll",        "Streamline NIS"),
        ("sl.pcl.dll",        "Streamline PCL"),
        ("sl.deepdvc.dll",    "Streamline Deep DVC"),
        ("sl.directsr.dll",   "Streamline DirectSR"),
        ("sl.nvperf.dll",     "Streamline NvPerf"),
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
/// True for a directory GreenBoost itself keeps backups in (e.g.
/// `_DLSS5_Backup`); its DLL copies are not the game's live libraries.
pub fn is_backup_dir(e: &walkdir::DirEntry) -> bool {
    e.depth() > 0 && e.file_type().is_dir()
        && e.file_name().to_string_lossy().to_lowercase().contains("backup")
}

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
            for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().filter_entry(|e| !is_backup_dir(e)).flatten() {
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
                installed: true,
                leftover_bytes: None,
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
                        for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().filter_entry(|e| !is_backup_dir(e)).flatten() {
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
                            installed: true,
                            leftover_bytes: None,
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
        for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().filter_entry(|e| !is_backup_dir(e)).flatten() {
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
            installed: true,
            leftover_bytes: None,
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

pub struct InstalledApp {
    pub appid: String,
    // Steam's own StateFlags. Bit 0x4 ("fully installed") is the only bit
    // this scanner cares about , see is_fully_installed().
    pub state_flags: u32,
}

/// Returns a map from the on-disk install directory name (ACF's
/// "installdir" field) to that app's AppID + StateFlags, across every Steam
/// library.
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
///
/// StateFlags was added 2026-08-31: this map used to carry only the appid,
/// so a directory Steam's uninstaller left behind (no manifest at all, or
/// a manifest with StateFlags never re-checked) was indistinguishable from
/// a real install , scan_games() just saw "a directory exists".
/// Pulls `installdir` + `appid` + `StateFlags` out of one appmanifest's raw
/// text. Split out from `installed_apps_map()` so the parsing itself is
/// testable without touching `$HOME` or the filesystem , everything else in
/// that function is just "which files to read", not "what they mean".
/// `installdir`/`appid` missing means a malformed or unrelated file, not a
/// manifest worth indexing at all. `StateFlags` missing defaults to 0
/// (not fully installed) rather than assuming installed , an absent field
/// is not evidence a game is playable.
fn parse_acf_manifest(content: &str) -> Option<(String, InstalledApp)> {
    let installdir_re = Regex::new(r#""installdir"\s+"([^"]+)""#).unwrap();
    let appid_re = Regex::new(r#""appid"\s+"([^"]+)""#).unwrap();
    let state_re = Regex::new(r#""StateFlags"\s+"(\d+)""#).unwrap();

    let installdir = installdir_re.captures(content)?[1].to_string();
    let appid = appid_re.captures(content)?[1].to_string();
    let state_flags = state_re.captures(content)
        .and_then(|c| c[1].parse::<u32>().ok())
        .unwrap_or(0);
    Some((installdir, InstalledApp { appid, state_flags }))
}

pub fn installed_apps_map() -> HashMap<String, InstalledApp> {
    let mut map = HashMap::new();
    for lib in get_steam_libraries() {
        let Ok(entries) = std::fs::read_dir(&lib) else { continue };
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname_str = fname.to_string_lossy();
            if fname_str.starts_with("appmanifest_") && fname_str.ends_with(".acf") {
                let Ok(content) = std::fs::read_to_string(entry.path()) else { continue };
                if let Some((installdir, app)) = parse_acf_manifest(&content) {
                    map.insert(installdir, app);
                }
            }
        }
    }
    map
}

/// Steam's "fully installed" bit. Anything else (updating, partially
/// installed, update-required, uninstalling, …) is not something the Suite
/// should offer to launch, and a directory with no manifest at all (the
/// uninstall-leftover case) doesn't reach this check , it has no entry in
/// the map to begin with.
fn is_fully_installed(state_flags: u32) -> bool {
    state_flags & 0x4 != 0
}

/// Scan games added to Steam as non-Steam shortcuts.
///
/// These are invisible to `scan_games()`'s library walk: a shortcut has no
/// appmanifest and its files live under `steamapps/compatdata/<appid>/pfx`,
/// not `steamapps/common`. The user-visible case that forced this is a
/// Battle.net launcher installed into its own Proton prefix.
///
/// Two levels are produced:
///   * the shortcut itself (the launcher), and
///   * for a Battle.net prefix, the games installed *under* it, read from
///     Blizzard's own `product.db` inside the prefix , because a tile
///     saying "Battle.net" is not the game the user wants to launch.
pub fn scan_shortcut_games() -> Vec<Game> {
    let target_dlls: HashMap<&str, &str> = nvidia_dll_target_map();
    let mut games: Vec<Game> = Vec::new();

    // A prefix is large (14 GB was measured on the reference box) and most
    // of it is Wine, not game files. Cap the DLL walk instead of letting a
    // scan of one launcher stall the whole Games view.
    fn dlls_under(dir: &Path, target_dlls: &HashMap<&str, &str>) -> Vec<GameDll> {
        let mut found = Vec::new();
        for walk_entry in WalkDir::new(dir).max_depth(6).into_iter().flatten() {
            let f_path = walk_entry.path();
            if !f_path.is_file() { continue; }
            if let Some(f_name) = f_path.file_name().and_then(|n| n.to_str()) {
                if let Some(&tech) = target_dlls.get(f_name.to_lowercase().as_str()) {
                    found.push(GameDll {
                        name: f_name.to_string(),
                        path: f_path.to_string_lossy().to_string(),
                        version: get_dll_version(f_path),
                        tech_type: tech.to_string(),
                    });
                }
            }
        }
        found
    }

    for sc in crate::nonsteam::read_shortcuts() {
        if sc.is_hidden || sc.exe.is_empty() { continue; }
        let exe = PathBuf::from(&sc.exe);
        // A shortcut whose target is gone is a leftover, not a game.
        if !exe.is_file() { continue; }
        let Some(launcher_dir) = exe.parent().map(|p| p.to_path_buf()) else { continue };

        // Steam's own icon for the shortcut, when the user set one. No CDN
        // fallback here , a non-Steam appid has no artwork on Steam's CDN.
        let image = if !sc.icon.is_empty() && Path::new(&sc.icon).is_file() {
            Some(sc.icon.clone())
        } else {
            None
        };

        games.push(Game {
            name: if sc.app_name.is_empty() {
                exe.file_stem().map(|s| s.to_string_lossy().into_owned())
                   .unwrap_or_else(|| "Non-Steam game".to_string())
            } else { sc.app_name.clone() },
            path: launcher_dir.to_string_lossy().to_string(),
            appid: Some(sc.appid.to_string()),
            image,
            dlls: dlls_under(&launcher_dir, &target_dlls),
            optimizations: Vec::new(),
            is_optimized: game_is_optimized(&launcher_dir),
            has_backup: game_has_backup(&launcher_dir),
            installed: true,
            leftover_bytes: None,
        });

        for (name, dir) in crate::nonsteam::battlenet_games(&sc.exe) {
            games.push(Game {
                name,
                path: dir.to_string_lossy().to_string(),
                // Same shortcut appid: launching any of them goes through
                // the same Battle.net launcher.
                appid: Some(sc.appid.to_string()),
                image: None,
                dlls: dlls_under(&dir, &target_dlls),
                optimizations: Vec::new(),
                is_optimized: game_is_optimized(&dir),
                has_backup: game_has_backup(&dir),
                installed: true,
                leftover_bytes: None,
            });
        }
    }

    games
}

pub fn scan_games() -> Vec<Game> {
    let libraries = get_steam_libraries();
    let mut games: HashMap<String, Game> = HashMap::new();

    let target_dlls: HashMap<&str, &str> = nvidia_dll_target_map();
    let appid_map = installed_apps_map();

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
            let mut dir_bytes: u64 = 0;

            // Scan for NVIDIA tech DLLs (limited depth for speed). Also
            // totals the directory's size on this same walk , reused as
            // leftover_bytes below for a not-installed entry, rather than
            // walking the tree a second time just to size it.
            for walk_entry in WalkDir::new(&game_dir).max_depth(10).into_iter().filter_entry(|e| !is_backup_dir(e)).flatten() {
                let f_path = walk_entry.path();
                if !f_path.is_file() { continue; }
                if let Ok(meta) = walk_entry.metadata() {
                    dir_bytes += meta.len();
                }
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

            // Installed only when Steam's own manifest says so , see
            // installed_apps_map()'s doc comment. A directory with no
            // matching manifest, or one whose StateFlags no longer has the
            // "fully installed" bit, is treated as an uninstall leftover:
            // no Launch button, no box art, surfaced instead as reclaimable
            // disk space. Real incident 2026-08-31: FF7 Rebirth (356 MB)
            // and FF16 (64 MB) both kept appearing as playable games for
            // this exact reason.
            let app = appid_map.get(&game_name);
            let installed = app.map(|a| is_fully_installed(a.state_flags)).unwrap_or(false);
            let appid = app.map(|a| a.appid.clone());
            let image = if installed { appid.as_ref().and_then(|id| get_steam_image(id)) } else { None };
            let leftover_bytes = if installed { None } else { Some(dir_bytes) };

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
                installed,
                leftover_bytes,
            });
        }
    }

    // Merge Heroic, Lutris and non-Steam shortcut games (deduplicate by
    // path). Each of those scanners already checks its own source of truth
    // for existence (installed.json's install_path, the Lutris prefix, the
    // shortcut's exe), so unlike the Steam library walk above they are
    // never leftovers , always installed.
    for g in scan_heroic_games().into_iter()
                                .chain(scan_lutris_games())
                                .chain(scan_shortcut_games()) {
        games.entry(g.path.clone()).or_insert(g);
    }

    // Self-heal the hidden list , see should_stay_hidden()'s doc comment.
    let mut hidden_list = load_hidden_games();
    let before = hidden_list.len();
    hidden_list.retain(|h| {
        let now_installed = games.get(&h.path).map(|g| g.installed).unwrap_or(false);
        should_stay_hidden(h, now_installed)
    });
    if hidden_list.len() != before {
        let _ = save_hidden_games(&hidden_list);
    }

    let hidden: HashSet<String> = hidden_list.into_iter().map(|g| g.path).collect();
    let mut result: Vec<Game> = games.into_values()
        .filter(|g| !hidden.contains(&g.path))
        .collect();
    result.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    result
}

/// Delete an uninstalled game's leftover files. Scoped tightly: only a
/// direct child of a `common/` dir under a real Steam library, only when
/// Steam no longer has a fully-installed manifest for it, and never one of
/// the non-game names scan_games() itself skips , so this can only ever
/// remove exactly what the "Not installed" section in the Games view is
/// showing, never an arbitrary path a caller hands in.
pub fn delete_leftover_files_impl(path: String) -> Result<u64, String> {
    let target = PathBuf::from(&path);

    let libraries = get_steam_libraries();
    let common_parent = libraries.iter()
        .find_map(|lib| {
            let common = lib.join("common");
            let parent = target.parent()?;
            if std::fs::canonicalize(parent).ok() == std::fs::canonicalize(&common).ok() {
                Some(common)
            } else { None }
        });
    let Some(_common) = common_parent else {
        return Err("not a direct child of a known Steam library's common/ dir".to_string());
    };

    let Some(game_name) = target.file_name().and_then(|n| n.to_str()) else {
        return Err("path has no file name".to_string());
    };
    let name_lower = game_name.to_lowercase();
    let is_non_game = name_lower.starts_with("proton")
        || name_lower.starts_with("steamlinuxruntime")
        || name_lower.contains("steam controller")
        || name_lower.contains("steamworks shared")
        || name_lower.contains("easyanticheat runtime")
        || name_lower.contains("hotfix")
        || name_lower == "steam.dll"
        || name_lower.contains("redistributable");
    if is_non_game {
        return Err("refusing to delete Steam infrastructure".to_string());
    }

    let app = installed_apps_map();
    if let Some(a) = app.get(game_name) {
        if is_fully_installed(a.state_flags) {
            return Err(format!("{game_name} is still installed , uninstall it from Steam first"));
        }
    }

    if !target.is_dir() {
        return Err("directory does not exist".to_string());
    }

    let freed: u64 = WalkDir::new(&target).into_iter().flatten()
        .filter_map(|e| e.metadata().ok())
        .filter(|m| m.is_file())
        .map(|m| m.len())
        .sum();

    std::fs::remove_dir_all(&target).map_err(|e| format!("delete failed: {e}"))?;
    Ok(freed)
}

#[cfg(test)]
mod installed_state_tests {
    use super::*;

    // Trimmed but structurally real snippet , the fields scan_games() cares
    // about, matching what's actually on disk in
    // ~/.local/share/Steam/steamapps/appmanifest_2680010.acf.
    const KHAZAN_INSTALLED: &str = "\"AppState\"\n{\n\t\"appid\"\t\t\"2680010\"\n\t\"name\"\t\t\"The First Berserker: Khazan\"\n\t\"StateFlags\"\t\t\"4\"\n\t\"installdir\"\t\t\"The First Berserker Khazan\"\n}\n";

    #[test]
    fn state_flags_4_is_fully_installed() {
        let (installdir, app) = parse_acf_manifest(KHAZAN_INSTALLED).unwrap();
        assert_eq!(installdir, "The First Berserker Khazan");
        assert_eq!(app.appid, "2680010");
        assert!(is_fully_installed(app.state_flags));
    }

    // 1026 = 0x402 , update-required (0x400) + uninstalling (0x2), neither
    // of which is the fully-installed bit (0x4). A game mid-update or
    // mid-uninstall should not show a Launch button.
    #[test]
    fn other_state_flags_are_not_fully_installed() {
        let content = KHAZAN_INSTALLED.replace("\"4\"", "\"1026\"");
        let (_, app) = parse_acf_manifest(&content).unwrap();
        assert!(!is_fully_installed(app.state_flags));
    }

    #[test]
    fn a_manifest_missing_state_flags_defaults_to_not_installed() {
        let content = KHAZAN_INSTALLED
            .lines()
            .filter(|l| !l.contains("StateFlags"))
            .collect::<Vec<_>>()
            .join("\n");
        let (_, app) = parse_acf_manifest(&content).unwrap();
        assert_eq!(app.state_flags, 0);
        assert!(!is_fully_installed(app.state_flags));
    }

    // Real incident 2026-08-31: FF7 Rebirth's manifest was gone entirely
    // (Steam's uninstaller removed it), but 356 MB of files were left under
    // steamapps/common/. A directory with no manifest match must never be
    // treated as installed , parse_acf_manifest() never even runs for a
    // directory that has no matching .acf, so installed_apps_map() simply
    // has no entry for it, and scan_games()'s `app.map(...).unwrap_or(false)`
    // resolves that absence to `installed: false`.
    #[test]
    fn malformed_or_unrelated_content_yields_no_manifest() {
        assert!(parse_acf_manifest("not a vdf file at all").is_none());
        assert!(parse_acf_manifest("\"appid\"\t\t\"123\"\n").is_none(), "missing installdir");
    }

    #[test]
    fn hidden_with_no_appid_is_reinstated_once_installed_again() {
        let entry = HiddenGame {
            path: "/games/Khazan".to_string(),
            name: "Khazan".to_string(),
            appid: None,
        };
        // Real incident: hidden while an orphaned leftover, later
        // reinstalled , must come back.
        assert!(!should_stay_hidden(&entry, true));
        // Still not installed , nothing has changed, stays hidden (this is
        // just the ordinary "removed from list" state, not a bug).
        assert!(should_stay_hidden(&entry, false));
    }

    #[test]
    fn hidden_with_an_appid_stays_hidden_regardless_of_install_state() {
        let entry = HiddenGame {
            path: "/games/Hearthstone".to_string(),
            name: "Hearthstone".to_string(),
            appid: Some("3274469611".to_string()),
        };
        assert!(should_stay_hidden(&entry, true));
        assert!(should_stay_hidden(&entry, false));
    }

    #[test]
    fn legacy_entries_with_no_appid_field_deserialize_as_none() {
        // Every hidden_games.json entry written before this field existed
        // has no "appid" key at all , confirms #[serde(default)] does the
        // right thing rather than failing to parse the whole file.
        let json = r#"[{"path":"/games/FF16","name":"FINAL FANTASY XVI"}]"#;
        let list: Vec<HiddenGame> = serde_json::from_str(json).unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].appid, None);
    }
}
