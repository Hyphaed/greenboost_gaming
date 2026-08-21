// Copyright 2026 Ferran Duarri , GPL v2
// Global Settings , pure-Rust read/write, no Python subprocess.
//
// Config lives at ~/.config/greenboost-gaming/global_settings.json.
// Auto-detection (GPU name, session type) is done here with the same
// logic the Python module used, via nvidia-smi and env-var inspection.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct GlobalSettings {
    // User-controlled
    #[serde(default = "default_dlss_preset")]
    pub dlss_preset:        String,
    #[serde(default)]                    pub dlss_indicator:     bool,
    #[serde(default = "default_true")]   pub dlss_upgrade:       bool,
    #[serde(default)]                    pub wayland:            bool,
    #[serde(default)]                    pub perf_mode:          bool,
    #[serde(default)]                    pub hdr:                bool,
    #[serde(default)]                    pub auto_disable_secondary_on_launch: bool,
    // Launch Steam minimised to tray (`steam -silent`) when it isn't already
    // running, and start the game with `steam -applaunch` instead of the
    // steam:// URL, whose desktop activation is what raises Steam over the
    // game. Steam has no option to hide its tray ICON , that stays.
    #[serde(default = "default_true")]   pub steam_silent_launch: bool,
    // Explicit upstream Proton for the wrapper (`proton` script or its
    // directory). Empty = auto-detect.
    #[serde(default)]                    pub proton_upstream:    String,
    // GreenBoost runtime feature toggles
    #[serde(default)]                    pub nis_enable:         bool,
    #[serde(default)]                    pub nis_dispatch:       bool,
    #[serde(default = "default_true")]   pub gplasync:           bool,
    #[serde(default = "default_true")]   pub perf_lock:          bool,
    #[serde(default = "default_true")]   pub compositor_suspend: bool,
    #[serde(default = "default_true")]   pub ddr_prewarm:        bool,
    #[serde(default = "default_true")]   pub memlock_unlimited:  bool,
    #[serde(default = "default_true")]   pub vk_pipeline_cache:  bool,
    #[serde(default = "default_true")]   pub vk_queue_priority:  bool,
    #[serde(default = "default_true")]   pub vk_memory_priority: bool,
    // C3/C4: Frame-pacing, Reflex, NIS tuning, advanced
    #[serde(default = "default_nis_sharpness")]      pub nis_sharpness:       f64,
    #[serde(default = "default_nis_scale")]          pub nis_scale:           f64,
    #[serde(default)]                                pub reflex_enable:       bool,
    #[serde(default)]                                pub fps_cap:             i32,
    #[serde(default)]                                pub stream_priority:     bool,
    #[serde(default)]                                pub vk_debug:            bool,
    #[serde(default)]                                pub nvapi_hud:           bool,
    /// On-screen GPU/FPS overlay via MangoHud , works regardless of which
    /// translation layer a game uses (DXVK, vkd3d-proton, or native
    /// Vulkan), unlike nvapi_hud below which only affects DXVK's own HUD.
    /// Per-game GameWrappers.mangohud (if explicitly true) still forces it
    /// on for that one game even when this is off.
    #[serde(default)]                                pub mangohud_enabled:    bool,
    #[serde(default = "default_vk_overflow_min_mb")] pub vk_overflow_min_mb:  i32,
    #[serde(default)]                                pub vk_t3_min_mb:        i32,
    #[serde(default = "default_log_ttl_days")]       pub log_ttl_days:        i32,
    #[serde(default)]                                pub shader_threads:      i32,
    #[serde(default = "default_shader_cache_gb")]    pub shader_cache_gb:     i32,
    #[serde(default = "default_gplasync_version")]   pub gplasync_version:    String,
    /// Comma-separated VKD3D_CONFIG flags, e.g. "dxr,pipeline_library_app_cache".
    /// Empty string = let vkd3d-proton use its own defaults.
    #[serde(default)]                                pub vkd3d_config:        String,
    /// GREENBOOST_AFFINITY , "all" | "pcores" | "numa". "all" (default):
    /// every logical CPU stays schedulable. Confirmed live 2026-08-07 that a
    /// 150+-thread UE4 title left 16 of 32 threads idle under the previous
    /// hard-coded P-cores-only pin; "pcores"/"numa" remain selectable for
    /// titles that measurably prefer less scheduler jitter on the render
    /// thread. See greenboost_proton/proton's GREENBOOST_AFFINITY handling.
    #[serde(default = "default_affinity_mode")]      pub affinity_mode:       String,
    // OpenGL LD_PRELOAD layer (mirrors gb_gaming.global_settings.GlobalSettings ,
    // keep both structs in sync when adding fields here).
    #[serde(default = "default_true")]               pub gl_layer_enabled:    bool,
    #[serde(default = "default_gl_overflow_min_mb")] pub gl_overflow_min_mb:  i32,
    /// What the window's close button does , "tray" (default) hides the
    /// Suite to the system tray and leaves it running, "quit" exits as it
    /// always did. A window the user cannot get back would be worse than
    /// either, so this is user-visible, not a hidden default.
    #[serde(default = "default_close_action")]       pub close_action:        String,
    /// Whether quitting the Suite also stops the game it launched , the
    /// behaviour Steam has. Off means a game outlives the Suite, which is
    /// the pre-0.1.2 behaviour and still a legitimate choice.
    #[serde(default = "default_true")]               pub stop_game_on_quit:   bool,
    // Auto-detected (read-only on the UI side)
    #[serde(default)]                    pub detected_session:   String,
    #[serde(default)]                    pub detected_gpu:       String,
    #[serde(default)]                    pub detected_series:    String,
    #[serde(default = "default_preset")] pub recommended_preset: String,
}

fn default_true() -> bool { true }
fn default_dlss_preset() -> String { "auto".to_string() }
fn default_preset() -> String { "render_preset_m".to_string() }
fn default_nis_sharpness() -> f64 { 0.5 }
fn default_nis_scale() -> f64 { 1.0 }
fn default_vk_overflow_min_mb() -> i32 { 32 }
fn default_log_ttl_days() -> i32 { 14 }
fn default_shader_cache_gb() -> i32 { 8 }
fn default_gplasync_version() -> String { "current".to_string() }
fn default_gl_overflow_min_mb() -> i32 { 32 }
fn default_affinity_mode() -> String { "all".to_string() }
fn default_close_action() -> String { "tray".to_string() }

impl Default for GlobalSettings {
    fn default() -> Self {
        Self {
            dlss_preset:        default_dlss_preset(),
            dlss_indicator:     false,
            dlss_upgrade:       true,
            wayland:            false,
            perf_mode:          false,
            hdr:                false,
            auto_disable_secondary_on_launch: false,
            steam_silent_launch: true,
            proton_upstream:    String::new(),
            nis_enable:         false,
            nis_dispatch:       false,
            gplasync:           true,
            perf_lock:          true,
            compositor_suspend: true,
            ddr_prewarm:        true,
            memlock_unlimited:  true,
            vk_pipeline_cache:  true,
            vk_queue_priority:  true,
            vk_memory_priority: true,
            nis_sharpness:      default_nis_sharpness(),
            nis_scale:          default_nis_scale(),
            reflex_enable:      false,
            fps_cap:            0,
            // Always on, and no longer user-visible. Gaming priority while a
            // game runs is signalled through greenboost.ko's gaming_mode
            // (set by the Proton wrapper, read by the CUDA shim); with no
            // game running, inference should get the high-priority streams.
            // Presenting that as a toggle offered a choice that neither
            // outcome actually depended on.
            stream_priority:    true,
            vk_debug:           false,
            nvapi_hud:          false,
            mangohud_enabled:   false,
            vk_overflow_min_mb: default_vk_overflow_min_mb(),
            vk_t3_min_mb:       0,
            log_ttl_days:       default_log_ttl_days(),
            shader_threads:     0,
            shader_cache_gb:    default_shader_cache_gb(),
            gplasync_version:   default_gplasync_version(),
            vkd3d_config:       String::new(),
            affinity_mode:      default_affinity_mode(),
            gl_layer_enabled:   true,
            gl_overflow_min_mb: default_gl_overflow_min_mb(),
            close_action:       default_close_action(),
            stop_game_on_quit:  true,
            detected_session:   String::new(),
            detected_gpu:       String::new(),
            detected_series:    String::new(),
            recommended_preset: default_preset(),
        }
    }
}

/// One DLSS preset choice , id (the env-var value) + label (UI text).
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct DlssPresetChoice {
    pub id:    String,
    pub label: String,
}

// ── Detection helpers ──────────────────────────────────────────────────────

pub fn detect_session_type() -> String {
    let xdg = std::env::var("XDG_SESSION_TYPE").unwrap_or_default().to_lowercase();
    if xdg == "wayland" || xdg == "x11" { return xdg; }
    if std::env::var("WAYLAND_DISPLAY").is_ok() { return "wayland".to_string(); }
    if std::env::var("DISPLAY").is_ok()         { return "x11".to_string(); }
    String::new()
}

fn detect_gpu() -> (String, String) {
    let out = Command::new("nvidia-smi")
        .args(["--query-gpu=name", "--format=csv,noheader,nounits"])
        .output();
    let name = out.ok()
        .and_then(|o| if o.status.success() {
            String::from_utf8(o.stdout).ok()
        } else { None })
        .map(|s| s.lines().next().unwrap_or("").trim().to_string())
        .unwrap_or_default();

    let series = parse_series(&name);
    (name, series)
}

fn parse_series(name: &str) -> String {
    // RTX XYYY → "RTX X0" (tens digit of model number gives the generation)
    let upper = name.to_ascii_uppercase();
    if let Some(pos) = upper.find("RTX ") {
        let rest = &upper[pos + 4..];
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if digits.len() >= 3 {
            let gen = &digits[..digits.len() - 2];
            return format!("RTX {gen}0");
        }
    }
    if let Some(pos) = upper.find("GTX ") {
        let rest = &upper[pos + 4..];
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !digits.is_empty() {
            return format!("GTX {}", &digits[..digits.len().min(2)]);
        }
    }
    String::new()
}

fn recommended_preset_for(series: &str) -> String {
    match series {
        s if s.starts_with("RTX 20") || s.starts_with("RTX 30") => "render_preset_k".to_string(),
        s if s.starts_with("RTX 40") || s.starts_with("RTX 50") => "render_preset_m".to_string(),
        _ => "render_preset_latest".to_string(),
    }
}

fn fill_detected(s: &mut GlobalSettings) {
    s.detected_session   = detect_session_type();
    let (gpu, series)    = detect_gpu();
    s.detected_gpu       = gpu;
    s.detected_series    = series.clone();
    s.recommended_preset = recommended_preset_for(&series);
}

// ── Config path ────────────────────────────────────────────────────────────

fn config_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".config")
        .join("greenboost-gaming")
        .join("global_settings.json")
}

// ── Load / save ────────────────────────────────────────────────────────────

pub fn get_impl() -> Result<GlobalSettings, String> {
    let path = config_path();
    let mut s = if path.exists() {
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("read config: {e}"))?;
        serde_json::from_str::<GlobalSettings>(&text)
            .unwrap_or_default()
    } else {
        let mut s = GlobalSettings::default();
        // First-run smart defaults
        fill_detected(&mut s);
        s.wayland = s.detected_session == "wayland";
        let _ = write_config(&s);
        return Ok(s);
    };
    fill_detected(&mut s);
    Ok(s)
}

pub fn set_impl(mut s: GlobalSettings) -> Result<String, String> {
    fill_detected(&mut s);
    write_config(&s)?;
    write_env_file(&s);
    apply_hdr_system(&s);
    Ok("ok".to_string())
}

/// Write ~/.config/environment.d/greenboost.conf so env vars are available
/// to native Vulkan games and any tool that sources systemd environment.d.
/// This complements the Python global_settings.as_env_dict() path used by
/// the Proton wrapper , native (non-Proton) games pick up from here.
fn write_env_file(s: &GlobalSettings) {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let dir = std::path::PathBuf::from(&home).join(".config").join("environment.d");
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("greenboost.conf");

    // Resolve DLSS preset
    let chosen = if s.dlss_preset == "auto" {
        s.recommended_preset.clone()
    } else {
        s.dlss_preset.clone()
    };

    let mut lines: Vec<String> = vec![
        "# Generated by GreenBoost Gaming Suite , do not edit manually".into(),
    ];

    // DLSS preset
    if !chosen.is_empty() && chosen != "off" {
        lines.push("DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE=on".into());
        if chosen != "default" {
            lines.push(format!("DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION={chosen}"));
        }
    }
    if s.dlss_indicator { lines.push("PROTON_DLSS_INDICATOR=1".into()); }
    if s.dlss_upgrade   { lines.push("PROTON_DLSS_UPGRADE=1".into()); }
    if s.wayland        { lines.push("PROTON_ENABLE_WAYLAND=1".into()); }
    if s.hdr            { lines.push("ENABLE_HDR_WSI=1".into()); }

    // NIS / Vulkan layer toggles
    lines.push(format!("GREENBOOST_NIS={}", if s.nis_enable    { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_NIS_DISPATCH={}", if s.nis_dispatch { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_NIS_SHARPNESS={:.3}", s.nis_sharpness));
    lines.push(format!("GREENBOOST_NIS_SCALE={:.3}", s.nis_scale));
    lines.push(format!("GREENBOOST_GPLASYNC={}", if s.gplasync { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_PERF_LOCK={}", if s.perf_lock { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_COMPOSITOR_SUSPEND={}", if s.compositor_suspend { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_DDR_PREWARM={}", if s.ddr_prewarm { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_MEMLOCK_UNLIMITED={}", if s.memlock_unlimited { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_VK_PIPELINE_CACHE={}", if s.vk_pipeline_cache { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_VK_QUEUE_PRIORITY={}", if s.vk_queue_priority { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_VK_MEMORY_PRIORITY={}", if s.vk_memory_priority { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_REFLEX={}", if s.reflex_enable { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_STREAM_PRIORITY={}", if s.stream_priority { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_VK_DEBUG={}", if s.vk_debug { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_NVAPI_HUD={}", if s.nvapi_hud { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_MANGOHUD_DEFAULT={}", if s.mangohud_enabled { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_VK_OVERFLOW_MIN_MB={}", s.vk_overflow_min_mb));
    lines.push(format!("GREENBOOST_LOG_TTL_DAYS={}", s.log_ttl_days));
    lines.push(format!("GREENBOOST_SHADER_CACHE_GB={}", s.shader_cache_gb));
    if s.fps_cap > 0 { lines.push(format!("DXVK_FRAME_RATE={}", s.fps_cap)); }
    if s.vk_t3_min_mb > 0   { lines.push(format!("GREENBOOST_VK_T3_MIN_MB={}", s.vk_t3_min_mb)); }
    if s.shader_threads > 0  { lines.push(format!("GREENBOOST_SHADER_THREADS={}", s.shader_threads)); }
    if s.gplasync_version != "current" && !s.gplasync_version.is_empty() {
        lines.push(format!("GREENBOOST_GPLASYNC_VERSION={}", s.gplasync_version));
    }
    if !s.vkd3d_config.is_empty() { lines.push(format!("VKD3D_CONFIG={}", s.vkd3d_config)); }
    lines.push(format!("GREENBOOST_AFFINITY={}",
        if s.affinity_mode.is_empty() { "all" } else { s.affinity_mode.as_str() }));

    // OpenGL layer , same GREENBOOST_OPENGL / GREENBOOST_GL_OVERFLOW_MIN_MB pair
    // the Python as_env_dict() path exports for the Proton wrapper; native
    // (non-Proton) Vulkan/OpenGL games only pick these up from this file.
    lines.push(format!("GREENBOOST_OPENGL={}", if s.gl_layer_enabled { "1" } else { "0" }));
    lines.push(format!("GREENBOOST_GL_OVERFLOW_MIN_MB={}", s.gl_overflow_min_mb));

    let content = lines.join("\n") + "\n";
    let tmp = path.with_extension("tmp");
    if std::fs::write(&tmp, &content).is_ok() {
        let _ = std::fs::rename(&tmp, &path);
    }
}

/// Apply HDR setting to the compositor immediately when the user saves
/// global settings.  Only acts on the primary/first display.
fn apply_hdr_system(s: &GlobalSettings) {
    // Use "primary" as display name , let the HDR function find the right output.
    let _ = crate::manager::set_hdr_enabled("primary".to_string(), s.hdr);
}

fn write_config(s: &GlobalSettings) -> Result<(), String> {
    let path = config_path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)
            .map_err(|e| format!("mkdir config dir: {e}"))?;
    }
    let tmp = path.with_extension("tmp");
    let json = serde_json::to_string_pretty(s)
        .map_err(|e| format!("serialize: {e}"))?;
    std::fs::write(&tmp, &json)
        .map_err(|e| format!("write config: {e}"))?;
    std::fs::rename(&tmp, &path)
        .map_err(|e| format!("atomic rename: {e}"))?;
    Ok(())
}

// ── Settings profiles ──────────────────────────────────────────────────────

fn gs_profiles_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".config")
        .join("greenboost-gaming")
        .join("global-profiles")
}

fn sanitize_profile_name(s: &str) -> String {
    s.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == ' ' { c } else { '_' })
        .collect::<String>()
        .trim()
        .to_string()
}

pub fn list_gs_profiles_impl() -> Result<Vec<String>, String> {
    let dir = gs_profiles_dir();
    if !dir.exists() { return Ok(vec![]); }
    let mut names: Vec<String> = std::fs::read_dir(&dir)
        .map_err(|e| format!("read dir: {e}"))?
        .filter_map(|e| {
            let e = e.ok()?;
            let name = e.file_name().into_string().ok()?;
            name.strip_suffix(".json").map(|n| n.to_string())
        })
        .collect();
    names.sort();
    Ok(names)
}

pub fn save_gs_profile_impl(name: &str, mut s: GlobalSettings) -> Result<(), String> {
    let dir = gs_profiles_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir: {e}"))?;
    s.detected_session   = String::new();
    s.detected_gpu       = String::new();
    s.detected_series    = String::new();
    s.recommended_preset = String::new();
    let safe = sanitize_profile_name(name);
    let path = dir.join(format!("{safe}.json"));
    let json = serde_json::to_string_pretty(&s).map_err(|e| format!("serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {e}"))?;
    Ok(())
}

pub fn load_gs_profile_impl(name: &str) -> Result<Option<GlobalSettings>, String> {
    let dir = gs_profiles_dir();
    let safe = sanitize_profile_name(name);
    let path = dir.join(format!("{safe}.json"));
    if !path.exists() { return Ok(None); }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read: {e}"))?;
    let mut s: GlobalSettings = serde_json::from_str(&text).map_err(|e| format!("parse: {e}"))?;
    fill_detected(&mut s);
    Ok(Some(s))
}

pub fn delete_gs_profile_impl(name: &str) -> Result<(), String> {
    let dir = gs_profiles_dir();
    let safe = sanitize_profile_name(name);
    let path = dir.join(format!("{safe}.json"));
    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| format!("remove: {e}"))?;
    }
    Ok(())
}

// ── DLSS preset list ────────────────────────────────────────────────────────

pub fn preset_choices_impl() -> Result<Vec<DlssPresetChoice>, String> {
    Ok(vec![
        DlssPresetChoice { id: "auto".into(),                label: "Auto (recommended for your GPU)".into() },
        DlssPresetChoice { id: "default".into(),             label: "Default (game decides)".into() },
        DlssPresetChoice { id: "off".into(),                 label: "Off (no override)".into() },
        DlssPresetChoice { id: "render_preset_k".into(),     label: "Preset K , best on RTX 20 / 30".into() },
        DlssPresetChoice { id: "render_preset_m".into(),     label: "Preset M , best on RTX 40 / 50".into() },
        DlssPresetChoice { id: "render_preset_l".into(),     label: "Preset L , maximum sharpness (more artifacts)".into() },
        DlssPresetChoice { id: "render_preset_latest".into(),label: "Latest / Recommended (NVIDIA's current pick)".into() },
    ])
}
