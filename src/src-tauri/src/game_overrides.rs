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
    /// Maps a lowercased DLL filename to the absolute directory (or
    /// directories) that already contain a DLL of that name , computed
    /// from the scanner's own `Game.dlls[].path` findings when the user
    /// picks a folder in the UI. Lets the overlay (and the "copy into
    /// game" action) target the directory a game actually loads a DLL
    /// from, not just the exe directory; a name with no entry here still
    /// falls back to the exe directory in the Proton wrapper. See
    /// `_external_dll_plan()` in greenboost_proton/gb_proton_main.py.
    #[serde(default)]
    pub external_dll_targets: HashMap<String, Vec<String>>,
    /// Lowercased filenames excluded from the overlay/copy-in even though
    /// they're present in `external_dll_dir , lets a user opt a specific
    /// file out (an experimental Neural Rendering / Deep DVC / DirectSR
    /// DLL they don't want linked in) without removing it from the shared
    /// folder. Empty = every *.dll in the folder is used, the original
    /// behaviour. See `_external_dll_plan()` in
    /// greenboost_proton/gb_proton_main.py and
    /// `install_external_folder_into_game()` in gb_gaming/dlss_updater.py.
    #[serde(default)]
    pub external_dll_disabled: Vec<String>,
    /// One-switch DLSS 5 (Neural Rendering), rewritten 2026-09-02 around
    /// GreenBoost's own DLSS Neural Rendering runtime , see gb_gaming/dlss5.py,
    /// gb_gaming/dlss5_runtime.py and docs/dlss5-external-folders.md for the
    /// mechanism and why an earlier one (patching a leaked, Control-specific
    /// RenoDX add-on's process-name check) was replaced rather than
    /// extended, and why ReShade support was dropped entirely , the runtime
    /// needs neither ReShade nor a Streamline DLL swap. Independent of
    /// external_dlls_enabled.
    #[serde(default)]
    pub dlss5_enabled: bool,
    /// The DLL name the DLSS5 runtime was installed as for this game (e.g.
    /// "winmm.dll"), set by the Suite after a successful dlss5_enable call.
    /// The Proton wrapper appends `<stem>=n,b` to WINEDLLOVERRIDES for this
    /// value when dlss5_enabled is true , see
    /// greenboost_proton/gb_proton_main.py. "" = not installed yet.
    #[serde(default)]
    pub dlss5_proxy_dll: String,
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
