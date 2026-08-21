// Shared types used across multiple views and components.
export interface GameDll  { name: string; path: string; version: string; tech_type: string; }
export interface GameSetting { key: string; display: string; current: string; recommended: string; needs_change: boolean; }
export interface SettingGroup { title: string; settings: GameSetting[]; }
export interface Game { name: string; path: string; appid?: string; image?: string; dlls: GameDll[]; optimizations: GameSetting[]; is_optimized: boolean; has_backup: boolean; }

export interface NvidiaMismatchInfo {
  loaded:  string;
  on_disk: string;
}

export interface NvidiaUpdateStatus {
  update_available: boolean;
  available_version: string | null;
  installed_version: string | null;
  update_command: string | null;
  source: string;
  checked: boolean;
}

export interface SystemStatus {
  module: string;
  module_version?: string;
  vulkan_layer: string;
  gl_layer?: string;
  nvidia_driver: string;
  cpu_governor: string;
  proton_installed: boolean;
  nvidia_mismatch?: NvidiaMismatchInfo;
  cpu_name?: string;
  total_ram_gb?: number;
  kernel_version?: string;
  session_type?: string;
  greenboost_gaming_mode?: boolean;
  // true = deployed compat-tool copy differs from this checkout, re-run
  // `sudo ./install.sh`; false = matches; null/undefined = can't tell
  // (end-user install, or GreenBoost Proton not installed).
  proton_wrapper_stale?: boolean | null;
}
export interface GpuInfo { name: string; temp: string; power_limit: string; power_usage: string; core_clock_offset: string; mem_clock_offset: string; fan_speed: string; power_limit_min: number; power_limit_max: number; compute_cap: string; }

export interface GpuMetrics {
  temp_c:         number | null;
  power_w:        number | null;
  power_limit_w:  number | null;
  clock_gpu_mhz:  number | null;
  clock_mem_mhz:  number | null;
  gpu_util_pct:   number | null;
  mem_util_pct:   number | null;
  vram_used_mb:   number | null;
  vram_total_mb:  number | null;
  fan_pct:        number | null;
  /** Human-readable NVML clock-throttle reasons. Empty = not throttling. */
  throttle_reasons: string[];
  /** false when NVML couldn't tell us , not the same as "not throttling". */
  throttle_known:   boolean;
}
export interface PoolBrief {
  t1_gb:         number;
  t2_alloc_gb:   number;
  t2_max_gb:     number;
  t2_pct:        number;
  t3_alloc_gb:   number;
  t3_max_gb:     number;
  /** This is swap_pressure (T3's enum), not T2's , greenboost.c:2796. */
  t3_pressure:   string;
  kv_reserve_mb: number;
  kv_t2_mb:      number;
  /** MB-precision figures from the `status` sysfs file; null when unreadable , fall back to the `_gb` fields above. */
  t2_alloc_mb:   number | null;
  t2_avail_mb:   number | null;
  t3_alloc_mb:   number | null;
  t2_fill_pct:   number | null;
}

export interface DisplayMode { resolution: string; rates: number[]; }
export interface DisplayInfo { name: string; connected: boolean; enabled: boolean; primary: boolean; current_mode: string; current_rate: number; modes: DisplayMode[]; gsync_compatible: boolean; vrr: boolean; connector: string; width_mm: number; height_mm: number; }

export interface DlssSourceOption {
  id: string;
  label: string;
  description: string;
  active: boolean;
}
export interface DlssSourceState {
  sources: DlssSourceOption[];
  active: string;
  custom_url: string;
  streamline_origin: string;
}

export interface CachedDll {
  name:       string;
  version:    string;
  sha256:     string;
  source:     string;
  fetched_at: number;
  size_bytes: number;
  // Was missing here while both DllPicker.tsx and Games.tsx redeclared this
  // same shape locally WITH `path` (the version-picker needs it to tell
  // apart multiple cached versions of the same DLL) , this is the one
  // actually imported (About.tsx), so it's the one that needed fixing, not
  // deleting.
  path:       string;
}

// One backup generation available to restore a DLL from, surfaced by
// gb_gaming.dlss_updater's DllFinding.to_dict() (restore_points).
export interface DlssRestorePoint {
  path:    string;
  label:   string;  // version string, or "unknown"
  mtime:   number;  // unix seconds
}

export interface GameNisConfig {
  enabled:   boolean;
  sharpness: number;
  scale:     number;
}

export interface GameWrappers {
  gamemode:  boolean;
  mangohud:  boolean;
  gamescope: string[];
}

export interface DlssFileFinding {
  path:         string;
  family:       string;
  name:         string;
  pretty:       string;
  current:      string;   // "1.2.3.4" or "unknown"
  latest:       string;
  via:          string;
  needs_update: boolean;
  game_root:    string;
  shipped:      string | null;  // version the game shipped with, if known
  upgraded:     boolean;        // current differs from shipped
  can_restore_shipped: boolean; // a .gdlss_original snapshot exists
  restore_points: DlssRestorePoint[]; // .gdlss_bak + timestamped backups, newest first
}
export interface DlssStatus {
  scanned:     number;
  out_of_date: number;
  findings:    DlssFileFinding[];
  scanned_at:  number;   // unix seconds
  source: {
    nvngx:      string;
    nvngx_url:  string;
    streamline: string;
  };
}

export interface GameOverrides {
  fps_cap:           number;
  nis:               GameNisConfig | null;
  hdr:               boolean;
  dlss_preset:       string;
  reflex:            boolean;
  governor:          string;
  env:               Record<string, string>;
  wrappers:          GameWrappers | null;
  gpu_profile:       string;
  gplasync:          boolean | null;
  perf_lock:         boolean | null;
  compositor_suspend: boolean | null;
  vk_pipeline_cache: boolean | null;
}

export interface GlobalSettingsState {
  /** Mirrors perf_mode in global_settings.rs. Was absent here, which let
   *  About write it through an untyped spread with no type checking. */
  perf_mode: boolean;
  dlss_preset: string;
  dlss_indicator: boolean;
  dlss_upgrade: boolean;
  wayland: boolean;
  hdr: boolean;
  auto_disable_secondary_on_launch: boolean;
  /** Start Steam minimised to tray and launch via `steam -applaunch`, so no
   *  Steam window appears over the game. Steam's tray ICON always stays. */
  steam_silent_launch: boolean;
  /** Explicit upstream Proton path for the wrapper; "" = auto-detect. */
  proton_upstream: string;
  /** "tray" (default) hides the Suite on window close; "quit" exits. */
  close_action: string;
  stop_game_on_quit: boolean;
  nis_enable: boolean;
  nis_dispatch: boolean;
  gplasync: boolean;
  perf_lock: boolean;
  compositor_suspend: boolean;
  ddr_prewarm: boolean;
  memlock_unlimited: boolean;
  vk_pipeline_cache: boolean;
  vk_queue_priority: boolean;
  vk_memory_priority: boolean;
  nis_sharpness: number;
  nis_scale: number;
  reflex_enable: boolean;
  fps_cap: number;
  stream_priority: boolean;
  vk_debug: boolean;
  nvapi_hud: boolean;
  mangohud_enabled: boolean;
  vk_overflow_min_mb: number;
  vk_t3_min_mb: number;
  log_ttl_days: number;
  shader_threads: number;
  shader_cache_gb: number;
  gplasync_version: string;
  vkd3d_config: string;
  gl_layer_enabled: boolean;
  gl_overflow_min_mb: number;
  detected_session: string;
  detected_gpu: string;
  detected_series: string;
  recommended_preset: string;
}

export interface DirectStorageInfo {
  detected:       boolean;
  dlls_found:     string[];
  proton_capable: boolean | null;
  proton_build:   string;
  nvme_storage:   boolean | null;
}

export type ViewType = "status" | "games" | "displays" | "profile" | "about" | "live";

export type InstallStreamProps = {
  title:        string;
  command:      string;
  args?:        Record<string, unknown>;
  destructive?: boolean;
  confirm?:     string;
  onDone:       (ok: boolean) => void;
};

/// Mirrors AutoTuneResult in src-tauri/src/manager.rs , the per-card +
/// per-CPU-topology recommendation behind Smart Defaults. It was previously
/// typed inline at the call site as `{label, notes}`, which is why
/// `recommended_shader_threads` was being silently discarded despite
/// CLAUDE.md requiring it be written to GlobalSettings.shader_threads.
export interface AutoTuneResult {
  label: string;
  core_offset_mhz: number;
  mem_offset_mt: number;
  power_w: number;
  lock_clocks_mhz: number | null;
  fan_curve: [number, number][];
  recommended_shader_threads: number;
  numa_nodes: number;
  physical_cores: number;
  logical_cores: number;
  l3_cache_kb: number;
  notes: string[];
}

/** How the last `launch_game` is actually going , polled via
 *  `get_launch_status`. `launch_game` itself can only report that Steam took
 *  the request; whether a game starts is decided seconds later by Proton. */
export type LaunchStatus =
  | { state: "idle" }
  | { state: "pending"; appid: string; elapsed_s: number; phase: string; eta_s: number }
  | { state: "started"; appid: string }
  | { state: "failed";  appid: string; log: string[] };
