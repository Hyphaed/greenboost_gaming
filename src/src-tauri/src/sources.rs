// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost Gaming Suite , DLSS DLL source picker bridge.
//
// These commands shell out to `python3 -m gb_gaming.dlss_updater` so the
// Tauri UI can read and write the user's DLL source preference without
// duplicating the policy logic in Rust.  The Python backend is the
// single source of truth , see gb_gaming/dlss_updater.py.
//
// We deliberately do NOT re-implement source policy here.  Tauri commands
// are a thin frontend; the trust boundary is the Python module.

use serde::{Deserialize, Serialize};
use std::process::Command;

/// One available DLL source the user can pick from in Preferences.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct DlssSource {
    /// Stable identifier: "bundled", "recol", "custom".
    pub id: String,
    /// User-visible label, e.g. "Bundled with Gaming Suite".
    pub label: String,
    /// One-line description shown in the Preferences card.
    pub description: String,
    /// Whether this is the currently active source.
    pub active: bool,
}

/// Result of `get_dlss_source_state` , drives the Preferences UI.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct DlssSourceState {
    pub sources: Vec<DlssSource>,
    /// Currently active source id ("bundled" / "recol" / "custom").
    pub active: String,
    /// Custom URL when active == "custom", else empty string.
    pub custom_url: String,
    /// Streamline DLLs always come from NVIDIA's official GitHub , this is
    /// a constant string the UI shows in the "Streamline" card.
    pub streamline_origin: String,
}

fn python_query(arg: &str) -> Result<String, String> {
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

/// Read the current source state.  The Python module reads
/// /etc/greenboost-gaming/sources.conf and the per-user override.
pub fn get_dlss_source_state() -> Result<DlssSourceState, String> {
    let body = r#"
cfg = d.get_sources()
state = {
    "sources": [
        {"id": "bundled", "label": "Bundled with Gaming Suite",
         "description": "DLLs ship inside the app; populated by the packager from NVIDIA's official DLSS SDK at developer.nvidia.com. Hash-verified before install.",
         "active": cfg.nvngx_source == "bundled"},
        {"id": "nvidia-github", "label": "NVIDIA/DLSS GitHub (official)",
         "description": "github.com/NVIDIA/DLSS , official NVIDIA repo. Always fetches the latest tagged release (or NVIDIA_DLSS_GH_TAG override). No third party.",
         "active": cfg.nvngx_source == "nvidia-github"},
        {"id": "recol", "label": "Community mirror (Recol)",
         "description": "github.com/Recol/DLSS-Updater-DLLs , community-maintained. Use if the bundled DLLs are outdated and you trust the mirror.",
         "active": cfg.nvngx_source == "recol"},
        {"id": "custom", "label": "Custom mirror URL",
         "description": "Any base URL of your choice; the Suite will GET <url>/<dll_name>. For internal corporate mirrors.",
         "active": cfg.nvngx_source == "custom"},
    ],
    "active": cfg.nvngx_source,
    "custom_url": cfg.nvngx_custom_url,
    "streamline_origin": "github.com/NVIDIAGameWorks/Streamline (official, public Releases API)",
}
print(json.dumps(state))
"#;
    let script = format!(
        "{bootstrap}import json\nfrom gb_gaming import dlss_updater as d\n{body}",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = python_query(&script)?;
    serde_json::from_str(&stdout).map_err(|e| format!("bad JSON from python: {e}"))
}

/// Fetch available release tags from NVIDIA/DLSS and NVIDIAGameWorks/Streamline.
/// Returns a JSON value matching list_available_dlss_tags() in dlss_updater.py.
pub fn list_dlss_versions_impl() -> Result<serde_json::Value, String> {
    let script = format!(
        "{bootstrap}import json\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.list_available_dlss_tags()))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = python_query(&script)?;
    serde_json::from_str(&stdout).map_err(|e| format!("bad JSON from python: {e}"))
}

/// Persist DLSS/Streamline pinned release tags to sources.conf.
/// Pass empty string for either tag to revert to 'latest'.
pub fn set_dlss_pinned_tags_impl(nvngx_tag: &str, streamline_tag: &str) -> Result<(), String> {
    let nvngx_lit = serde_json::to_string(nvngx_tag).map_err(|e| e.to_string())?;
    let sl_lit    = serde_json::to_string(streamline_tag).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}from gb_gaming import dlss_updater as d\nd.set_pinned_tags(nvngx_tag={nvngx_lit}, streamline_tag={sl_lit})\nprint(\"ok\")\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    python_query(&script)?;
    Ok(())
}

/// Per-DLL update status for one game, driven by
/// `gb_gaming.dlss_updater.summary()` , the same PE-version-resource scan
/// that backs `sync_and_update_dlss_streaming`. Reused here read-only so the
/// UI can show "checked vs not yet checked" and "up to date vs update
/// available" per file, before the user commits to an actual update.
pub fn get_dlss_status_impl(path: &str) -> Result<serde_json::Value, String> {
    let path_lit = serde_json::to_string(path).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}import json\nfrom pathlib import Path\nfrom gb_gaming import dlss_updater as d\nprint(json.dumps(d.summary([Path({path_lit})])))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let stdout = python_query(&script)?;
    serde_json::from_str(&stdout).map_err(|e| format!("bad JSON from python: {e}"))
}

/// Persist a new source pick.  Writes to
/// ~/.config/greenboost-gaming/sources.conf.
pub fn set_dlss_source(id: &str, custom_url: &str) -> Result<String, String> {
    // Strict whitelist on the Rust side too, even though the Python
    // module validates again.  Keep the trust boundary tight.
    if !matches!(id, "bundled" | "nvidia-github" | "recol" | "custom") {
        return Err(format!("unknown source id: {id}"));
    }
    let custom = if id == "custom" { custom_url } else { "" };
    // Escape the strings via serde so we don't have to worry about shell
    // metacharacters in the user's custom URL.
    let id_lit = serde_json::to_string(id).map_err(|e| e.to_string())?;
    let url_lit = serde_json::to_string(custom).map_err(|e| e.to_string())?;
    let script = format!(
        "{bootstrap}from gb_gaming import dlss_updater as d\nd.set_nvngx_source({id_lit}, custom_url={url_lit})\nprint(\"ok\")\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    python_query(&script)
}
