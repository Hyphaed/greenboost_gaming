// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost Gaming Suite , GPU profile save/load bridge.
//
// Profile shape mirrors gb_gaming.gpu_profile.Profile so the Python
// side stays the single source of truth.  These Tauri commands shell
// out to `python3 -c "..."` , the same pattern used for the DLSS
// source picker in sources.rs.
//
// Why no direct serde-Rust of the profile JSON?  Because the Python
// module already handles edge cases (validation, atomic writes,
// directory creation, file-permission compat).  Reimplementing that
// in Rust just so we can avoid a subprocess invocation would double
// the maintenance surface for no real benefit , profile save/load
// happens once on click, not in a hot loop.

use serde::{Deserialize, Serialize};
use std::process::Command;

/// Anchor point on the fan curve: (temperature °C, fan percent).
pub type FanCurvePoint = (i32, i32);

/// A GPU profile that mirrors gb_gaming.gpu_profile.Profile exactly.
/// Field names match the Python dataclass so the JSON round-trips
/// without translation.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct GpuProfile {
    pub name: String,
    pub power_limit_w:   Option<f32>,
    pub core_offset_mhz: Option<i32>,
    pub mem_offset_mhz:  Option<i32>,
    /// Sorted ascending by temperature.  Empty list = "leave fan in
    /// auto" (matches gnome-control-center / nvidia-settings default).
    pub fan_curve: Vec<FanCurvePoint>,
}

fn python(arg: &str) -> Result<String, String> {
    let out = Command::new("python3")
        .args(["-c", arg])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

fn _py_bootstrap() -> String {
    crate::py_bootstrap::py_bootstrap()
}

pub fn list_profiles_impl() -> Result<Vec<String>, String> {
    let script = format!(
        "{bootstrap}\nimport json\nfrom gb_gaming import gpu_profile\nprint(json.dumps(gpu_profile.list_profiles()))",
        bootstrap = _py_bootstrap());
    let s = python(&script)?;
    serde_json::from_str(&s).map_err(|e| format!("bad JSON: {e}"))
}

pub fn load_profile_impl(name: &str) -> Result<Option<GpuProfile>, String> {
    let name_lit = serde_json::to_string(name).map_err(|e| e.to_string())?;
    let script = format!(r#"
{bootstrap}
import json
from gb_gaming import gpu_profile
p = gpu_profile.load_profile({name_lit})
if p is None:
    print("null")
else:
    print(p.to_json())
"#, bootstrap = _py_bootstrap());
    let s = python(&script)?;
    if s.trim() == "null" {
        return Ok(None);
    }
    serde_json::from_str(&s).map(Some).map_err(|e| format!("bad JSON: {e}"))
}

/// Tell the fan daemon to start following `name` (or clear, when None).
/// Writes ~/.config/greenboost-gaming/active_profile.json atomically;
/// the daemon polls that file every 5 s.
pub fn set_active_profile_impl(name: Option<&str>) -> Result<String, String> {
    let name_arg = match name {
        Some(n) => serde_json::to_string(n).map_err(|e| e.to_string())?,
        None    => "None".to_string(),
    };
    let script = format!(r#"
{bootstrap}
from gb_gaming import gpu_profile
gpu_profile.set_active_profile({name_arg})
print("ok")
"#, bootstrap = _py_bootstrap());
    python(&script)
}

/// Return the profile name the daemon is currently tracking, or None.
pub fn get_active_profile_impl() -> Result<Option<String>, String> {
    let script = format!(r#"
{bootstrap}
import json
from gb_gaming import gpu_profile
print(json.dumps(gpu_profile.get_active_profile()))
"#, bootstrap = _py_bootstrap());
    let s = python(&script)?;
    serde_json::from_str(&s).map_err(|e| format!("bad JSON: {e}"))
}

// ── Game-setting profiles (per-game overrides snapshots) ───────────────────

fn game_setting_profiles_dir() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home)
        .join(".config")
        .join("greenboost-gaming")
        .join("game-profiles")
}

fn sanitize_name(s: &str) -> String {
    s.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == ' ' { c } else { '_' })
        .collect::<String>()
        .trim()
        .to_string()
}

pub fn list_game_setting_profiles_impl() -> Result<Vec<String>, String> {
    let dir = game_setting_profiles_dir();
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

pub fn save_game_setting_profile_impl(name: &str, overrides: serde_json::Value) -> Result<(), String> {
    let dir = game_setting_profiles_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir: {e}"))?;
    let safe = sanitize_name(name);
    let path = dir.join(format!("{safe}.json"));
    let json = serde_json::to_string_pretty(&overrides).map_err(|e| format!("serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {e}"))?;
    Ok(())
}

pub fn load_game_setting_profile_impl(name: &str) -> Result<Option<serde_json::Value>, String> {
    let dir = game_setting_profiles_dir();
    let safe = sanitize_name(name);
    let path = dir.join(format!("{safe}.json"));
    if !path.exists() { return Ok(None); }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read: {e}"))?;
    serde_json::from_str(&text).map(Some).map_err(|e| format!("parse: {e}"))
}

pub fn delete_game_setting_profile_impl(name: &str) -> Result<(), String> {
    let dir = game_setting_profiles_dir();
    let safe = sanitize_name(name);
    let path = dir.join(format!("{safe}.json"));
    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| format!("remove: {e}"))?;
    }
    Ok(())
}

pub fn save_profile_impl(p: GpuProfile) -> Result<String, String> {
    // Pass the profile as JSON on stdin to avoid shell quoting issues
    // with names containing apostrophes / unicode / newlines.
    let blob = serde_json::to_string(&p).map_err(|e| e.to_string())?;
    let blob_lit = serde_json::to_string(&blob).map_err(|e| e.to_string())?;
    let script = format!(r#"
{bootstrap}
import json
from gb_gaming import gpu_profile
d = json.loads({blob_lit})
gpu_profile.save_profile(gpu_profile.Profile(
    name=d["name"],
    power_limit_w=d.get("power_limit_w"),
    core_offset_mhz=d.get("core_offset_mhz"),
    mem_offset_mhz=d.get("mem_offset_mhz"),
    fan_curve=[tuple(t) for t in d.get("fan_curve", [])],
))
print("ok")
"#, bootstrap = _py_bootstrap());
    python(&script)
}
