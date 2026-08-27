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
mod nonsteam;
mod game_lifecycle;

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
    upgrade_suite_streaming           as backend_upgrade_suite_streaming,
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

/// Launch a game. **async on purpose** , see the body.
#[tauri::command]
async fn launch_game(appid: String, disable_secondary_displays: Option<bool>)
    -> Result<String, String>
{
    // Runs on a blocking worker, never on the webview's command thread.
    //
    // The body waits on real things: up to 20 s for Steam to come up when it
    // starts it with -silent, plus process spawns. A synchronous #[command]
    // does that work on the thread the UI is driven from, so any stall there
    // is indistinguishable from a crash , on 2026-08-21 a blocking wait on
    // `steam -applaunch` froze the Suite hard enough that the desktop offered
    // "Force Quit", and no launch status ever reached the Games view because
    // the function never returned to publish one. The deadlock itself is
    // fixed in launch_game_ext; this keeps a future slow path from ever
    // costing the UI again.
    let out = tauri::async_runtime::spawn_blocking(move || {
        let r = backend_launch_game_ext(&appid, disable_secondary_displays.unwrap_or(false));
        // Steam hands back no process handle, so the appid is the only thread
        // we have back to this game when the user asks us to stop it.
        if r.is_ok() { crate::game_lifecycle::note_launch(&appid); }
        r
    })
    .await
    .map_err(|e| format!("launch task did not run: {e}"))?;
    out
}

/// How the last launch is going , poll this after `launch_game` returns.
///
/// `launch_game` can only report that Steam accepted the request; whether a
/// game actually starts is decided seconds later, by Proton, in another
/// process tree. Polling this is what lets the Games view say "running" or
/// "it died, here is why" instead of an optimistic sentence written before
/// either was known.
/// Proton installs the wrapper can be pointed at , `[name, path]` pairs for
/// the Upstream Proton picker.
#[tauri::command]
fn list_proton_installs() -> Vec<(String, String)> {
    crate::manager::list_proton_installs()
}

#[tauri::command]
fn get_launch_status() -> crate::game_lifecycle::LaunchStatus {
    crate::game_lifecycle::launch_status()
}

/// Stop the running game , the tray's "Stop game", and the Quit path when
/// `stop_game_on_quit` is set. `appid` defaults to whatever this Suite
/// launched; passing one explicitly targets that prefix.
#[tauri::command]
fn stop_game(appid: Option<String>) -> Result<crate::game_lifecycle::StopReport, String> {
    crate::game_lifecycle::stop_game_impl(appid, 5.0)
}

#[tauri::command]
fn game_session_active() -> bool {
    crate::game_lifecycle::has_live_session()
}

/// Clear a launch that never got past Steam's own DRM/ownership check ,
/// offered as the "Fix Stuck Launch" button on `LaunchStatus::Failed`'s
/// `stuck_reason == "drm_check"` case. Mechanically this IS a stop (same
/// `stop_game_impl` the tray and quit-handler use): there is nothing to
/// save and nothing playing, just a wedged pre-launch child tree (steam.exe
/// + wine infrastructure) that needs clearing so Steam will accept another
/// attempt without a full client restart. `appid` defaults to whatever this
/// Suite last launched, same convention as `stop_game`.
#[tauri::command]
fn fix_stuck_launch(appid: Option<String>) -> Result<crate::game_lifecycle::StopReport, String> {
    std::env::set_var("GB_STOP_REASON", "stuck_launch_fix");
    let report = crate::game_lifecycle::stop_game_impl(appid, 5.0)?;
    crate::game_lifecycle::reset_launch_status();
    Ok(report)
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

/// Upgrade GreenBoost core: pull its repo, run its installer, reload the
/// kernel module. Same command whether core is missing or merely out of
/// date , the installer is idempotent, and the Updates card and the
/// "Install kernel module" button both land here.
#[tauri::command]
async fn upgrade_core_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_install_module_streaming(channel)
}

/// Upgrade the Gaming Suite itself: pull this repo, re-run its install.sh.
#[tauri::command]
async fn upgrade_suite_streaming(channel: Channel<String>) -> Result<i32, String> {
    backend_upgrade_suite_streaming(channel)
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

// ── System tray ───────────────────────────────────────────────────────
//
// Closing the window used to exit the process outright. That is the wrong
// default for a suite whose whole job is to supervise a running game: the
// game kept running with nothing left watching it, and gaming_mode stayed
// pinned. The tray keeps the supervisor alive and, with it, a way to stop
// the game.

use std::sync::atomic::{AtomicBool, Ordering};

/// Whether a tray icon actually exists. If the desktop has no StatusNotifier
/// host (or libayatana-appindicator is missing), hiding the window would
/// strand the user with no way back , so we fall back to quitting.
static TRAY_READY: AtomicBool = AtomicBool::new(false);

fn close_hides_to_tray() -> bool {
    if !TRAY_READY.load(Ordering::Relaxed) {
        return false;
    }
    crate::global_settings::get_impl()
        .map(|s| s.close_action != "quit")
        .unwrap_or(true)
}

/// Stop the game on the way out, when the user asked for Steam-like
/// behaviour. Best-effort: a failure here must never block the exit.
///
/// Gated on `LaunchStatus::Started` , a session record exists (and this
/// function's target, `stop_game_impl`, would happily kill it) from the
/// moment the wrapper starts, which is well before the real game process
/// exists. Steam's own pre-launch helpers (`d3ddriverquery64.exe`,
/// `iscriptevaluator.exe`, ...) go through the exact same wrapper and write
/// the exact same session record while Steam decides whether to start the
/// game at all. Closing the Suite during that window used to kill the
/// helper's process tree, which reads to Steam as the whole launch dying and
/// makes it retry from scratch , exactly the "Launch does nothing" loop this
/// guard exists to stop. `LaunchStatus` already tells the two apart (see
/// `is_steam_internal_helper` in live_stats.rs, which the watcher behind
/// `LaunchStatus::Started` already applies), so use it rather than
/// re-deriving the distinction here.
fn stop_game_if_configured() {
    let wants_stop = crate::global_settings::get_impl()
        .map(|s| s.stop_game_on_quit)
        .unwrap_or(true);
    if !wants_stop {
        return;
    }
    if !matches!(crate::game_lifecycle::launch_status(),
                 crate::game_lifecycle::LaunchStatus::Started { .. }) {
        eprintln!("[greenboost-gaming] no game has actually started yet , \
                    leaving Steam's own launch/prefix setup alone rather than \
                    killing it on Suite quit.");
        return;
    }
    std::env::set_var("GB_STOP_REASON", "suite_quit");
    match crate::game_lifecycle::stop_game_impl(None, 5.0) {
        Ok(r)  => eprintln!("[greenboost-gaming] {}", r.summary()),
        Err(e) => eprintln!("[greenboost-gaming] could not stop the game ({e}) \
                             , it keeps running; nothing else is affected."),
    }
}

fn show_main_window(app: &tauri::AppHandle) {
    use tauri::Manager;
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn build_tray(app: &tauri::AppHandle) {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let build = || -> tauri::Result<()> {
        let show = MenuItem::with_id(app, "show", "Show GreenBoost", true, None::<&str>)?;
        let stop = MenuItem::with_id(app, "stop", "Stop game", true, None::<&str>)?;
        let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
        let menu = Menu::with_items(app, &[&show, &stop, &quit])?;

        let mut builder = TrayIconBuilder::with_id("greenboost-tray")
            .tooltip("GreenBoost Gaming Suite")
            .menu(&menu)
            .show_menu_on_left_click(false)
            .on_menu_event(|app, event| match event.id.as_ref() {
                "show" => show_main_window(app),
                "stop" => {
                    std::env::set_var("GB_STOP_REASON", "tray_stop");
                    match crate::game_lifecycle::stop_game_impl(None, 5.0) {
                        Ok(r)  => eprintln!("[greenboost-gaming] {}", r.summary()),
                        Err(e) => eprintln!("[greenboost-gaming] stop failed: {e}"),
                    }
                }
                "quit" => {
                    stop_game_if_configured();
                    app.exit(0);
                }
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up, ..
                } = event {
                    show_main_window(tray.app_handle());
                }
            });
        if let Some(icon) = app.default_window_icon() {
            builder = builder.icon(icon.clone());
        }
        builder.build(app)?;
        Ok(())
    };

    match build() {
        Ok(()) => TRAY_READY.store(true, Ordering::Relaxed),
        Err(e) => eprintln!(
            "[greenboost-gaming] no system tray available ({e}) , closing the \
             window will quit the Suite as before. Nothing else changes; \
             install libayatana-appindicator3 to get the tray back."),
    }
}

fn main() {
    // Re-apply persisted performance mode before the window opens.
    if let Ok(settings) = crate::global_settings::get_impl() {
        if settings.perf_mode {
            let _ = crate::manager::set_performance_mode(true);
        }
    }

    // A session that died hard leaves a stale record behind , and the same
    // crash leaves greenboost.ko's gaming_mode at 1, which parks inference
    // T2 buffers at the LRU tail until something notices. Clean up on the
    // way in, so the next launch starts from a known state.
    let pruned = crate::game_lifecycle::prune_stale_sessions();
    if pruned > 0 {
        eprintln!("[greenboost-gaming] cleared {pruned} stale game session(s) \
                   left by a previous crash");
    }
    // A crash also leaves the CPU governor pinned, a GPU power limit applied
    // and gaming_mode at 1 , the last of which quietly costs inference
    // throughput until something clears it. Put it back on the way in.
    let (restored, needs_root) = crate::game_lifecycle::restore_stale_power();
    if restored > 0 {
        eprintln!("[greenboost-gaming] restored power settings left by \
                   {restored} crashed session(s)");
    }
    if needs_root > 0 {
        eprintln!("[greenboost-gaming] {needs_root} crashed session(s) left \
                   settings that need root to restore , run: sudo sh \
                   ~/.local/state/greenboost-gaming/power-restore-*.sh");
    }

    tauri::Builder::default()
        .setup(|app| {
            build_tray(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if close_hides_to_tray() {
                    // Keep running in the tray. Steam behaves this way, and
                    // a running game outlives a closed window either way ,
                    // this at least leaves the user a way to stop it.
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    stop_game_if_configured();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_launch_status,
            list_proton_installs,
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
            upgrade_core_streaming,
            upgrade_suite_streaming,
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
            stop_game,
            fix_stuck_launch,
            game_session_active,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
