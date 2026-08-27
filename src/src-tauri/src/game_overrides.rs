// Copyright 2026 Ferran Duarri , GPL v2
// Per-game GreenBoost override settings.
//
// Config path: ~/.config/greenboost-gaming/per-game/<appid>.json
// The Proton wrapper reads this same file at launch time.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
pub struct GameWrappers {
    #[serde(default)]
    pub gamemode: bool,
    #[serde(default)]
    pub mangohud: bool,
    #[serde(default)]
    pub gamescope: Vec<String>,  // empty = disabled; non-empty = gamescope args
}

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
pub struct GameNisConfig {
    pub enabled:   bool,
    #[serde(default = "default_sharpness")]
    pub sharpness: f64,
    #[serde(default = "default_scale")]
    pub scale:     f64,
}
fn default_sharpness() -> f64 { 0.5 }
fn default_scale()     -> f64 { 1.0 }

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
pub struct GameOverrides {
    /// 0 = no cap (uses global).
    #[serde(default)]
    pub fps_cap:     i32,
    /// NIS config block; None = use global NIS setting.
    #[serde(default)]
    pub nis:         Option<GameNisConfig>,
    /// Override HDR for this game; false = use global.
    #[serde(default)]
    pub hdr:         bool,
    /// DLSS render preset override; "" = use global.
    #[serde(default)]
    pub dlss_preset: String,
    /// Enable NVIDIA Reflex for this game.
    #[serde(default)]
    pub reflex:      bool,
    /// CPU governor override; "" = use global.
    #[serde(default)]
    pub governor:    String,
    /// Arbitrary env var overrides (applied via setdefault in Proton wrapper).
    #[serde(default)]
    pub env:         HashMap<String, String>,
    /// Launch wrappers (gamemode, mangohud, gamescope).
    #[serde(default)]
    pub wrappers:    Option<GameWrappers>,
    /// GPU profile name to auto-apply on launch; "" = no binding.
    /// Must match a saved profile in ~/.config/greenboost-gaming/profiles/.
    #[serde(default)]
    pub gpu_profile: String,
    /// Override GREENBOOST_GPLASYNC for this game. None = use global setting.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gplasync: Option<bool>,
    /// Override GREENBOOST_PERF_LOCK for this game. None = use global setting.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub perf_lock: Option<bool>,
    /// Override GREENBOOST_COMPOSITOR_SUSPEND for this game. None = use global.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compositor_suspend: Option<bool>,
    /// Override GREENBOOST_VK_PIPELINE_CACHE for this game. None = use global.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vk_pipeline_cache: Option<bool>,
    /// Overlay a user-supplied directory of DLLs into the game's own exe
    /// directory at launch (symlinked, with any colliding shipped DLL backed
    /// up and restored on exit). GreenBoost never downloads, extracts, or
    /// redistributes anything placed there , the user is responsible for
    /// obtaining the files and has the rights to use them.
    #[serde(default)]
    pub external_dlls_enabled: bool,
    /// Directory the overlay above reads from; "" = unset.
    #[serde(default)]
    pub external_dll_dir: String,
}

fn per_game_path(appid: &str) -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".config")
        .join("greenboost-gaming")
        .join("per-game")
        .join(format!("{appid}.json"))
}

pub fn get_impl(appid: &str) -> Result<GameOverrides, String> {
    let path = per_game_path(appid);
    if !path.exists() {
        return Ok(GameOverrides::default());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("read: {e}"))?;
    serde_json::from_str(&text).map_err(|e| format!("parse: {e}"))
}

pub fn save_impl(appid: &str, overrides: &GameOverrides) -> Result<(), String> {
    let path = per_game_path(appid);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("mkdir: {e}"))?;
    }
    let json = serde_json::to_string_pretty(overrides)
        .map_err(|e| format!("serialize: {e}"))?;
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, &json).map_err(|e| format!("write tmp: {e}"))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename: {e}"))?;
    Ok(())
}
