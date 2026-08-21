// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

use std::path::{Path, PathBuf};
use std::collections::HashMap;
use std::sync::OnceLock;
use regex::Regex;
use serde::{Serialize, Deserialize};
use walkdir::WalkDir;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct GameSetting {
    pub key: String,
    pub display: String,
    pub current: String,
    pub recommended: String,
    pub needs_change: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SettingGroup {
    pub title: String,
    pub settings: Vec<GameSetting>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Tier { Ultra, High, Medium, Low }

pub struct TierRec {
    pub ultra: &'static str,
    pub high: &'static str,
    pub medium: &'static str,
    pub low: &'static str,
}

// Memoized: detect_hardware_tier() now backs is_optimized on every game in
// the library scan (scanner.rs) in addition to the settings panel, and the
// hardware doesn't change mid-process , spawning nvidia-smi once per game
// on every scan was a real cost at ~300 profiles, not just a style nit.
static HARDWARE_TIER: OnceLock<Tier> = OnceLock::new();

pub fn detect_hardware_tier() -> Tier {
    HARDWARE_TIER.get_or_init(detect_hardware_tier_uncached).clone()
}

fn detect_hardware_tier_uncached() -> Tier {
    use std::process::Command;
    let out = Command::new("nvidia-smi")
        .args(["--query-gpu=compute_cap,memory.total", "--format=csv,noheader,nounits"])
        .output();

    if let Ok(o) = out {
        let text = String::from_utf8_lossy(&o.stdout);
        let mut parts = text.split(',');
        let sm: f32 = parts.next().unwrap_or("0").trim().parse().unwrap_or(0.0);
        let vram_mb: u64 = {
            let s = parts.next().unwrap_or("0").trim();
            let re = Regex::new(r"(\d+)").unwrap();
            re.captures(s).and_then(|c| c[1].parse().ok()).unwrap_or(0)
        };

        // SM 10.x = Blackwell (RTX 50xx), SM 8.9 = Ada Lovelace (RTX 40xx)
        // SM 8.x = Ampere (RTX 30xx), SM 7.5 = Turing (RTX 20xx)
        if sm >= 10.0 && vram_mb >= 12000 { return Tier::Ultra; }
        if sm >= 10.0 || (sm >= 8.9 && vram_mb >= 12000) { return Tier::Ultra; }
        if sm >= 8.9 || (sm >= 8.0 && vram_mb >= 10000) { return Tier::High; }
        if sm >= 8.0 || (sm >= 7.5 && vram_mb >= 8000)  { return Tier::Medium; }
    }
    Tier::Low
}

fn tier_rec(tier: &Tier, r: &TierRec) -> &'static str {
    match tier {
        Tier::Ultra => r.ultra,
        Tier::High => r.high,
        Tier::Medium => r.medium,
        Tier::Low => r.low,
    }
}

// Returns (display_name, category, TierRec) for each known setting key
fn settings_vocabulary() -> Vec<(&'static str, &'static [&'static str], &'static str, TierRec)> {
    // (display_name, key_aliases, category, recommendations)
    vec![
        ("Texture Quality", &["texturequality", "r.texturequality", "textureresolution", "sg.texturequality"] as &[&str], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Low" }),
        ("Shadow Quality", &["shadowquality", "sg.shadowquality", "r.shadowquality", "shadowresolution"], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Off" }),
        ("Ambient Occlusion", &["ambientocclusion", "ssao", "r.ambientocclusionstaticfraction", "hbao", "hbaoplus"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Anti-aliasing", &["antialiasing", "aaquality", "sg.antialiasingsetting", "r.antialiasingsetting", "aamode"], "In-Game Settings", TierRec { ultra: "DLSS Quality", high: "DLSS Balanced", medium: "DLSS Performance", low: "TAA" }),
        ("Cloud Quality", &["cloudquality", "clouddetail", "vcloudquality"], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Low" }),
        ("Detail Distance", &["detaildistance", "levellofdistance", "levelofdetail", "loddistance", "sg.viewdistancequality"], "In-Game Settings", TierRec { ultra: "High", high: "High", medium: "Medium", low: "Low" }),
        ("Display Mode", &["displaymode", "windowmode", "fullscreen", "windowtype"], "In-Game Settings", TierRec { ultra: "FullScreen", high: "FullScreen", medium: "FullScreen", low: "FullScreen" }),
        ("Dynamic Crowds", &["dynamiccrowds", "crowddensity", "npcpopulation"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "On", low: "Off" }),
        ("FidelityFX Super Resolution (FSR) 1.0", &["fsr_enable", "fidelityfxsr", "amd_fsr", "fsrenable", "fsr1"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("FidelityFX Super Resolution (FSR) 2.2", &["fsr2", "fsr2_enable", "fsr_version2", "fsr22"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("Foliage Quality", &["foliagequality", "vegetationquality", "treequality", "sg.foliagequality"], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Low" }),
        ("Motion Blur", &["motionblur", "motionblurenable", "motionbluramount", "sg.motionblurquality"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("Depth of Field", &["depthoffield", "dof", "dofquality"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "On", low: "Off" }),
        ("Lens Flare", &["lensflare", "lensflareenable"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Chromatic Aberration", &["chromaticaberration", "chromaticaberrationsetting"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("Film Grain", &["filmgrain", "filmgrainintensity"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("Screen Space Reflections", &["screenspacereflections", "ssr", "ssrquality", "r.ssrquality"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Global Illumination", &["globalillumination", "gi", "gi_quality", "vxgi"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Hair Works / Strand Hair", &["hairworks", "strandhair", "nvidia_hairworks", "hairrendering"], "In-Game Settings", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Max Frame Rate", &["maxframerate", "frameratelimit", "fpslimit", "maxfps"], "In-Game Settings", TierRec { ultra: "0", high: "0", medium: "0", low: "0" }),
        ("Graphics Preset", &["graphicspreset", "qualitypreset", "graphicslevel", "graphicsquality"], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Low" }),
        ("Vertical Sync", &["vsync", "verticalsync", "vsyncenable", "r.vsync", "busevsync"], "In-Game Settings", TierRec { ultra: "Off", high: "Off", medium: "Off", low: "Off" }),
        ("Shadow Distance", &["shadowdistance", "r.shadowdistance", "shadowfarplane"], "In-Game Settings", TierRec { ultra: "Ultra", high: "High", medium: "Medium", low: "Low" }),
        // RTX Technologies
        ("DLSS Super Resolution", &["dlss_quality", "dlssquality", "dlssmode", "upscalingalgorithm", "dlssresolutionmode"], "RTX Technologies", TierRec { ultra: "Quality", high: "Balanced", medium: "Performance", low: "Off" }),
        ("DLSS Frame Generation", &["framegeneration", "dlss_g", "dlssg_enable", "nvngx_dlssg", "framegenerationenable"], "RTX Technologies", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("NVIDIA Reflex", &["reflexenable", "nvidia_reflex", "dlss_reflexlowlatency", "reflexlowlatency", "reflex"], "RTX Technologies", TierRec { ultra: "On", high: "On", medium: "On", low: "Off" }),
        ("Ray Tracing", &["raytracing", "rtx_enable", "raytracingsetting", "enablertxgi", "rtxenabled", "r.raytracing"], "RTX Technologies", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
        ("Ray Tracing Quality", &["raytracingquality", "rtxquality", "rtxreflections", "raytracinglevel"], "RTX Technologies", TierRec { ultra: "Ultra", high: "High", medium: "Off", low: "Off" }),
        ("DLSS Ray Reconstruction", &["dlssd", "dlss_rr", "rayreconstruction", "nvngx_dlssd"], "RTX Technologies", TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
    ]
}

// ── Value domains ──────────────────────────────────────────────────────
//
// `settings_vocabulary()` recommends human words ("Ultra", "On", "DLSS
// Quality") because most games' own config keys are word-valued
// (`TextureQuality=High`). Unreal Engine's `sg.*Quality` scalability group
// and a handful of `b*`/float cvars are not , they're integers (0-4) or
// `True`/`False`/floats, and writing a word into them is simply invalid to
// the engine, so it gets discarded and the scan reads the old numeric
// value back on the next pass: `needs_change` never clears no matter how
// many times Optimize runs. Confirmed live 2026-08-08 against "The First
// Berserker: Khazan" (`sg.TextureQuality=0`, `bUseVSync=False`,
// `FrameRateLimit=60.000000`) , every one of those kept re-showing "needs
// change" after Optimize. The domain is inferred from the alias that
// actually matched a real key in the file, not from the vocabulary entry,
// so a game using the word form still gets words.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Domain { Word, UeScalability, UeBool, UeFloat }

fn infer_domain(alias: &str) -> Domain {
    let a = alias.to_lowercase();
    if a.starts_with("sg.") && a.ends_with("quality") { return Domain::UeScalability; }
    if a == "busevsync" || a == "bsmoothframerate" { return Domain::UeBool; }
    if a == "frameratelimit" { return Domain::UeFloat; }
    Domain::Word
}

/// Render a tier word ("Ultra"/"On"/"Off") as the literal the matched key's
/// domain actually accepts. Unrecognized words pass through unchanged ,
/// this only ever narrows behavior for the handful of keys above.
fn render_for_domain(domain: Domain, tier_word: &str) -> String {
    match domain {
        Domain::Word => tier_word.to_string(),
        Domain::UeScalability => match tier_word.to_lowercase().as_str() {
            "ultra" | "epic" | "cinematic" => "4",
            "high"  => "3",
            "medium" => "2",
            "low"   => "1",
            "off"   => "0",
            _ => tier_word,
        }.to_string(),
        Domain::UeBool => match tier_word.to_lowercase().as_str() {
            "on" | "true" => "True",
            "off" | "false" => "False",
            _ => tier_word,
        }.to_string(),
        Domain::UeFloat => match tier_word.trim().parse::<f64>() {
            Ok(n) => format!("{n:.6}"),
            Err(_) => tier_word.to_string(),
        },
    }
}

/// Compares `current` (as read from the file) against a value already
/// rendered into the same domain via `render_for_domain`.
fn values_equal_for_domain(domain: Domain, current: &str, recommended_rendered: &str) -> bool {
    match domain {
        Domain::UeFloat => {
            match (current.trim().parse::<f64>(), recommended_rendered.trim().parse::<f64>()) {
                (Ok(a), Ok(b)) => (a - b).abs() < 0.01,
                _ => current.trim().eq_ignore_ascii_case(recommended_rendered.trim()),
            }
        }
        _ => current.trim().eq_ignore_ascii_case(recommended_rendered.trim()),
    }
}

/// Excludes Unreal's installer/bookkeeping tree
/// (`AppData/Local/UnrealEngine/<ver>/Saved/Config/.../Manifest.ini`) from
/// being treated as a game-settings file. Confirmed live 2026-08-08: the
/// append-fallback in `apply_across_config_files` was writing invented
/// keys into exactly this file (346 lines for one game) because it was the
/// first ".ini" the walk happened to find , this is never where a game's
/// own settings live; the per-game tree sits under the studio's own
/// codename directory (e.g. `.../BBQ/Saved/Config/...`), not `UnrealEngine/`.
fn is_plausible_settings_file(path: &Path) -> bool {
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_lowercase();
    if name.contains("manifest") { return false; }
    let path_lower = path.to_string_lossy().to_lowercase();
    !(path_lower.contains("/unrealengine/") && path_lower.contains("/saved/config/"))
}

const SKIP_DIRS: &[&str] = &["_commonredist", "__installers", "redist", "shaders", "videos", "movies", "data/movies", "node_modules", "crack", ".git"];

pub fn find_config_files(game_path: &Path) -> Vec<PathBuf> {
    let mut results = Vec::new();
    let home = std::env::var("HOME").unwrap_or_default();

    // Walk game directory (max depth 6, skip noise dirs)
    for entry in WalkDir::new(game_path).max_depth(6).into_iter().flatten() {
        let path = entry.path();
        if !path.is_file() { continue; }

        // Skip noise directories
        let path_lower = path.to_string_lossy().to_lowercase();
        if SKIP_DIRS.iter().any(|d| path_lower.contains(d)) { continue; }

        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            match ext.to_lowercase().as_str() {
                "ini" | "cfg" | "config" => results.push(path.to_path_buf()),
                "json" => {
                    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                        let n = name.to_lowercase();
                        if n.contains("setting") || n.contains("config") || n.contains("option") || n.contains("pref") || n.contains("graphic") {
                            results.push(path.to_path_buf());
                        }
                    }
                }
                _ => {}
            }
        }
    }

    // Check XDG config dirs
    if let Some(game_name) = game_path.file_name().and_then(|n| n.to_str()) {
        let xdg_paths = vec![
            format!("{}/.config/{}", home, game_name),
            format!("{}/.local/share/{}", home, game_name),
            format!("{}/.config/{}", home, game_name.to_lowercase()),
        ];
        for xdg in xdg_paths {
            let p = PathBuf::from(&xdg);
            if p.exists() {
                for entry in WalkDir::new(&p).max_depth(3).into_iter().flatten() {
                    let ep = entry.path();
                    if ep.is_file() {
                        if let Some(ext) = ep.extension().and_then(|e| e.to_str()) {
                            if matches!(ext.to_lowercase().as_str(), "ini" | "cfg" | "json" | "config") {
                                results.push(ep.to_path_buf());
                            }
                        }
                    }
                }
            }
        }
    }

    // Proton/Windows games never write their user settings into their own
    // (read-only) install directory , Unreal Engine titles in particular
    // write GameUserSettings.ini/Engine.ini into the Windows profile inside
    // the Proton prefix, at a path keyed by the game's INTERNAL project
    // codename, not its Steam name or install dir. Confirmed live 2026-08-07
    // for "The First Berserker: Khazan" (installdir "The First Berserker
    // Khazan"): its real config lives at
    // steamapps/compatdata/2680010/pfx/drive_c/users/steamuser/AppData/Local/BBQ/Saved/Config/WindowsNoEditor/
    // , "BBQ" is the studio's internal codename, unrelated to either the
    // Steam display name or the install directory, so this can only be
    // found by resolving the appid (via installdir) then globbing
    // AppData/Local/*/Saved/Config/WindowsNoEditor/, the fixed convention
    // every Unreal Engine Windows build uses regardless of project name.
    // Before this, find_config_files() could never locate settings for any
    // Proton game following this (extremely common) convention , every
    // setting silently showed "Not Set".
    if let Some(installdir) = game_path.file_name().and_then(|n| n.to_str()) {
        let appid_map = crate::scanner::installdir_appid_map();
        if let Some(appid) = appid_map.get(installdir) {
            // game_path = <library>/steamapps/common/<installdir> , the
            // library's steamapps/ dir is two levels up, same root
            // compatdata/ sits under.
            if let Some(steamapps) = game_path.parent().and_then(|p| p.parent()) {
                let steamuser = steamapps
                    .join("compatdata").join(appid).join("pfx")
                    .join("drive_c/users/steamuser");
                // BOTH standard Unreal config roots, not just one.
                //
                // UE writes Saved/Config/ under %LOCALAPPDATA%\<Project>\ OR
                // under %USERPROFILE%\Documents\My Games\<Project>\,
                // depending on what the project sets. Only AppData/Local was
                // searched here, so any title using the Documents form
                // reported "No writable config file found" however many times
                // it had been played , confirmed 2026-08-21 with FINAL
                // FANTASY VII REBIRTH (appid 2909400), whose GameUserSettings
                // .ini sits at Documents/My Games/FINAL FANTASY VII REBIRTH/
                // Saved/Config/WindowsNoEditor/ and has nothing at all under
                // AppData/Local.
                for root in [steamuser.join("AppData/Local"),
                             steamuser.join("Documents/My Games")] {
                    if !root.exists() { continue; }
                    for entry in WalkDir::new(&root).max_depth(6).into_iter().flatten() {
                        let ep = entry.path();
                        if !ep.is_file() { continue; }
                        // Only descend into a real "Saved/Config/..." tree ,
                        // AppData/Local also holds Temp/, browser caches,
                        // etc. that would otherwise pollute the merge.
                        let ep_lower = ep.to_string_lossy().to_lowercase();
                        if !ep_lower.contains("/saved/config/") { continue; }
                        // CrashReportClient.ini is UE's crash-reporter config,
                        // present in every UE prefix and never a game setting.
                        if ep_lower.contains("/crashreportclient/") { continue; }
                        if let Some(ext) = ep.extension().and_then(|e| e.to_str()) {
                            if matches!(ext.to_lowercase().as_str(), "ini" | "cfg" | "config") {
                                results.push(ep.to_path_buf());
                            }
                        }
                    }
                }
            }
        }
    }

    results
}

pub fn parse_config_file(path: &Path) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let Ok(content) = std::fs::read_to_string(path) else { return map; };

    if path.extension().and_then(|e| e.to_str()) == Some("json") {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) {
            flatten_json(&v, "", &mut map);
        }
        return map;
    }

    // INI/CFG: key=value or key = value, ignore [sections] and comments
    let re = Regex::new(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.\[\]]*)\s*=\s*(.*)$").unwrap();
    for cap in re.captures_iter(&content) {
        let key = cap[1].trim().to_string();
        let val = cap[2].trim().trim_matches('"').trim_matches('\'').to_string();
        if !key.starts_with(';') && !key.starts_with('#') && !val.is_empty() {
            map.insert(key.to_lowercase(), val);
        }
    }
    map
}

fn flatten_json(v: &serde_json::Value, prefix: &str, out: &mut HashMap<String, String>) {
    match v {
        serde_json::Value::Object(obj) => {
            for (k, val) in obj {
                let new_key = if prefix.is_empty() { k.clone() } else { format!("{}.{}", prefix, k) };
                flatten_json(val, &new_key, out);
            }
        }
        serde_json::Value::Bool(b) => { out.insert(prefix.to_lowercase(), if *b { "On".into() } else { "Off".into() }); }
        serde_json::Value::Number(n) => { out.insert(prefix.to_lowercase(), n.to_string()); }
        serde_json::Value::String(s) => { out.insert(prefix.to_lowercase(), s.clone()); }
        _ => {}
    }
}

pub fn scan_game_settings(game_path: &Path) -> Vec<SettingGroup> {
    let tier = detect_hardware_tier();
    let config_files = find_config_files(game_path);

    // Merge all config files into one flat key→value map
    let mut merged: HashMap<String, String> = HashMap::new();
    for cfg_path in &config_files {
        let parsed = parse_config_file(cfg_path);
        merged.extend(parsed);
    }

    let vocab = settings_vocabulary();
    let mut ingame_settings: Vec<GameSetting> = Vec::new();
    let mut nvidia_settings: Vec<GameSetting> = Vec::new();

    // Always include NVIDIA tech settings (even if not found in configs)
    let nvidia_keys: &[&str] = &["dlss_quality", "framegeneration", "reflexenable", "raytracing", "raytracingquality", "dlssd"];

    for (display, aliases, category, rec) in &vocab {
        let tier_word = tier_rec(&tier, rec).to_string();

        // Find if any alias exists in merged config, remembering which one
        // matched , needs_change and the displayed "recommended" value must
        // both be rendered into THAT alias's domain, not compared as words,
        // or a numeric UE key can never converge (see Domain doc comment).
        let matched = aliases.iter()
            .find_map(|alias| merged.get(*alias).map(|v| (*alias, v.clone())));

        let (current, recommended, needs_change) = match matched {
            Some((alias, current_val)) => {
                let domain = infer_domain(alias);
                let recommended_rendered = render_for_domain(domain, &tier_word);
                let nc = !values_equal_for_domain(domain, &current_val, &recommended_rendered);
                (current_val, recommended_rendered, nc)
            }
            None if nvidia_keys.iter().any(|k| aliases.contains(k)) => {
                // The key doesn't exist in ANY config file for this game ,
                // writing one does nothing but pollute an arbitrary file
                // (that used to append into Manifest.ini). Still show it as
                // "Not Set" for visibility, but it can no longer pin the
                // "Not optimized" badge forever: partial revert of the
                // 2026-08-07 change at this call site (kept for genuinely
                // present-but-wrong values) , narrowed to "present" only.
                ("Not Set".to_string(), tier_word.clone(), false)
            }
            None => continue, // not an NVIDIA key and not found , skip
        };

        let setting = GameSetting {
            key: aliases[0].to_string(),
            display: display.to_string(),
            current,
            recommended,
            needs_change,
        };

        if *category == "RTX Technologies" {
            nvidia_settings.push(setting);
        } else {
            ingame_settings.push(setting);
        }
    }

    let mut groups = Vec::new();
    if !ingame_settings.is_empty() {
        groups.push(SettingGroup { title: "In-Game Settings".to_string(), settings: ingame_settings });
    }
    if !nvidia_settings.is_empty() {
        groups.push(SettingGroup { title: "RTX Technologies".to_string(), settings: nvidia_settings });
    }

    // If nothing found in configs, return a minimal NVIDIA group with defaults
    if groups.is_empty() {
        let nvidia_defaults = vec![
            ("DLSS Super Resolution", "dlssquality", "Not Set"),
            ("DLSS Frame Generation", "framegeneration", "Not Set"),
            ("NVIDIA Reflex", "reflexenable", "Not Set"),
            ("Ray Tracing", "raytracing", "Not Set"),
        ];
        let settings: Vec<GameSetting> = nvidia_defaults.iter().map(|(disp, key, cur)| {
            let rec_str = match *key {
                "dlssquality" => tier_rec(&tier, &TierRec { ultra: "Quality", high: "Balanced", medium: "Performance", low: "Off" }),
                "framegeneration" => tier_rec(&tier, &TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
                "reflexenable" => tier_rec(&tier, &TierRec { ultra: "On", high: "On", medium: "On", low: "Off" }),
                "raytracing" => tier_rec(&tier, &TierRec { ultra: "On", high: "On", medium: "Off", low: "Off" }),
                _ => "Off",
            };
            GameSetting {
                key: key.to_string(),
                display: disp.to_string(),
                current: cur.to_string(),
                recommended: rec_str.to_string(),
                needs_change: false,
            }
        }).collect();
        groups.push(SettingGroup { title: "RTX Technologies".to_string(), settings });
    }

    // A8: Engine-specific group
    if let Some(engine_group) = scan_engine_group(game_path) {
        groups.push(engine_group);
    }

    // A8: Per-game profile group
    if let Some(profile_settings) = load_per_game_profile(game_path) {
        groups.push(SettingGroup {
            title: "Game Profile Optimizations".to_string(),
            settings: profile_settings,
        });
    }

    groups
}

/// ini/cfg/config files only , the ones `parse_config_file`'s `key=value`
/// regex actually understands. Excludes json (structured, not line-based;
/// scan_game_settings still reads json for *display* via flatten_json, but
/// nothing here regex-edits it).
fn find_writable_config_files(game_path: &Path) -> Vec<PathBuf> {
    find_config_files(game_path).into_iter().filter(|p| {
        p.extension().and_then(|e| e.to_str())
            .map(|e| matches!(e, "ini" | "cfg" | "config"))
            .unwrap_or(false)
        && is_plausible_settings_file(p)
    }).collect()
}

/// Apply one regex substitution across every writable config file, appending
/// to `fallback_append_to` (first file found) when no file already has the
/// key. Returns true if at least one file was successfully written.
///
/// Why every file, not just the first: `scan_game_settings` merges keys from
/// ALL config files for the Current/Recommended comparison the user sees ,
/// many engines (Unreal in particular) split settings across
/// GameUserSettings.ini and Engine.ini. Editing only the first file found
/// (previous behavior) silently left keys that live in a different file
/// unchanged, even though the UI had just shown them as "needs change".
fn apply_across_config_files(
    files: &[PathBuf], key_or_aliases: &[&str], value: &str,
) -> bool {
    let mut any_written = false;
    let mut any_matched = false;
    let mut file_contents: Vec<(PathBuf, String, bool)> = Vec::new();

    for path in files {
        let Ok(original) = std::fs::read_to_string(path) else { continue };
        let mut content = original.clone();
        let mut matched_here = false;
        for alias in key_or_aliases {
            let rendered = render_for_domain(infer_domain(alias), value);
            // Anchored to the whole line and capturing the whole value
            // (not `\S+`, a single token) , the previous unanchored,
            // single-token pattern both matched substrings of unrelated
            // keys (`vsync` inside `bUseVSync`, corrupting it) and left
            // the tail of any multi-word value in place, so re-running
            // Optimize on "DLSS Quality" produced
            // "DLSS Quality Quality Quality ..." forever. Confirmed live
            // 2026-08-08 on Khazan's Manifest.ini.
            let re_str = format!(
                r"(?im)^([ \t]*)({})[ \t]*=[ \t]*.*$",
                regex::escape(alias),
            );
            if let Ok(re) = Regex::new(&re_str) {
                // Must count as "matched" whenever the key is found, not
                // only when the substitution changes the text , a value
                // that's already converged (no-op rewrite) otherwise looks
                // identical to "key absent" on every later run, so the
                // append-fallback fires and re-appends the bare fallback
                // key forever even though the real key already exists and
                // is already correct. Caught live by the optimizer's own
                // idempotency test: a second Optimize run on an
                // already-converged file kept growing it.
                if re.is_match(&content) { matched_here = true; }
                content = re.replace_all(&content, |caps: &regex::Captures| {
                    format!("{}{}={}", &caps[1], &caps[2], rendered)
                }).to_string();
            }
        }
        if matched_here { any_matched = true; }
        file_contents.push((path.clone(), content, matched_here));
    }

    // Key not present in any file , append to the first file found that's
    // a plausible settings file (find_writable_config_files already
    // excludes Unreal's Manifest.ini bookkeeping tree, so "first" here can
    // no longer land in it).
    let append_idx = if any_matched { None } else {
        file_contents.iter().position(|(p, _, _)| is_plausible_settings_file(p))
    };
    if let Some(idx) = append_idx {
        let rendered = render_for_domain(infer_domain(key_or_aliases[0]), value);
        let (_path, content, _) = &mut file_contents[idx];
        if !content.ends_with('\n') { content.push('\n'); }
        content.push_str(&format!("{}={rendered}\n", key_or_aliases[0]));
    }

    for (i, (path, content, matched_here)) in file_contents.iter().enumerate() {
        let should_write = if any_matched { *matched_here } else { Some(i) == append_idx };
        if !should_write { continue; }
        let backup_path = PathBuf::from(format!("{}.gbak", path.to_string_lossy()));
        if !backup_path.exists() {
            if let Ok(original) = std::fs::read_to_string(path) {
                let _ = std::fs::write(&backup_path, &original);
            }
        }
        if std::fs::write(path, content).is_ok() { any_written = true; }
    }
    any_written
}

pub fn apply_optimization(game_path: &Path) -> bool {
    let files = find_writable_config_files(game_path);
    if files.is_empty() { return false; }

    let tier = detect_hardware_tier();
    let vocab = settings_vocabulary();
    let mut any_written = false;

    // Apply vocabulary-based settings across every config file.
    for (_display, aliases, _cat, rec) in &vocab {
        let recommended = tier_rec(&tier, rec);
        if apply_across_config_files(&files, aliases, recommended) { any_written = true; }
    }

    // A8: Apply engine-specific settings across every config file.
    if let Some(engine) = detect_game_engine(game_path) {
        for (key, value) in engine_specific_settings(engine, &tier) {
            if apply_across_config_files(&files, &[key], value) { any_written = true; }
        }
    }

    any_written
}

pub fn set_game_setting_impl(game_path: &Path, key: &str, value: &str) -> bool {
    let files = find_writable_config_files(game_path);
    if files.is_empty() { return false; }

    // Resolve all aliases for this key so we match whatever spelling is in the file.
    let vocab = settings_vocabulary();
    let aliases: Vec<String> = vocab.iter()
        .find(|(_, a, _, _)| a.iter().any(|k| k.eq_ignore_ascii_case(key)))
        .map(|(_, a, _, _)| a.iter().map(|s| s.to_string()).collect())
        .unwrap_or_else(|| vec![key.to_string()]);
    let alias_refs: Vec<&str> = aliases.iter().map(|s| s.as_str()).collect();

    apply_across_config_files(&files, &alias_refs, value)
}

pub fn revert_optimization(game_path: &Path) -> bool {
    let files = find_config_files(game_path);
    let mut reverted = false;
    for config_path in files {
        let backup_path = PathBuf::from(format!("{}.gbak", config_path.to_string_lossy()));
        if backup_path.exists() {
            if std::fs::copy(&backup_path, &config_path).is_ok() {
                let _ = std::fs::remove_file(&backup_path);
                reverted = true;
            }
        }
    }
    reverted
}

// ── A8: Game engine detection + per-engine settings ──────────────────────

/// Detect game engine from binary/file markers in the game directory.
/// Returns a short engine ID: "unreal", "unity", "id_tech", "source2",
/// "frostbite", "cry", or None.
pub fn detect_game_engine(game_path: &Path) -> Option<&'static str> {
    // Unreal Engine: UE4/UE5 folder structure markers
    let ue_markers = ["Engine/Binaries", "Engine/Content", "UE4Game", "UnrealGame",
                      "FortniteGame", "Shipping/Win64"];
    for m in &ue_markers {
        if game_path.join(m).exists() { return Some("unreal"); }
    }
    // Unity: managed .dll + UnityPlayer.dll
    if game_path.join("UnityPlayer.dll").exists()
       || game_path.join("UnityPlayer.so").exists()
       || game_path.join("Data/Managed").exists() {
        return Some("unity");
    }
    // id Tech 5/6/7 (DOOM, Wolfenstein, Rage)
    if game_path.join("base").join("pak000.pk4").exists()
       || game_path.join("base").join("pak0000.resources").exists()
       || game_path.join("classicgamecontent").exists() {
        return Some("id_tech");
    }
    // Source 2 (Dota 2, CS2, Half-Life: Alyx)
    if game_path.join("game").join("csgo").exists()
       || game_path.join("dota").exists()
       || game_path.join("hlvr").exists() {
        return Some("source2");
    }
    // Frostbite (Battlefield, FIFA, Mass Effect: Legendary)
    if game_path.join("Data").join("Win32").exists()
       || game_path.join("Data").join("Win64").exists() {
        // Additional check: presence of .cas files
        if walkdir::WalkDir::new(game_path.join("Data")).max_depth(2)
            .into_iter().flatten()
            .any(|e| e.path().extension().map(|x| x == "cas").unwrap_or(false))
        {
            return Some("frostbite");
        }
    }
    // CryEngine / CRYENGINE (Far Cry, Hunt: Showdown)
    if game_path.join("Engine").join("CryENGINE.exe").exists()
       || game_path.join("Engine").join("Bin64").exists() {
        return Some("cry");
    }
    None
}

/// Returns engine-specific INI settings to inject during optimize.
/// Tuned for RTX GPUs running via GreenBoost Proton.
pub fn engine_specific_settings(engine: &str, tier: &Tier) -> Vec<(&'static str, &'static str)> {
    match engine {
        "unreal" => {
            let mut s = vec![
                // Explicit fullscreen for lowest latency (Wayland fullscreen bypass)
                ("FullscreenMode", "0"),
                // Disable frame smoothing , causes micro-stutters, DLSS handles it
                ("bSmoothFrameRate", "False"),
                // Disable motion blur (GreenBoost adds motion clarity via Reflex)
                ("r.MotionBlurQuality", "0"),
                // Enable distance field shadows for better RT integration
                ("r.DistanceFieldShadowing", "1"),
                // Screen percentage at 100% , DLSS does its own scaling
                ("r.ScreenPercentage", "100"),
                // Vulkan-friendly PSO caching (avoids shader compile stutter)
                ("r.ShaderPipelineCache.Enabled", "1"),
                ("r.ShaderPipelineCache.GameFileMaskEnabled", "1"),
            ];
            if matches!(tier, Tier::Ultra | Tier::High) {
                s.push(("r.Lumen.Reflections.Allow", "1"));
                s.push(("r.Nanite.Enabled", "1"));
            }
            s
        }
        "unity" => vec![
            ("targetFrameRate", "-1"),      // uncap
            ("vSyncCount", "0"),            // disable vsync (DXGI handles it)
            ("maximumLODLevel", "0"),        // highest detail LOD
            ("streamingMipmapsActive", "1"), // streaming keeps VRAM pressure low
        ],
        "id_tech" => vec![
            ("com_skipIntroVideo", "1"),
            ("r_syncFlush", "0"),           // async GPU sync → lower latency
            ("r_displayRefresh", "0"),      // let display run uncapped
        ],
        "source2" => vec![
            ("r_dynamic_envmap", "1"),
            ("r_queued_decals", "1"),
            ("mat_queue_mode", "-1"),        // auto threading
        ],
        "frostbite" => vec![
            ("GstRender.Dx12Enabled", "1"),
            ("GstRender.Dx11Enable", "0"),
        ],
        "cry" => vec![
            ("r_MotionBlur", "0"),
            ("r_VSync", "0"),
        ],
        _ => vec![],
    }
}

/// Scan for engine-specific recommended settings and return them as a group.
pub fn scan_engine_group(game_path: &Path) -> Option<SettingGroup> {
    let engine = detect_game_engine(game_path)?;
    let tier = detect_hardware_tier();
    let pairs = engine_specific_settings(engine, &tier);
    if pairs.is_empty() { return None; }

    let engine_label = match engine {
        "unreal"    => "Unreal Engine",
        "unity"     => "Unity Engine",
        "id_tech"   => "id Tech Engine",
        "source2"   => "Source 2 Engine",
        "frostbite" => "Frostbite Engine",
        "cry"       => "CryEngine",
        _           => engine,
    };

    let config_files = find_config_files(game_path);
    let mut merged: HashMap<String, String> = HashMap::new();
    for cfg in &config_files {
        merged.extend(parse_config_file(cfg));
    }

    let settings: Vec<GameSetting> = pairs.into_iter().map(|(key, recommended)| {
        // `merged` keys are lowercased by parse_config_file(); `key` here
        // comes from engine_specific_settings() in its natural mixed case
        // (e.g. "FullscreenMode", "r.MotionBlurQuality"). Looking it up
        // as-is could never hit, so `current` was always "Not Set" and
        // needs_change always true even right after a successful Optimize
        // that wrote the value correctly (apply_across_config_files uses a
        // case-insensitive regex). Lowercase only for the lookup , `key`
        // itself still drives `display` below so the UI keeps showing the
        // canonical spelling.
        let current = merged.get(&key.to_lowercase()).cloned()
            .unwrap_or_else(|| "Not Set".to_string());
        // See the matching comment in scan_game_settings() above , "Not
        // Set" must count as needing change, not be excluded from it.
        // Engine-specific values are already literal (not tier words), so
        // this domain lookup is a no-op for everything except the handful
        // of UE keys the Domain table knows (e.g. bSmoothFrameRate) , kept
        // for one consistent comparison rule across both scan functions.
        let domain = infer_domain(key);
        let needs_change = !values_equal_for_domain(domain, &current, recommended);
        GameSetting {
            key: key.to_string(),
            display: key.to_string(),
            current,
            recommended: recommended.to_string(),
            needs_change,
        }
    }).collect();

    Some(SettingGroup {
        title: format!("{engine_label} Optimizations"),
        settings,
    })
}

/// Load per-game profile from ~/.config/greenboost-gaming/per-game/<AppID>.json
/// Returns additional recommended settings or None if no profile exists.
pub fn load_per_game_profile(game_path: &Path) -> Option<Vec<GameSetting>> {
    let game_name = game_path.file_name()?.to_str()?.to_lowercase();
    let home = std::env::var("HOME").unwrap_or_default();

    // Search order: user overrides first, then system profiles.
    let candidates = [
        PathBuf::from(&home).join(".config").join("greenboost-gaming").join("per-game").join(format!("{game_name}.json")),
        PathBuf::from("/usr/share/greenboost-gaming/profiles/per-game").join(format!("{game_name}.json")),
        PathBuf::from("/usr/local/share/greenboost-gaming/profiles/per-game").join(format!("{game_name}.json")),
    ];
    let profile_path = candidates.into_iter().find(|p| p.exists())?;

    let text = std::fs::read_to_string(&profile_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&text).ok()?;

    let settings_obj = data.get("settings")?.as_object()?;
    let tier = detect_hardware_tier();

    let result: Vec<GameSetting> = settings_obj.iter().filter_map(|(key, val)| {
        // Value can be: "string" or {"ultra":"x","high":"y","medium":"z","low":"w"}
        let recommended = if val.is_object() {
            let tier_key = match tier {
                Tier::Ultra  => "ultra",
                Tier::High   => "high",
                Tier::Medium => "medium",
                Tier::Low    => "low",
            };
            val.get(tier_key)?.as_str()?.to_string()
        } else {
            val.as_str()?.to_string()
        };
        Some(GameSetting {
            key: key.clone(),
            display: key.clone(),
            current: "Not Set".to_string(),
            recommended,
            needs_change: false,
        })
    }).collect();

    if result.is_empty() { None } else { Some(result) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_game_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "gb_optimizer_test_{name}_{}_{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .unwrap().as_nanos()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Both standard Unreal config roots are searched, not just one.
    ///
    /// UE writes Saved/Config/ under %LOCALAPPDATA%\\<Project>\\ OR under
    /// %USERPROFILE%\\Documents\\My Games\\<Project>\\. Only the first was
    /// searched, so FINAL FANTASY VII REBIRTH reported "No writable config
    /// file found" after a full 335-second play session that had just written
    /// GameUserSettings.ini (live, 2026-08-21). It has nothing whatsoever
    /// under AppData/Local.
    #[test]
    fn finds_unreal_config_under_documents_my_games_too() {
        let root = temp_game_dir("cfgroots");
        let steamuser = root.join("compatdata/9/pfx/drive_c/users/steamuser");

        let docs = steamuser.join("Documents/My Games/FINAL FANTASY VII REBIRTH/Saved/Config/WindowsNoEditor");
        fs::create_dir_all(&docs).unwrap();
        fs::write(docs.join("GameUserSettings.ini"), "[Core]\n").unwrap();

        let appdata = steamuser.join("AppData/Local/BBQ/Saved/Config/WindowsNoEditor");
        fs::create_dir_all(&appdata).unwrap();
        fs::write(appdata.join("Engine.ini"), "[Core]\n").unwrap();

        // UE's crash reporter config lives in the same tree and is never a
        // game setting , including it made every UE prefix look configured.
        let crash = steamuser.join("Documents/My Games/FINAL FANTASY VII REBIRTH/Saved/Config/CrashReportClient/UE4CC-Windows-X");
        fs::create_dir_all(&crash).unwrap();
        fs::write(crash.join("CrashReportClient.ini"), "[Core]\n").unwrap();

        let mut found: Vec<String> = Vec::new();
        for r in [steamuser.join("AppData/Local"), steamuser.join("Documents/My Games")] {
            if !r.exists() { continue; }
            for e in WalkDir::new(&r).max_depth(6).into_iter().flatten() {
                let ep = e.path();
                if !ep.is_file() { continue; }
                let low = ep.to_string_lossy().to_lowercase();
                if !low.contains("/saved/config/") { continue; }
                if low.contains("/crashreportclient/") { continue; }
                if ep.extension().and_then(|x| x.to_str())
                    .map(|x| matches!(x.to_lowercase().as_str(), "ini" | "cfg" | "config"))
                    .unwrap_or(false)
                {
                    found.push(ep.file_name().unwrap().to_string_lossy().into_owned());
                }
            }
        }
        found.sort();
        assert_eq!(found, vec!["Engine.ini".to_string(),
                               "GameUserSettings.ini".to_string()],
                   "both roots must be searched and CrashReportClient excluded");
        let _ = fs::remove_dir_all(&root);
    }

    /// Repro of the live 2026-08-08 bug on "The First Berserker: Khazan":
    /// Unreal's numeric `sg.*Quality` / `b*` / float cvars must converge
    /// (needs_change → false) after Optimize, and stay converged , not
    /// just stop growing.
    #[test]
    fn ue_scalability_keys_converge_and_stay_idempotent() {
        let game_dir = temp_game_dir("ue_scalability");
        let ini_path = game_dir.join("GameUserSettings.ini");
        fs::write(&ini_path,
            "sg.TextureQuality=0\nsg.ShadowQuality=0\nbUseVSync=True\nFrameRateLimit=75.000000\n"
        ).unwrap();

        assert!(apply_optimization(&game_dir), "first Optimize should write something");

        let groups = scan_game_settings(&game_dir);
        let tracked: Vec<&GameSetting> = groups.iter().flat_map(|g| &g.settings)
            .filter(|s| matches!(s.display.as_str(),
                "Texture Quality" | "Shadow Quality" | "Vertical Sync" | "Max Frame Rate"))
            .collect();
        assert_eq!(tracked.len(), 4, "expected all four tracked keys to be found in the file");
        for s in &tracked {
            assert!(!s.needs_change,
                "{} still needs_change after Optimize: current={:?} recommended={:?}",
                s.display, s.current, s.recommended);
        }

        // Idempotency: a second run must not change the file at all.
        let after_first = fs::read_to_string(&ini_path).unwrap();
        apply_optimization(&game_dir);
        let after_second = fs::read_to_string(&ini_path).unwrap();
        assert_eq!(after_first, after_second, "second Optimize run mutated the file");

        // Domain-correct literals, never a word in a numeric/bool slot.
        assert!(Regex::new(r"(?mi)^sg\.TextureQuality=\d+$").unwrap().is_match(&after_second));
        assert!(Regex::new(r"(?mi)^sg\.ShadowQuality=\d+$").unwrap().is_match(&after_second));
        assert!(Regex::new(r"(?mi)^bUseVSync=(True|False)$").unwrap().is_match(&after_second));
        assert!(Regex::new(r"(?mi)^FrameRateLimit=[\d.]+$").unwrap().is_match(&after_second));

        fs::remove_dir_all(&game_dir).ok();
    }

    /// Repro of the exact corruption seen live: a multi-word Word-domain
    /// recommendation ("DLSS Quality") applied repeatedly must never
    /// accrete trailing tokens onto the previous value.
    #[test]
    fn multiword_value_does_not_accrete_across_repeated_optimize() {
        let game_dir = temp_game_dir("multiword_accrete");
        let ini_path = game_dir.join("settings.ini");
        fs::write(&ini_path, "antialiasing=TAA\n").unwrap();

        for _ in 0..5 { apply_optimization(&game_dir); }

        let content = fs::read_to_string(&ini_path).unwrap();
        let line = content.lines()
            .find(|l| l.to_lowercase().starts_with("antialiasing="))
            .expect("antialiasing key missing after Optimize");
        assert!(!line.to_lowercase().contains("quality quality"),
            "value accreted across repeated Optimize runs: {line:?}");

        fs::remove_dir_all(&game_dir).ok();
    }

    /// Repro of the Manifest.ini pollution: a vocabulary key with no match
    /// anywhere must never get appended into Unreal's installer
    /// bookkeeping file, even when it's the only other .ini around.
    #[test]
    fn append_fallback_skips_manifest_ini() {
        let game_dir = temp_game_dir("manifest_skip");
        fs::create_dir_all(game_dir.join("sub")).unwrap();
        fs::write(game_dir.join("sub").join("Manifest.ini"), "existing=1\n").unwrap();
        fs::write(game_dir.join("GameUserSettings.ini"), "existing=1\n").unwrap();

        apply_optimization(&game_dir);

        let manifest_content = fs::read_to_string(game_dir.join("sub").join("Manifest.ini")).unwrap();
        assert_eq!(manifest_content, "existing=1\n",
            "Manifest.ini was written to , it must never be an append target");

        fs::remove_dir_all(&game_dir).ok();
    }

    #[test]
    fn value_domains_render_and_compare_correctly() {
        assert_eq!(render_for_domain(Domain::UeScalability, "Ultra"), "4");
        assert_eq!(render_for_domain(Domain::UeScalability, "Off"), "0");
        assert_eq!(render_for_domain(Domain::UeBool, "On"), "True");
        assert_eq!(render_for_domain(Domain::UeBool, "Off"), "False");
        assert_eq!(render_for_domain(Domain::UeFloat, "0"), "0.000000");
        assert_eq!(render_for_domain(Domain::Word, "DLSS Quality"), "DLSS Quality");

        assert!(values_equal_for_domain(Domain::UeFloat, "60.000000", "60.000000"));
        assert!(values_equal_for_domain(Domain::UeFloat, "60", "60.000000"));
        assert!(!values_equal_for_domain(Domain::UeScalability, "0", "4"));

        assert_eq!(infer_domain("sg.texturequality"), Domain::UeScalability);
        assert_eq!(infer_domain("busevsync"), Domain::UeBool);
        assert_eq!(infer_domain("frameratelimit"), Domain::UeFloat);
        assert_eq!(infer_domain("vsync"), Domain::Word);
    }
}
