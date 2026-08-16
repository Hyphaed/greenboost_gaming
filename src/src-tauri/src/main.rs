// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod py_bootstrap;
mod game_overrides;
mod scanner;
mod optimizer;
mod manager;
mod dlss;
mod sources;
mod profiles;
mod global_settings;
mod nvml_reader;
mod live_stats;
mod nvidia_diag;
mod directstorage;
mod updates;

use crate::scanner::{Game, HiddenGame, scan_games};
use crate::optimizer::{SettingGroup, scan_game_settings, apply_optimization, revert_optimization, set_game_setting_impl};
use crate::manager::{
    SystemStatus, get_system_status, set_performance_mode,
    GpuInfo, get_gpu_info, apply_gpu_profile,
    AutoTuneResult, compute_auto_tune as backend_compute_auto_tune,
    GpuInfoAdvanced, get_gpu_advanced as backend_get_gpu_advanced,
    set_fan_manual, set_fan_auto,
    apply_fan_curve as backend_apply_fan_curve,
    enable_fan_control_streaming as backend_enable_fan_control_streaming,
    lock_gpu_clocks_max as backend_lock_gpu_clocks_max,
    reset_gpu_clocks    as backend_reset_gpu_clocks,
    set_power_limit_w   as backend_set_power_limit_w,
    reset_power_limit   as backend_reset_power_limit,
    DisplayInfo, get_displays, set_display_mode, set_display_rotation,
    set_display_scale as backend_set_display_scale,
    set_display_primary as backend_set_display_primary,
    set_display_arrangement as backend_set_display_arrangement,
    set_display_positions   as backend_set_display_positions,
    set_display_vrr         as backend_set_display_vrr,
    set_display_enabled     as backend_set_display_enabled,
    restore_all_displays_impl as backend_restore_all_displays,
    read_night_light        as backend_read_night_light,
    apply_night_light       as backend_apply_night_light,
    NightLightState, DisplayPosition,
    install_greenboost_layers, install_greenboost_proton,
    install_layers_streaming          as backend_install_layers_streaming,
    uninstall_layers_streaming        as backend_uninstall_layers_streaming,
    install_proton_streaming          as backend_install_proton_streaming,
    uninstall_proton_streaming        as backend_uninstall_proton_streaming,
    install_greenboost_module_streaming as backend_install_module_streaming,
    launch_game_ext                   as backend_launch_game_ext,
    set_hdr_enabled as backend_set_hdr_enabled,
    SessionRecord, get_session_history_impl as backend_get_session_history,
    GameAnalytics, analyze_game_sessions_impl as backend_analyze_game_sessions,
};
use tauri::ipc::Channel;
use crate::dlss::{
    DlssUpdateResult, update_game_dlss, restore_game_dlss,
    update_game_dlss_streaming     as backend_update_dlss_streaming,
    restore_game_dlss_streaming    as backend_restore_dlss_streaming,
    restore_game_dlss_to_original_streaming as backend_restore_dlss_to_original_streaming,
    sync_dlss_library_streaming    as backend_sync_dlss_library_streaming,
    sync_and_update_dlss_streaming as backend_sync_and_update_dlss_streaming,
    CachedDll,
    list_cached_dlls_impl          as backend_list_cached_dlls,
    install_cached_into_game_impl  as backend_install_cached_into_game,
    restore_single_dll_to_original_impl as backend_restore_single_dll_to_original,
    restore_dll_from_backup_impl as backend_restore_dll_from_backup,
};
use crate::sources::{
    DlssSourceState,
    get_dlss_source_state as backend_get_dlss_source_state,
    set_dlss_source as backend_set_dlss_source,
    list_dlss_versions_impl as backend_list_dlss_versions,
    set_dlss_pinned_tags_impl as backend_set_dlss_pinned_tags,
    get_dlss_status_impl as backend_get_dlss_status,
};
use crate::profiles::{
    GpuProfile,
    list_profiles_impl, load_profile_impl, save_profile_impl,
    get_active_profile_impl, set_active_profile_impl,
    list_game_setting_profiles_impl, save_game_setting_profile_impl,
    load_game_setting_profile_impl, delete_game_setting_profile_impl,
};
use crate::global_settings::{
    GlobalSettings, DlssPresetChoice,
    get_impl as backend_get_global_settings,
    set_impl as backend_set_global_settings,
    preset_choices_impl as backend_dlss_preset_choices,
    list_gs_profiles_impl, save_gs_profile_impl,
    load_gs_profile_impl, delete_gs_profile_impl,
};
use crate::game_overrides::{GameOverrides, get_impl as backend_get_game_overrides, save_impl as backend_save_game_overrides};

#[tauri::command]
fn get_games() -> Vec<Game> {
    scan_games()
}

#[tauri::command]
fn hide_game(path: String, name: String) -> Result<(), String> {
    scanner::hide_game_impl(path, name)
}

#[tauri::command]
fn unhide_game(path: String) -> Result<(), String> {
    scanner::unhide_game_impl(path)
}

#[tauri::command]
fn list_hidden_games() -> Vec<HiddenGame> {
    scanner::load_hidden_games()
}

/// Check GitLab for newer releases of this Suite and of GreenBoost core.
/// `force` skips the on-disk cache (the "Check now" button); the automatic
/// check on app start passes false so we don't hit GitLab every launch.
#[tauri::command]
fn check_updates(force: Option<bool>) -> updates::UpdateReport {
    updates::check_updates_impl(force.unwrap_or(false))
}

/// This binary's own version, so About doesn't carry a hand-maintained copy.
#[tauri::command]
fn get_suite_version() -> String {
    updates::suite_version()
}

#[tauri::command]
fn get_game_settings(path: String) -> Vec<SettingGroup> {
    scan_game_settings(std::path::Path::new(&path))
}

#[tauri::command]
fn get_status() -> SystemStatus {
    get_system_status()
}

#[tauri::command]
fn get_gpu() -> GpuInfo {
    get_gpu_info()
}

#[tauri::command]
fn get_gpu_advanced() -> GpuInfoAdvanced {
    backend_get_gpu_advanced()
}

#[tauri::command]
fn apply_gpu(core: i32, mem: i32, power: i32) -> Result<String, String> {
    apply_gpu_profile(core, mem, power)
}

#[tauri::command]
fn optimize_game(path: String) -> bool {
    apply_optimization(std::path::Path::new(&path))
}

#[tauri::command]
fn set_game_setting(path: String, key: String, value: String) -> bool {
    set_game_setting_impl(std::path::Path::new(&path), &key, &value)
}

#[tauri::command]
fn revert_game(path: String) -> bool {
    revert_optimization(std::path::Path::new(&path))
}

#[tauri::command]
fn update_dlss(path: String) -> Result<DlssUpdateResult, String> {
    Ok(update_game_dlss(std::path::Path::new(&path)))
}

#[tauri::command]
fn restore_dlss(path: String) -> Result<DlssUpdateResult, String> {
    Ok(restore_game_dlss(std::path::Path::new(&path)))
}

#[tauri::command]
fn set_perf_mode(enabled: bool) -> Result<String, String> {
    set_performance_mode(enabled)
}

#[tauri::command]
fn fan_manual(speed: i32) -> Result<String, String> {
    set_fan_manual(speed)
}

#[tauri::command]
fn fan_auto() -> Result<String, String> {
    set_fan_auto()
}

#[tauri::command]
fn apply_fan_curve_cmd(points: Vec<[f64; 2]>) -> Result<String, String> {
    backend_apply_fan_curve(points)
}

#[tauri::command]
fn gpu_auto_tune() -> AutoTuneResult {
    backend_compute_auto_tune()
}

#[tauri::command]
fn lock_gpu_clocks_max() -> Result<String, String> {
    backend_lock_gpu_clocks_max()
}

#[tauri::command]
fn reset_gpu_clocks() -> Result<String, String> {
    backend_reset_gpu_clocks()
}

#[tauri::command]
fn set_power_limit(watts: f32) -> Result<String, String> {
    backend_set_power_limit_w(watts)
}

#[tauri::command]
fn reset_power_limit() -> Result<String, String> {
    backend_reset_power_limit()
}

#[tauri::command]
fn query_displays() -> Vec<DisplayInfo> {
    get_displays()
}

#[tauri::command]
fn apply_display_mode(name: String, resolution: String, rate: f32) -> Result<String, String> {
    set_display_mode(name, resolution, rate)
}

#[tauri::command]
fn apply_display_rotation(name: String, rotation: String) -> Result<String, String> {
    set_display_rotation(name, rotation)
}

#[tauri::command]
fn install_layers() -> Result<String, String> {
    install_greenboost_layers()
}

#[tauri::command]
fn install_proton() -> Result<String, String> {
    install_greenboost_proton()
}

#[tauri::command]
fn launch_game(appid: String, disable_secondary_displays: Option<bool>)
    -> Result<String, String>
{
    backend_launch_game_ext(&appid, disable_secondary_displays.unwrap_or(false))
}

#[tauri::command]
fn apply_display_enabled(name: String, enabled: bool)
    -> Result<String, String>
{
    backend_set_display_enabled(name, enabled)
}

// PR-UUU: streaming install / uninstall.  React opens a Tauri
// `Channel<String>` and listens for line-by-line output via .onmessage.
#[tauri::command]
async fn install_layers_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_install_layers_streaming(channel)
}

#[tauri::command]
async fn uninstall_layers_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_uninstall_layers_streaming(channel)
}

#[tauri::command]
async fn install_proton_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_install_proton_streaming(channel)
}

#[tauri::command]
async fn uninstall_proton_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_uninstall_proton_streaming(channel)
}

#[tauri::command]
async fn install_module_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_install_module_streaming(channel)
}

// PR-VVV: streaming DLSS update / restore.  The React InstallStreamModal
// is reused , both flows surface line-by-line progress instead of the
// previous opaque toast message.
#[tauri::command]
async fn update_dlss_streaming(path: String, channel: Channel<String>)
    -> Result<i32, String>
{
    backend_update_dlss_streaming(std::path::Path::new(&path), &channel)
}

#[tauri::command]
async fn restore_dlss_streaming(path: String, channel: Channel<String>)
    -> Result<i32, String>
{
    backend_restore_dlss_streaming(std::path::Path::new(&path), &channel)
}

#[tauri::command]
async fn restore_dlss_to_original_streaming(path: String, channel: Channel<String>)
    -> Result<i32, String>
{
    backend_restore_dlss_to_original_streaming(std::path::Path::new(&path), &channel)
}

#[tauri::command]
async fn sync_dlss_library_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    backend_sync_dlss_library_streaming(&channel)
}

#[tauri::command]
async fn sync_and_update_dlss_streaming(path: String, channel: Channel<String>)
    -> Result<i32, String>
{
    backend_sync_and_update_dlss_streaming(std::path::Path::new(&path), &channel)
}

/// Write Coolbits=4 xorg.conf entry via pkexec/sudo so the user's next
/// session allows manual fan speed control through nvidia-settings.
#[tauri::command]
async fn enable_fan_control_streaming(channel: Channel<String>) -> Result<i32, String> {
    let send = move |msg: String| { let _ = channel.send(msg); };
    Ok(backend_enable_fan_control_streaming(send))
}

// PR-AAAA: per-game DLL picker.
#[tauri::command]
fn list_cached_dlls() -> Result<Vec<CachedDll>, String> {
    backend_list_cached_dlls()
}

#[tauri::command]
fn install_cached_dll(dll_name: String, game_path: String, version: Option<String>)
    -> Result<String, String>
{
    backend_install_cached_into_game(
        &dll_name, std::path::Path::new(&game_path), version.as_deref())
}

#[tauri::command]
fn restore_dll_to_original(dll_name: String, game_path: String) -> Result<String, String> {
    backend_restore_single_dll_to_original(&dll_name, std::path::Path::new(&game_path))
}

#[tauri::command]
fn restore_dll_from_backup(dll_name: String, game_path: String, backup_path: String)
    -> Result<String, String>
{
    backend_restore_dll_from_backup(&dll_name, std::path::Path::new(&game_path), &backup_path)
}

// PR-YYY: Global Settings , the NVIDIA-app-style "Global Settings"
// sub-tab inside the Games view.  Three commands: read current state
// (with auto-detection re-run), write user changes, list available
// DLSS preset options for the dropdown.
#[tauri::command]
fn get_global_settings() -> Result<GlobalSettings, String> {
    backend_get_global_settings()
}

#[tauri::command]
fn save_global_settings(settings: GlobalSettings) -> Result<String, String> {
    backend_set_global_settings(settings)
}

#[tauri::command]
fn list_dlss_preset_choices() -> Result<Vec<DlssPresetChoice>, String> {
    backend_dlss_preset_choices()
}

// PR-KKK: new display knobs (scale / primary / arrangement) wired to xrandr.
#[tauri::command]
fn apply_display_scale(name: String, percent: u32) -> Result<String, String> {
    backend_set_display_scale(name, percent)
}

#[tauri::command]
fn apply_display_primary(name: String) -> Result<String, String> {
    backend_set_display_primary(name)
}

#[tauri::command]
fn apply_display_arrangement(mode: String, displays: Vec<String>)
    -> Result<String, String>
{
    backend_set_display_arrangement(mode, displays)
}

#[tauri::command]
fn apply_display_positions(positions: Vec<DisplayPosition>)
    -> Result<String, String>
{
    backend_set_display_positions(positions)
}

#[tauri::command]
fn apply_display_vrr(name: String, enabled: bool) -> Result<String, String> {
    backend_set_display_vrr(name, enabled)
}

#[tauri::command]
fn restore_all_displays() -> Result<String, String> {
    backend_restore_all_displays()
}

#[tauri::command]
fn get_night_light() -> Result<NightLightState, String> {
    backend_read_night_light()
}

#[tauri::command]
fn apply_night_light(state: NightLightState) -> Result<String, String> {
    backend_apply_night_light(state)
}

#[tauri::command]
fn apply_hdr(name: String, enabled: bool) -> Result<String, String> {
    backend_set_hdr_enabled(name, enabled)
}

/// Linux NVIDIA driver settings (equivalent to nvidiaProfileInspector on Windows).
/// Returns the Steam launch options string for pasting into Steam > Properties.
#[tauri::command]
fn get_steam_launch_options(preset: String) -> Result<String, String> {
    crate::manager::get_steam_launch_options(&preset)
}

#[tauri::command]
fn apply_nvidia_system_settings(preset: String) -> Result<String, String> {
    crate::manager::apply_nvidia_system_settings(&preset)
}

#[derive(serde::Serialize)]
struct GpuMetrics {
    temp_c:         Option<i32>,
    power_w:        Option<f32>,
    power_limit_w:  Option<f32>,
    clock_gpu_mhz:  Option<u32>,
    clock_mem_mhz:  Option<u32>,
    gpu_util_pct:   Option<u32>,
    mem_util_pct:   Option<u32>,
    vram_used_mb:   Option<u64>,
    vram_total_mb:  Option<u64>,
    fan_pct:        Option<u32>,
    /// Human-readable throttle reasons from NVML. Empty = not throttling.
    throttle_reasons: Vec<String>,
    /// false when NVML couldn't tell us , distinct from "not throttling",
    /// so the UI can stay quiet instead of implying the GPU is healthy.
    throttle_known: bool,
}

#[tauri::command]
fn poll_gpu_metrics() -> GpuMetrics {
    match crate::nvml_reader::read_snapshot() {
        None => GpuMetrics {
            temp_c: None, power_w: None, power_limit_w: None,
            clock_gpu_mhz: None, clock_mem_mhz: None,
            gpu_util_pct: None, mem_util_pct: None,
            vram_used_mb: None, vram_total_mb: None, fan_pct: None,
            throttle_reasons: Vec::new(), throttle_known: false,
        },
        Some(s) => GpuMetrics {
            temp_c:        s.temp_gpu,
            power_w:       s.power_mw.map(|w| w as f32 / 1000.0),
            power_limit_w: s.power_limit_mw.map(|w| w as f32 / 1000.0),
            clock_gpu_mhz: s.clock_graphics,
            clock_mem_mhz: s.clock_mem,
            gpu_util_pct:  s.gpu_util,
            mem_util_pct:  s.mem_util,
            vram_used_mb:  s.mem_used.map(|b| b >> 20),
            vram_total_mb: s.mem_total.map(|b| b >> 20),
            fan_pct:       s.fan_speed_avg(),
            throttle_reasons: s.throttle_bits
                .map(|b| crate::nvml_reader::throttle_reasons(b)
                        .into_iter().map(str::to_string).collect())
                .unwrap_or_default(),
            throttle_known: s.throttle_bits.is_some(),
        },
    }
}

// C2: Live stats , stream the Vulkan layer syslog and send SIGUSR1/2 signals.
#[tauri::command]
async fn stream_layer_log(channel: Channel<String>) {
    live_stats::stream_layer_log_impl(channel);
}

#[tauri::command]
fn find_game_pid() -> Option<u32> {
    live_stats::find_game_pid_impl()
}

#[tauri::command]
fn send_sigusr1(pid: u32) -> Result<(), String> {
    live_stats::send_sigusr1_impl(pid)
}

#[tauri::command]
fn send_sigusr2(pid: u32) -> Result<(), String> {
    live_stats::send_sigusr2_impl(pid)
}

#[tauri::command]
fn get_dataflux_recent(limit: usize) -> Vec<serde_json::Value> {
    live_stats::get_dataflux_recent_impl(limit)
}

// G1: live, un-lagged T1/T2/T3 pool state straight from the kernel module's
// pool_brief sysfs file (see live_stats.rs doc comment).
#[tauri::command]
fn get_pool_brief() -> Option<live_stats::PoolBrief> {
    live_stats::get_pool_brief_impl()
}

// G2: governed GB-Semantics segment evaluation , surfaces
// gaming_inference_contention (AI inference competing with the running
// game for VRAM) as a warning banner in the UI.
#[tauri::command]
fn get_gaming_inference_contention() -> Result<serde_json::Value, String> {
    live_stats::get_gaming_inference_contention_impl()
}

// PR-III: DLSS source picker , exposes the hybrid-sourcing model (Streamline
// from NVIDIA's official GitHub, nvngx_*.dll from bundled / community mirror)
// to the Preferences panel.  Both commands shell out to the Python backend
// at gb_gaming.dlss_updater, which is the single source of truth.
#[tauri::command]
fn get_dlss_sources() -> Result<DlssSourceState, String> {
    backend_get_dlss_source_state()
}

#[tauri::command]
fn pick_dlss_source(id: String, custom_url: String) -> Result<String, String> {
    backend_set_dlss_source(&id, &custom_url)
}

#[tauri::command]
fn list_dlss_versions() -> Result<serde_json::Value, String> {
    backend_list_dlss_versions()
}

#[tauri::command]
fn set_dlss_pinned_tags(nvngx_tag: String, streamline_tag: String) -> Result<(), String> {
    backend_set_dlss_pinned_tags(&nvngx_tag, &streamline_tag)
}

// PR-JJJ: GreenWithEnvy-style profile save/load.  Shell out to the
// Python backend so the same profile files (~/.config/greenboost-
// gaming/profiles/*.json) can be edited via CLI tools too.
#[tauri::command]
fn list_gpu_profiles() -> Result<Vec<String>, String> {
    list_profiles_impl()
}

#[tauri::command]
fn load_gpu_profile(name: String) -> Result<Option<GpuProfile>, String> {
    load_profile_impl(&name)
}

#[tauri::command]
fn save_gpu_profile(profile: GpuProfile) -> Result<String, String> {
    save_profile_impl(profile)
}

#[tauri::command]
fn get_active_gpu_profile() -> Result<Option<String>, String> {
    get_active_profile_impl()
}

#[tauri::command]
fn set_active_gpu_profile(name: Option<String>) -> Result<String, String> {
    set_active_profile_impl(name.as_deref())
}

// ── Global Settings profiles ──────────────────────────────────────────────
#[tauri::command]
fn list_gs_profiles() -> Result<Vec<String>, String> {
    list_gs_profiles_impl()
}
#[tauri::command]
fn save_gs_profile(name: String, settings: GlobalSettings) -> Result<(), String> {
    save_gs_profile_impl(&name, settings)
}
#[tauri::command]
fn load_gs_profile(name: String) -> Result<Option<GlobalSettings>, String> {
    load_gs_profile_impl(&name)
}
#[tauri::command]
fn delete_gs_profile(name: String) -> Result<(), String> {
    delete_gs_profile_impl(&name)
}

// ── Game Setting profiles ─────────────────────────────────────────────────
#[tauri::command]
fn list_game_setting_profiles() -> Result<Vec<String>, String> {
    list_game_setting_profiles_impl()
}
#[tauri::command]
fn save_game_setting_profile(name: String, overrides: serde_json::Value) -> Result<(), String> {
    save_game_setting_profile_impl(&name, overrides)
}
#[tauri::command]
fn load_game_setting_profile(name: String) -> Result<Option<serde_json::Value>, String> {
    load_game_setting_profile_impl(&name)
}
#[tauri::command]
fn delete_game_setting_profile(name: String) -> Result<(), String> {
    delete_game_setting_profile_impl(&name)
}

#[tauri::command]
fn check_nvidia_update() -> crate::nvidia_diag::NvidiaUpdateStatus {
    crate::nvidia_diag::check_update()
}

/// Per-game DLSS/Streamline DLL version status: current vs latest-known,
/// so the UI can distinguish "not checked yet" from "up to date" from
/// "update available" , same three states as `check_nvidia_update`.
#[tauri::command]
fn get_dlss_status(path: String) -> Result<serde_json::Value, String> {
    backend_get_dlss_status(&path)
}

/// DirectStorage diagnostic for a selected game: whether it ships
/// dstorage.dll, whether the Proton build that will launch it is new
/// enough for vkd3d-proton's DirectStorage support, and whether the
/// install lives on NVMe storage , see directstorage.rs for why this is
/// detection/diagnostics only, not a behavior-changing toggle.
#[tauri::command]
fn get_directstorage_info(path: String) -> crate::directstorage::DirectStorageInfo {
    crate::directstorage::get_directstorage_info(std::path::Path::new(&path))
}

#[tauri::command]
async fn upgrade_nvidia_streaming(channel: tauri::ipc::Channel<String>) {
    crate::manager::upgrade_nvidia_streaming(channel);
}

#[tauri::command]
fn reboot_system() -> Result<(), String> {
    // `systemctl reboot` hands the request off to PID 1 and exits almost
    // immediately , it does not block until the machine actually reboots,
    // so waiting for it with .status() is safe and fast. The previous
    // .spawn() returned Ok(()) the instant the process forked, before
    // systemctl had even reported whether the request was accepted; a
    // denied request (no polkit auth, no root) still showed "success" to
    // the user while the machine stayed up.
    let status = std::process::Command::new("systemctl")
        .arg("reboot")
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("systemctl reboot exited with {status}"))
    }
}

#[tauri::command]
fn get_session_history() -> Vec<SessionRecord> {
    backend_get_session_history()
}

#[tauri::command]
fn analyze_game_sessions(appid: String) -> GameAnalytics {
    backend_analyze_game_sessions(&appid)
}

#[tauri::command]
fn get_game_overrides(appid: String) -> Result<GameOverrides, String> {
    backend_get_game_overrides(&appid)
}

#[tauri::command]
fn save_game_overrides(appid: String, overrides: GameOverrides) -> Result<(), String> {
    backend_save_game_overrides(&appid, &overrides)
}

fn main() {
    // Re-apply persisted performance mode before the window opens.
    if let Ok(settings) = crate::global_settings::get_impl() {
        if settings.perf_mode {
            let _ = crate::manager::set_performance_mode(true);
        }
    }

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            check_updates,
            get_suite_version,
            get_games,
            hide_game,
            unhide_game,
            list_hidden_games,
            get_game_settings,
            set_game_setting,
            get_status,
            get_gpu,
            get_gpu_advanced,
            apply_gpu,
            optimize_game,
            revert_game,
            update_dlss,
            restore_dlss,
            set_perf_mode,
            fan_manual,
            fan_auto,
            apply_fan_curve_cmd,
            enable_fan_control_streaming,
            gpu_auto_tune,
            lock_gpu_clocks_max,
            reset_gpu_clocks,
            set_power_limit,
            reset_power_limit,
            query_displays,
            apply_display_mode,
            apply_display_rotation,
            install_layers,
            install_proton,
            install_layers_streaming,
            uninstall_layers_streaming,
            install_proton_streaming,
            uninstall_proton_streaming,
            install_module_streaming,
            update_dlss_streaming,
            restore_dlss_streaming,
            restore_dlss_to_original_streaming,
            sync_dlss_library_streaming,
            sync_and_update_dlss_streaming,
            list_cached_dlls,
            install_cached_dll,
            restore_dll_to_original,
            restore_dll_from_backup,
            launch_game,
            apply_display_scale,
            apply_display_primary,
            apply_display_arrangement,
            apply_display_positions,
            apply_display_enabled,
            apply_display_vrr,
            restore_all_displays,
            get_dlss_sources,
            pick_dlss_source,
            list_dlss_versions,
            set_dlss_pinned_tags,
            list_gpu_profiles,
            load_gpu_profile,
            save_gpu_profile,
            get_active_gpu_profile,
            set_active_gpu_profile,
            list_gs_profiles,
            save_gs_profile,
            load_gs_profile,
            delete_gs_profile,
            list_game_setting_profiles,
            save_game_setting_profile,
            load_game_setting_profile,
            delete_game_setting_profile,
            get_global_settings,
            save_global_settings,
            list_dlss_preset_choices,
            apply_hdr,
            get_steam_launch_options,
            apply_nvidia_system_settings,
            poll_gpu_metrics,
            stream_layer_log,
            find_game_pid,
            get_dataflux_recent,
            get_pool_brief,
            get_gaming_inference_contention,
            send_sigusr1,
            send_sigusr2,
            reboot_system,
            get_night_light,
            apply_night_light,
            check_nvidia_update,
            upgrade_nvidia_streaming,
            get_dlss_status,
            get_directstorage_info,
            get_game_overrides,
            save_game_overrides,
            get_session_history,
            analyze_game_sessions,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
