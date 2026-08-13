// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

use std::process::Command;
use std::path::{Path, PathBuf};
use serde::{Serialize, Deserialize};
use regex::Regex;

/// "wayland" when XDG_SESSION_TYPE reports a Wayland compositor, "x11"
/// otherwise.  Display tools (xrandr / nvidia-settings) are no-ops under
/// Wayland , call sites use this to branch to compositor-native helpers
/// (kscreen-doctor on KDE, gnome-monitor-config on GNOME).
pub fn session_type() -> &'static str {
    if std::env::var("XDG_SESSION_TYPE")
        .unwrap_or_default().eq_ignore_ascii_case("wayland") { "wayland" }
    else { "x11" }
}

fn have(prog: &str) -> bool {
    Command::new("sh").args(["-c", &format!("command -v {prog}")])
        .status().map(|s| s.success()).unwrap_or(false)
}

/// Resolve a python3 interpreter that actually has PyGObject (`gi`)
/// importable , needed for the GNOME DisplayConfig / VRR D-Bus helpers
/// (gb_gaming._display_config, gb_gaming._vrr_gnome).
///
/// `Command::new("python3")` blindly trusts $PATH. On dev machines that's
/// routinely shadowed by conda/pyenv/uv shims carrying their own isolated
/// python3 with no PyGObject , even when the distro's python3-gi package
/// is correctly installed for the *system* interpreter (dpkg says
/// "installed", but the wrong binary answers to "python3"). Probe
/// explicitly instead of trusting PATH; cache the result for the process
/// lifetime since it can't change without a restart.
fn python3_with_gi() -> Option<&'static str> {
    use std::sync::OnceLock;
    static RESOLVED: OnceLock<Option<String>> = OnceLock::new();
    RESOLVED.get_or_init(|| {
        for cand in ["/usr/bin/python3", "python3"] {
            let ok = Command::new(cand)
                .args(["-c", "import gi"])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);
            if ok { return Some(cand.to_string()); }
        }
        None
    }).as_deref()
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SystemStatus {
    pub module: String,
    pub module_version: Option<String>,  // Some("x.y.z") when loaded
    pub vulkan_layer: String,        // "Installed" | "Missing"
    pub gl_layer: String,            // "Installed" | "Missing"
    pub nvidia_driver: String,
    pub cpu_governor: String,
    pub proton_installed: bool,
    pub nvidia_mismatch: Option<crate::nvidia_diag::NvidiaMismatchInfo>,
    #[serde(default)]
    pub cpu_name: String,
    #[serde(default)]
    pub total_ram_gb: f32,
    #[serde(default)]
    pub kernel_version: String,
    #[serde(default)]
    pub session_type: String,
    #[serde(default)]
    pub greenboost_gaming_mode: bool,  // true when greenboost.ko gaming_mode==1
    // Some(true) = the Steam-deployed wrapper differs from this repo's copy
    // (re-run `sudo ./install.sh`); Some(false) = they match; None = can't
    // tell (end-user install, not a dev checkout , see
    // proton_wrapper_deployed_stale()'s doc comment).
    #[serde(default)]
    pub proton_wrapper_stale: Option<bool>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct GpuInfo {
    pub name: String,
    pub temp: String,
    pub power_limit: String,
    pub power_usage: String,
    pub core_clock_offset: String,
    pub mem_clock_offset: String,
    pub fan_speed: String,
    pub power_limit_min: i32,
    pub power_limit_max: i32,
    pub compute_cap: String,
}

/// PR-TTT: GreenWithEnvy-style extended GPU info.  Pulled in a single
/// nvidia-smi invocation with `nounits` so each cell is a raw number we
/// format ourselves.  Fields kept optional via the "," placeholder so
/// older drivers / unsupported cards degrade gracefully.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct GpuInfoAdvanced {
    pub name:              String,
    pub uuid:              String,
    pub vbios:             String,
    pub driver_version:    String,
    pub cuda_cores:        String,    // approximate, derived from card name
    pub pcie_gen_current:  String,
    pub pcie_gen_max:      String,
    pub pcie_width_current:String,
    pub pcie_width_max:    String,

    // memory
    pub mem_total_mib:     i32,
    pub mem_used_mib:      i32,
    pub mem_bus_width:     String,
    pub gpu_util_pct:      i32,
    pub mem_util_pct:      i32,
    pub encoder_util_pct:  i32,
    pub decoder_util_pct:  i32,

    // clocks (current / max) , MHz
    pub clock_graphics_cur:i32,
    pub clock_graphics_max:i32,
    pub clock_sm_cur:      i32,
    pub clock_sm_max:      i32,
    pub clock_mem_cur:     i32,
    pub clock_mem_max:     i32,
    pub clock_video_cur:   i32,
    pub clock_video_max:   i32,

    // temperatures , °C
    pub temp_gpu:          i32,
    pub temp_slowdown:     i32,
    pub temp_shutdown:     i32,
    pub temp_max_op:       i32,
    pub temp_memory:       i32,

    // power , W
    pub power_draw:        f32,
    pub power_limit:       f32,
    pub power_limit_min:   f32,
    pub power_limit_max:   f32,
    pub power_limit_default:f32,
    pub power_limit_enforced:f32,

    // fan
    pub fan_speed_pct:     String,    // string for "Idle (zero-RPM)"
    pub fan_rpm:           String,    // rare; many cards don't expose this
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct DisplayMode {
    pub resolution: String,
    pub rates: Vec<f32>,
}

fn default_display_enabled() -> bool { true }

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct DisplayInfo {
    pub name: String,
    pub connected: bool,
    /// Whether GNOME currently has this monitor in the active layout
    /// (part of some logical_monitor) vs. physically detected but powered
    /// off. Defaults true for the kscreen-doctor/gdbus/xrandr fallback
    /// parsers below, which only ever enumerate already-active outputs ,
    /// only the GNOME DisplayConfig path (_display_config.py) computes
    /// this for real, since only it can represent "connected but off".
    #[serde(default = "default_display_enabled")]
    pub enabled: bool,
    pub primary: bool,
    pub current_mode: String,
    pub current_rate: f32,
    pub modes: Vec<DisplayMode>,
    pub gsync_compatible: bool,
    pub vrr: bool,
    pub connector: String,
    pub width_mm: u32,
    pub height_mm: u32,
}

fn read_module_version() -> Option<String> {
    // Fast path: sysfs exposes the version when the module is loaded.
    if let Ok(v) = std::fs::read_to_string("/sys/module/greenboost/version") {
        let v = v.trim().to_string();
        if !v.is_empty() { return Some(v); }
    }
    // Fallback: modinfo works even before first load (reads the .ko file).
    let out = Command::new("modinfo")
        .args(["-F", "version", "greenboost"])
        .output().ok()?;
    if out.status.success() {
        let v = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !v.is_empty() { return Some(v); }
    }
    None
}

pub fn get_system_status() -> SystemStatus {
    let module_loaded = Command::new("lsmod")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains("greenboost"))
        .unwrap_or(false);

    let module = if module_loaded { "Loaded".to_string() } else { "Not Loaded".to_string() };
    let module_version = if module_loaded { read_module_version() } else { None };

    // PR-UUU: the Vulkan layer manifest is written by install.sh as
    // `VkLayer_greenboost.json`.  The previous check looked for
    // `greenboost_gaming.json`, so even right after a successful
    // install the row still said "Missing".  We check both names ,
    // the canonical one first, and the legacy one as a fallback for
    // bundles that may have shipped the old filename.
    let layer_paths = [
        "/usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json",
        "/etc/vulkan/implicit_layer.d/VkLayer_greenboost.json",
        "/usr/share/vulkan/implicit_layer.d/greenboost_gaming.json",
    ];
    let vulkan_layer = if layer_paths.iter().any(|p| Path::new(p).exists()) {
        "Installed".to_string()
    } else {
        "Missing".to_string()
    };

    let gl_layer_paths = [
        "/usr/local/lib/libgb_gl.so",
        "/usr/lib/libgb_gl.so",
    ];
    let gl_layer = if gl_layer_paths.iter().any(|p| Path::new(p).exists()) {
        "Installed".to_string()
    } else {
        "Missing".to_string()
    };

    let diag = crate::nvidia_diag::read();
    let nvidia_driver = diag.loaded_kmod_version.clone().unwrap_or_else(|| ",".to_string());
    let nvidia_mismatch = if diag.mismatch {
        Some(crate::nvidia_diag::NvidiaMismatchInfo {
            loaded:  diag.loaded_kmod_version.unwrap_or_default(),
            on_disk: diag.userspace_lib_version.unwrap_or_default(),
        })
    } else {
        None
    };

    let cpu_governor = std::fs::read_to_string("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "Unknown".to_string());

    // PR-UUU: GreenBoost Proton detection , present if any Steam root
    // has a `compatibilitytools.d/greenboost-proton*` directory.
    let home = std::env::var("HOME").unwrap_or_default();
    let proton_installed = [
        format!("{home}/.local/share/Steam/compatibilitytools.d"),
        format!("{home}/.steam/root/compatibilitytools.d"),
        format!("{home}/.steam/steam/compatibilitytools.d"),
        format!("{home}/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d"),
    ].iter().any(|d| {
        std::fs::read_dir(d).ok().map(|rd| {
            rd.flatten().any(|e| {
                e.file_name().to_string_lossy()
                    .starts_with("greenboost-proton")
            })
        }).unwrap_or(false)
    });

    SystemStatus {
        module,
        module_version,
        vulkan_layer,
        gl_layer,
        nvidia_driver,
        cpu_governor,
        proton_installed,
        nvidia_mismatch,
        cpu_name:       read_cpu_name(),
        total_ram_gb:   read_total_ram_gb(),
        kernel_version: read_kernel_version(),
        session_type:   crate::global_settings::detect_session_type(),
        greenboost_gaming_mode: std::fs::read_to_string(
            "/sys/module/greenboost/parameters/gaming_mode")
            .ok()
            .map(|v| v.trim() == "1")
            .unwrap_or(false),
        proton_wrapper_stale: proton_wrapper_deployed_stale(),
    }
}

/// Whether the Steam-deployed `greenboost-proton` wrapper is byte-identical
/// to this repo checkout's copy. `None` when it can't be determined , an
/// end-user install (gaming_project_root() only resolves from
/// CARGO_MANIFEST_DIR, i.e. a dev build compiled from this checkout) or
/// GreenBoost Proton not installed at all.
///
/// Real incident, 2026-08-07: `greenboost_proton/proton` was fixed but a
/// live game launch still ran the old broken behavior, because Steam
/// doesn't run this repo's copy , it runs whatever
/// `greenboost_proton/install.sh` last staged into
/// `compatibilitytools.d/greenboost-proton/`, a SEPARATE step from editing
/// the source. It happened again 2026-08-08: the deployed copy was still
/// 8 hours stale when a real launch hit a false "layer manifest missing"
/// warning that the repo had already fixed. `install.sh` (top-level)
/// already re-runs `greenboost_proton/install.sh` on every `sudo
/// ./install.sh`, so the fix is a single command , the missing piece was
/// ever finding out staleness was the cause without diffing two files by
/// hand. This makes it a visible Status-view flag instead.
fn proton_wrapper_deployed_stale() -> Option<bool> {
    let project = gaming_project_root().ok()?;
    let repo_bytes = std::fs::read(project.join("greenboost_proton").join("proton")).ok()?;

    let home = std::env::var("HOME").unwrap_or_default();
    let roots = [
        format!("{home}/.local/share/Steam/compatibilitytools.d"),
        format!("{home}/.steam/root/compatibilitytools.d"),
        format!("{home}/.steam/steam/compatibilitytools.d"),
        format!("{home}/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d"),
    ];
    let deployed_path = roots.iter()
        .map(|r| std::path::PathBuf::from(format!("{r}/greenboost-proton/proton")))
        .find(|p| p.exists())?;
    let deployed_bytes = std::fs::read(&deployed_path).ok()?;

    Some(repo_bytes != deployed_bytes)
}

fn read_cpu_name() -> String {
    std::fs::read_to_string("/proc/cpuinfo").ok()
        .and_then(|text| {
            text.lines()
                .find(|l| l.starts_with("model name"))
                .and_then(|l| l.splitn(2, ':').nth(1))
                .map(|s| s.trim().to_string())
        })
        .unwrap_or_else(|| "Unknown".to_string())
}

fn read_total_ram_gb() -> f32 {
    std::fs::read_to_string("/proc/meminfo").ok()
        .and_then(|text| {
            text.lines()
                .find(|l| l.starts_with("MemTotal:"))
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|s| s.parse::<u64>().ok())
        })
        .map(|kb| kb as f32 / (1024.0 * 1024.0))
        .unwrap_or(0.0)
}

fn read_kernel_version() -> String {
    Command::new("uname").arg("-r").output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// One-shot nvidia-smi query , pulls every field in a single
/// invocation (fast , one process spawn instead of 6+) and returns
/// the raw CSV cells.  Uses `nounits` so we don't accumulate the
/// "0 %%" / "26.4 W W" double-unit bug.  N/A cells are normalised
/// to None.
fn nvidia_smi_query(fields: &[&str]) -> Vec<Option<String>> {
    let arg = format!("--query-gpu={}", fields.join(","));
    let out = Command::new("nvidia-smi")
        .args([&arg, "--format=csv,noheader,nounits"])
        .output();
    let Ok(out) = out else { return vec![None; fields.len()]; };
    if !out.status.success() {
        return vec![None; fields.len()];
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    // First non-empty line; rest of the cards (if any) are ignored ,
    // the Suite always shows GPU 0.
    let line = stdout.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
    let cells: Vec<Option<String>> = line.split(',')
        .map(|c| {
            let t = c.trim();
            if t.is_empty() || t.eq_ignore_ascii_case("[n/a]")
               || t == "Not Active"
            { None } else { Some(t.to_string()) }
        }).collect();
    // Pad to length so callers can index by position safely.
    let mut v = cells;
    v.resize(fields.len(), None);
    v
}

fn opt_str(v: &[Option<String>], i: usize) -> String {
    v.get(i).cloned().flatten().unwrap_or_else(|| ",".into())
}
fn opt_i32(v: &[Option<String>], i: usize) -> i32 {
    v.get(i).cloned().flatten()
        .and_then(|s| s.parse::<f32>().ok())
        .map(|f| f as i32).unwrap_or(0)
}
fn opt_f32(v: &[Option<String>], i: usize) -> f32 {
    v.get(i).cloned().flatten()
        .and_then(|s| s.parse::<f32>().ok()).unwrap_or(0.0)
}

pub fn get_gpu_info() -> GpuInfo {
    // Try NVML first (fast, no subprocess, Wayland-native).
    let nvml = crate::nvml_reader::read_snapshot();

    // nvidia-smi for fields NVML doesn't expose: name, compute_cap, power limits.
    let cells = nvidia_smi_query(&[
        "name", "power.min_limit", "power.max_limit", "compute_cap",
    ]);
    let name            = opt_str(&cells, 0);
    let power_limit_min = opt_i32(&cells, 1).max(0);
    let power_limit_max = opt_i32(&cells, 2).max(50);
    let compute_cap     = opt_str(&cells, 3);

    // Volatile fields from NVML when available, nvidia-smi otherwise.
    let temp = if let Some(ref n) = nvml {
        n.temp_gpu.map(|t| format!("{t} °C")).unwrap_or_else(|| ", °C".to_string())
    } else {
        let c = nvidia_smi_query(&["temperature.gpu"]);
        format!("{} °C", opt_str(&c, 0))
    };

    let power_usage = if let Some(ref n) = nvml {
        n.power_mw.map(|mw| format!("{:.1} W", mw as f32 / 1000.0))
            .unwrap_or_else(|| ", W".to_string())
    } else {
        let c = nvidia_smi_query(&["power.draw"]);
        format!("{} W", opt_str(&c, 0))
    };

    let power_limit = if let Some(ref n) = nvml {
        n.power_limit_mw.map(|mw| format!("{:.1} W", mw as f32 / 1000.0))
            .unwrap_or_else(|| ", W".to_string())
    } else {
        let c = nvidia_smi_query(&["power.limit"]);
        format!("{} W", opt_str(&c, 0))
    };

    let fan_speed = if let Some(ref n) = nvml {
        match n.fan_speed_avg() {
            Some(s) if s == 0 => "Idle (zero-RPM)".to_string(),
            Some(s)           => format!("{s} %"),
            None              => "Idle (zero-RPM)".to_string(),
        }
    } else {
        let c = nvidia_smi_query(&["fan.speed"]);
        match c.get(0).cloned().flatten() {
            Some(s) => format!("{s} %"),
            None    => "Idle (zero-RPM)".to_string(),
        }
    };

    // Clock offsets , nvidia-settings only (X11/XWayland).
    // On Wayland without XWayland return "," rather than silently querying
    // a tool that will either fail or return stale/wrong data.
    let (core_clock_offset, mem_clock_offset) = {
        let has_display = std::env::var("DISPLAY").map(|v| !v.is_empty()).unwrap_or(false)
            || find_x_display().is_some();
        if has_display {
            let core = Command::new("nvidia-settings")
                .args(["-q", "GPUGraphicsClockOffsetAllPerformanceLevels"])
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout)
                          .split(':').last().unwrap_or("0").trim().to_string())
                .unwrap_or_else(|_| "0".into());
            let mem = Command::new("nvidia-settings")
                .args(["-q", "GPUMemoryTransferRateOffsetAllPerformanceLevels"])
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout)
                          .split(':').last().unwrap_or("0").trim().to_string())
                .unwrap_or_else(|_| "0".into());
            (core, mem)
        } else {
            (",".into(), ",".into())
        }
    };

    GpuInfo {
        name,
        temp,
        power_limit,
        power_usage,
        core_clock_offset,
        mem_clock_offset,
        fan_speed,
        power_limit_min,
        power_limit_max,
        compute_cap,
    }
}

/// Hardware-topology-aware Auto Tune recommendation.
/// Reads GPU SM version/VRAM/TDP from nvidia-smi+NVML and CPU topology from
/// /proc/cpuinfo + /sys to produce optimal per-hardware recommendations.
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct AutoTuneResult {
    pub label:                  String,
    pub core_offset_mhz:        i32,
    pub mem_offset_mt:          i32,   // MT/s (for nvidia-settings offset)
    pub power_w:                i32,
    pub lock_clocks_mhz:        Option<u32>, // NVML locked clock (Wayland path)
    pub fan_curve:              Vec<[f64; 2]>,
    pub recommended_shader_threads: u32, // GREENBOOST_SHADER_THREADS
    pub numa_nodes:             u32,
    pub physical_cores:         u32,
    pub logical_cores:          u32,
    pub l3_cache_kb:            u32,
    pub notes:                  Vec<String>,
}

/// Count physical (non-HT) CPU cores from /proc/cpuinfo.
fn read_cpu_topology() -> (u32, u32, u32, u32) {
    // Returns (physical_cores, logical_cores, numa_nodes, l3_cache_kb)
    use std::collections::HashSet;

    let cpuinfo = std::fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
    let mut core_ids: HashSet<String> = HashSet::new();
    let mut logical: u32 = 0;
    let mut cur_phys = String::new();

    for line in cpuinfo.lines() {
        if line.starts_with("processor\t:") {
            logical += 1;
        } else if let Some(v) = line.strip_prefix("physical id\t:") {
            cur_phys = v.trim().to_string();
        } else if let Some(v) = line.strip_prefix("core id\t\t:") {
            core_ids.insert(format!("{}/{}", cur_phys, v.trim()));
        }
    }
    if logical == 0 { logical = 1; }
    let physical_cores = core_ids.len().max(1) as u32;

    // NUMA nodes , count directories under /sys/devices/system/node/node*
    let numa_nodes = std::fs::read_dir("/sys/devices/system/node")
        .map(|rd| {
            rd.filter_map(|e| e.ok())
              .filter(|e| e.file_name().to_string_lossy().starts_with("node"))
              .count() as u32
        })
        .unwrap_or(1)
        .max(1);

    // L3 cache , read size from first shared_cpu_map cache level with >=3
    let l3_kb = (0u32..16).find_map(|idx| {
        let level = std::fs::read_to_string(
            format!("/sys/devices/system/cpu/cpu0/cache/index{idx}/level"))
            .unwrap_or_default();
        if level.trim() != "3" { return None; }
        let size_str = std::fs::read_to_string(
            format!("/sys/devices/system/cpu/cpu0/cache/index{idx}/size"))
            .unwrap_or_default();
        let s = size_str.trim();
        if let Some(k) = s.strip_suffix('K') { k.trim().parse::<u32>().ok() }
        else if let Some(m) = s.strip_suffix('M') { m.trim().parse::<u32>().ok().map(|v| v * 1024) }
        else { s.parse::<u32>().ok() }
    }).unwrap_or(0);

    (physical_cores, logical.max(physical_cores), numa_nodes, l3_kb)
}

pub fn compute_auto_tune() -> AutoTuneResult {
    let cells = nvidia_smi_query(&[
        "name",
        "gpu_bus_id",
        "compute_cap",
        "memory.total",
        "power.max_limit",
        "power.default_limit",
        "clocks.max.graphics",
        "clocks.max.memory",
    ]);
    let _nvml = crate::nvml_reader::read_snapshot();

    let name        = opt_str(&cells, 0);
    let sm: f32     = opt_str(&cells, 2).parse().unwrap_or(0.0);
    let vram_mb: u64 = {
        let raw = opt_str(&cells, 3);
        raw.split_whitespace().next().unwrap_or("0").parse().unwrap_or(0)
    };
    let tdp_max     = opt_f32(&cells, 4).max(50.0);
    let tdp_default = { let v = opt_f32(&cells, 5); if v > 1.0 { v } else { tdp_max } };
    let max_gpu_mhz = opt_i32(&cells, 6) as u32;

    // Boost clock from NVML is more accurate than clocks.max.graphics from smi.
    let nvml_max_clk = nvml_control_run(&["query"])
        .ok()
        .and_then(|out| {
            out.lines()
                .find(|l| l.starts_with("clock_graphics_max_mhz="))
                .and_then(|l| l.split('=').nth(1))
                .and_then(|v| v.trim().parse::<u32>().ok())
        })
        .unwrap_or(max_gpu_mhz);

    // ── CPU topology ────────────────────────────────────────────────────────
    let (physical_cores, logical_cores, numa_nodes, l3_cache_kb) = read_cpu_topology();
    let smt_active = logical_cores > physical_cores;

    let mut notes: Vec<String> = Vec::new();

    // ── Per-architecture GPU recommendations ────────────────────────────────
    //
    // SM 10.x = Blackwell (RTX 50xx)   , aggressive OC headroom, high TGP
    // SM 8.9  = Ada Lovelace (RTX 40xx) , best perf/W, responsive boost
    // SM 8.6  = Ampere GA106 (RTX 30x0) , stable at +150 MHz / +800 MT/s
    // SM 8.0  = Ampere GA100/A10x        , server, conservative
    // SM 7.5  = Turing (RTX 20xx, GTX 16xx) , tighter boost windows
    // SM 6.x  = Pascal (GTX 10xx)        , modest headroom

    let (core_offset, mem_offset, power_pct, label): (i32, i32, f32, &str) =
    if sm >= 10.0 {
        (150, 1000, 1.00, "Blackwell (RTX 50xx)")
    } else if sm >= 8.9 {
        (150, 1500, 1.00, "Ada Lovelace (RTX 40xx)")
    } else if sm >= 8.6 {
        (100, 800, 1.00, "Ampere (GA106, RTX 30x0)")
    } else if sm >= 8.0 {
        (80, 600, 0.98, "Ampere (GA100/A10x)")
    } else if sm >= 7.5 {
        (75, 500, 0.98, "Turing (RTX 20xx / GTX 16xx)")
    } else if sm >= 6.0 {
        (50, 300, 0.95, "Pascal (GTX 10xx)")
    } else {
        (0, 0, 1.00, "Unknown")
    };

    let power_pct_adj = if vram_mb >= 16000 { power_pct.min(1.00) }
                        else if vram_mb >= 10000 { power_pct.min(1.00) }
                        else { power_pct };

    let power_w = (tdp_default * power_pct_adj).round() as i32;
    let power_w = power_w.min(tdp_max as i32);

    let lock_clocks_mhz = if nvml_max_clk > 0 { Some(nvml_max_clk) } else { None };

    // Fan curve , more aggressive for high-TDP / high-VRAM builds.
    let fan_curve: Vec<[f64; 2]> = if tdp_max >= 300.0 || vram_mb >= 16000 {
        notes.push("Aggressive fan curve for high-TDP GPU.".into());
        vec![[30.0,30.0],[45.0,42.0],[60.0,62.0],[73.0,82.0],[84.0,98.0]]
    } else if tdp_max >= 200.0 {
        vec![[30.0,25.0],[50.0,38.0],[65.0,58.0],[78.0,80.0],[88.0,100.0]]
    } else {
        notes.push("Conservative fan curve for low-TDP GPU.".into());
        vec![[30.0,20.0],[50.0,30.0],[68.0,50.0],[80.0,72.0],[90.0,100.0]]
    };

    // ── CPU-topology-aware shader thread recommendation ──────────────────────
    // Reserve 2 physical cores for the OS/compositor/audio. On SMT systems use
    // physical cores only , shader compilation is memory-bound, HT siblings
    // share L1/L2 and contend badly during parallel SPIR-V transpilation.
    // On NUMA: cap at the core count of a single node to avoid cross-node
    // allocations in shader compiler threads (DXVK uses one arena per thread).
    let cores_per_numa = (physical_cores + numa_nodes - 1) / numa_nodes;
    let usable_cores = physical_cores.saturating_sub(2).max(1);
    // If multi-NUMA, cap at single-node core count to keep threads local.
    let shader_threads = if numa_nodes > 1 {
        usable_cores.min(cores_per_numa)
    } else {
        usable_cores
    };

    if smt_active {
        notes.push(format!(
            "SMT/HT active ({logical_cores} logical / {physical_cores} physical cores) , \
             shader threads pinned to physical cores only to avoid L2 contention."
        ));
        // GREENBOOST_AFFINITY (default "all") governs the game PROCESS's own
        // scheduling affinity , a separate knob from the shader-thread count
        // above. High-thread-count engines (UE4/UE5 titles routinely spawn
        // 100+ threads) benefit from every logical CPU staying schedulable;
        // confirmed live 2026-08-07 on an 8P/16E/32-thread i9-14900KF where
        // the previous hard-coded P-cores-only pin left 16 threads idle
        // under a real UE4 title. "pcores"/"numa" remain available per-game
        // for titles that measurably prefer less scheduler jitter on the
        // render thread instead.
        notes.push(
            "GREENBOOST_AFFINITY defaults to \"all\" (every core schedulable) , \
             switch to \"pcores\" per-game only if frametime A/B testing shows \
             it helps that specific title.".to_string()
        );
    }
    if numa_nodes > 1 {
        notes.push(format!(
            "{numa_nodes} NUMA nodes detected , shader threads capped at {cores_per_numa} \
             (one node) to avoid cross-node allocations."
        ));
    }
    if l3_cache_kb > 0 {
        let l3_mb = l3_cache_kb / 1024;
        if l3_mb < 8 {
            notes.push(format!(
                "Small L3 cache ({l3_mb} MB) , consider reducing shader_cache_gb to 4."
            ));
        }
    }
    if sm == 0.0 {
        notes.push("Could not detect GPU compute capability , applied conservative defaults.".into());
    }
    notes.push(format!(
        "GPU: {name} | SM {sm} | {vram_mb} MiB VRAM | TDP {tdp_default:.0}–{tdp_max:.0} W"
    ));
    notes.push(format!(
        "CPU: {physical_cores}P/{logical_cores}L cores | {numa_nodes} NUMA node(s) | \
         L3 {} | Shader threads → {shader_threads}",
        if l3_cache_kb >= 1024 { format!("{} MB", l3_cache_kb / 1024) }
        else if l3_cache_kb > 0 { format!("{l3_cache_kb} KB") }
        else { "?".into() }
    ));

    AutoTuneResult {
        label: label.to_string(),
        core_offset_mhz: core_offset,
        mem_offset_mt: mem_offset,
        power_w,
        lock_clocks_mhz,
        fan_curve,
        recommended_shader_threads: shader_threads,
        numa_nodes,
        physical_cores,
        logical_cores,
        l3_cache_kb,
        notes,
    }
}

/// GreenWithEnvy-style detailed GPU read.  Volatile fields come from NVML
/// (no subprocess), static fields from a single nvidia-smi call.
pub fn get_gpu_advanced() -> GpuInfoAdvanced {
    // NVML snapshot for volatile data.
    let nvml = crate::nvml_reader::read_snapshot();

    // nvidia-smi for everything NVML doesn't expose.
    let cells = nvidia_smi_query(&[
        "name", "uuid", "vbios_version", "driver_version",
        "pcie.link.gen.current",   "pcie.link.gen.max",
        "pcie.link.width.current", "pcie.link.width.max",
        "memory.total",
        "clocks.max.graphics", "clocks.max.sm",
        "clocks.max.memory",   "clocks.max.video",
        "temperature.gpu.tlimit",   // slowdown
        "power.min_limit", "power.max_limit", "power.default_limit",
        "fan.speed",                // average from driver
    ]);

    let name = opt_str(&cells, 0);
    let cuda_cores = match () {
        _ if name.contains("RTX 50") => "12000+",
        _ if name.contains("RTX 40") => "5000+",
        _ if name.contains("RTX 30") => "3500+",
        _ if name.contains("RTX 20") => "2000+",
        _ => ",",
    }.to_string();

    // ── Volatile fields from NVML, fallback to nvidia-smi ────────────

    let temp_gpu = nvml.as_ref()
        .and_then(|n| n.temp_gpu)
        .unwrap_or_else(|| {
            let c = nvidia_smi_query(&["temperature.gpu"]);
            opt_i32(&c, 0)
        });

    let power_draw = nvml.as_ref()
        .and_then(|n| n.power_mw)
        .map(|mw| mw as f32 / 1000.0)
        .unwrap_or_else(|| {
            let c = nvidia_smi_query(&["power.draw"]);
            opt_f32(&c, 0)
        });

    let power_limit = nvml.as_ref()
        .and_then(|n| n.power_limit_mw)
        .map(|mw| mw as f32 / 1000.0)
        .unwrap_or_else(|| {
            let c = nvidia_smi_query(&["power.limit"]);
            opt_f32(&c, 0)
        });

    let clock_graphics_cur = nvml.as_ref()
        .and_then(|n| n.clock_graphics).unwrap_or(0) as i32;
    let clock_sm_cur       = nvml.as_ref()
        .and_then(|n| n.clock_sm).unwrap_or(0) as i32;
    let clock_mem_cur      = nvml.as_ref()
        .and_then(|n| n.clock_mem).unwrap_or(0) as i32;
    let clock_video_cur    = nvml.as_ref()
        .and_then(|n| n.clock_video).unwrap_or(0) as i32;

    let mem_total_mib = nvml.as_ref()
        .and_then(|n| n.mem_total)
        .map(|b| (b / 1024 / 1024) as i32)
        .unwrap_or_else(|| opt_i32(&cells, 8));
    let mem_used_mib  = nvml.as_ref()
        .and_then(|n| n.mem_used)
        .map(|b| (b / 1024 / 1024) as i32)
        .unwrap_or(0);

    let gpu_util_pct = nvml.as_ref()
        .and_then(|n| n.gpu_util).unwrap_or(0) as i32;
    let mem_util_pct = nvml.as_ref()
        .and_then(|n| n.mem_util).unwrap_or(0) as i32;

    let fan_speed_pct = {
        let nvml_avg = nvml.as_ref().and_then(|n| n.fan_speed_avg());
        match nvml_avg {
            Some(s) if s == 0 => "Idle (zero-RPM)".to_string(),
            Some(s)           => format!("{s} %"),
            None => match cells.get(17).cloned().flatten() {
                Some(s) => format!("{s} %"),
                None    => "Idle (zero-RPM)".to_string(),
            }
        }
    };

    let temp_memory = {
        let c = nvidia_smi_query(&["temperature.memory"]);
        opt_i32(&c, 0)
    };

    GpuInfoAdvanced {
        name,
        uuid:               opt_str(&cells, 1),
        vbios:              opt_str(&cells, 2),
        driver_version:     opt_str(&cells, 3),
        cuda_cores,
        pcie_gen_current:   opt_str(&cells, 4),
        pcie_gen_max:       opt_str(&cells, 5),
        pcie_width_current: opt_str(&cells, 6),
        pcie_width_max:     opt_str(&cells, 7),
        mem_total_mib,
        mem_used_mib,
        mem_bus_width:      ",".to_string(),
        gpu_util_pct,
        mem_util_pct,
        encoder_util_pct:   0,
        decoder_util_pct:   0,
        clock_graphics_cur,
        clock_graphics_max: opt_i32(&cells, 9),
        clock_sm_cur,
        clock_sm_max:       opt_i32(&cells, 10),
        clock_mem_cur,
        clock_mem_max:      opt_i32(&cells, 11),
        clock_video_cur,
        clock_video_max:    opt_i32(&cells, 12),
        temp_gpu,
        temp_slowdown:      opt_i32(&cells, 13),
        temp_shutdown:      0,
        temp_max_op:        0,
        temp_memory,
        power_draw,
        power_limit,
        power_limit_min:    opt_f32(&cells, 14),
        power_limit_max:    opt_f32(&cells, 15),
        power_limit_default: opt_f32(&cells, 16),
        power_limit_enforced: power_limit,
        fan_speed_pct,
        fan_rpm: ",".to_string(),
    }
}

/// Find a usable X display for nvidia-settings , checks DISPLAY env,
/// then probes :0, :1, :2 in order.  Returns the first that has an
/// nvidia-settings connection, or None if none found.
fn find_x_display() -> Option<String> {
    // Candidate list: current DISPLAY first, then common XWayland slots.
    let mut candidates = vec![":0".to_string(), ":1".to_string(), ":2".to_string()];
    if let Ok(d) = std::env::var("DISPLAY") {
        if !d.is_empty() {
            candidates.insert(0, d);
        }
    }
    // XWayland is also often at WAYLAND_DISPLAY's companion X socket.
    // Try a quick probe: `nvidia-settings -q NvidiaDriverVersion` with
    // each display candidate; accept the first that exits successfully.
    for disp in candidates {
        let ok = Command::new("nvidia-settings")
            .env("DISPLAY", &disp)
            .args(["-q", "NvidiaDriverVersion", "--terse"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if ok { return Some(disp); }
    }
    None
}

fn nvidia_settings_set(display: &str, attr: &str, value: &str) -> Result<(), String> {
    let out = Command::new("nvidia-settings")
        .env("DISPLAY", display)
        .args(["-a", &format!("{attr}={value}")])
        .output()
        .map_err(|e| format!("spawn nvidia-settings: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).into_owned())
    }
}

// ── NVML-based fan control (Wayland-native, no X11 required) ─────────────
//
// Fan writes require root (nvmlDeviceSetFanSpeed_v2).  We shell out to
// gb_gaming/nvml_fan.py via pkexec / sudo; the script uses Python3 ctypes
// to call libnvidia-ml.so.1 directly.  pkexec shows a polkit dialog the
// first time and caches credentials for the session ("auth_admin_keep").
//
// Falls back to nvidia-settings (XWayland + Coolbits) when the NVML helper
// is not installed yet (e.g. first boot before `sudo install.sh`).

fn nvml_script(name: &str) -> Option<std::path::PathBuf> {
    let candidates = [
        format!("/usr/local/lib/greenboost-gaming/gb_gaming/{name}"),
        format!("/usr/lib/greenboost-gaming/gb_gaming/{name}"),
    ];
    for p in &candidates {
        let path = std::path::Path::new(p);
        if path.exists() { return Some(path.to_path_buf()); }
    }
    // Dev fallback: source tree (CARGO_MANIFEST_DIR is src/src-tauri → ../../ = repo root).
    let src_rel = concat!(env!("CARGO_MANIFEST_DIR"), "/../../gb_gaming/");
    let src = std::path::Path::new(src_rel).join(name);
    if src.exists() { return Some(src); }
    None
}

fn nvml_fan_script() -> Option<std::path::PathBuf>     { nvml_script("nvml_fan.py") }
fn nvml_control_script() -> Option<std::path::PathBuf> { nvml_script("nvml_control.py") }

/// Run `python3 <nvml_control.py> <args>` as root.
/// Uses sudo -n (NOPASSWD, zero prompts) when install.sh has configured sudoers;
/// falls back to pkexec (one polkit dialog) otherwise.
fn nvml_control_run(args: &[&str]) -> Result<String, String> {
    let script = nvml_control_script()
        .ok_or_else(|| "nvml_control.py not found , run sudo install.sh first".to_string())?;
    let out = run_nvml_script(&script, args)
        .map_err(|e| format!("nvml_control: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    if out.status.success() {
        Ok(stdout)
    } else {
        let combined = format!("{stdout}{stderr}").trim().to_string();
        if out.status.code() == Some(126) || out.status.code() == Some(127)
           || combined.contains("Not authorized")
           || combined.contains("Authorization not obtained")
        {
            Err("NVML_AUTH_CANCELLED".to_string())
        } else {
            Err(format!("NVML control: {combined}"))
        }
    }
}

fn elevate_cmd() -> Option<&'static str> {
    if which::which("pkexec").is_ok() { return Some("pkexec"); }
    if which::which("sudo").is_ok()   { return Some("sudo");   }
    None
}

/// Run `python3 <script> <args>` as root.
/// Tries `sudo -n` first (zero prompts when NOPASSWD is configured by install.sh),
/// falls back to `pkexec` (one polkit dialog) when sudo is denied non-interactively.
fn run_nvml_script(script: &std::path::Path, args: &[&str])
    -> Result<std::process::Output, String>
{
    let script_str = script.to_str()
        .ok_or_else(|| "script path is not valid UTF-8".to_string())?;

    // sudo -n: non-interactive; exits without prompting when NOPASSWD is configured.
    // When a password would be required sudo writes "sudo: a password is required"
    // to stderr and exits 1 , that's our signal to fall back to pkexec.
    if which::which("sudo").is_ok() {
        let mut cmd = Command::new("sudo");
        cmd.arg("-n").arg("python3").arg(script_str);
        for a in args { cmd.arg(a); }
        if let Ok(out) = cmd.output() {
            let stderr = String::from_utf8_lossy(&out.stderr);
            let sudo_denied = stderr.contains("a password is required")
                || stderr.contains("not permitted")
                || stderr.contains("not allowed to execute")
                || stderr.contains("may not run sudo");
            if !sudo_denied {
                return Ok(out);
            }
        }
    }

    // pkexec: shows one polkit dialog, then runs the command.
    if which::which("pkexec").is_ok() {
        let mut cmd = Command::new("pkexec");
        cmd.arg("python3").arg(script_str);
        for a in args { cmd.arg(a); }
        return cmd.output().map_err(|e| format!("pkexec: {e}"));
    }

    // Last resort: interactive sudo (terminal sessions only).
    if which::which("sudo").is_ok() {
        let mut cmd = Command::new("sudo");
        cmd.arg("python3").arg(script_str);
        for a in args { cmd.arg(a); }
        return cmd.output().map_err(|e| format!("sudo: {e}"));
    }

    Err("neither sudo nor pkexec found , cannot run privileged helper".into())
}

/// Run `python3 <nvml_fan.py> <args>` as root.
/// Uses sudo -n (NOPASSWD, zero prompts) when install.sh has configured sudoers;
/// falls back to pkexec (one polkit dialog) otherwise.
fn nvml_fan_run(args: &[&str]) -> Result<String, String> {
    let script = nvml_fan_script()
        .ok_or_else(|| "nvml_fan.py not found , run sudo install.sh first".to_string())?;
    let out = run_nvml_script(&script, args)
        .map_err(|e| format!("nvml_fan: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    if out.status.success() {
        Ok(stdout)
    } else {
        let combined = format!("{stdout}{stderr}").trim().to_string();
        if out.status.code() == Some(126) || out.status.code() == Some(127)
           || combined.contains("Not authorized")
           || combined.contains("Authorization not obtained")
        {
            Err("NVML_AUTH_CANCELLED".to_string())
        } else {
            Err(format!("NVML fan helper: {combined}"))
        }
    }
}

pub fn set_fan_manual(speed: i32) -> Result<String, String> {
    // Primary: NVML via pkexec , Wayland-native, no Coolbits needed.
    if nvml_fan_script().is_some() {
        let speed_s = speed.to_string();
        match nvml_fan_run(&["set", &speed_s]) {
            Ok(_)  => return Ok(format!("Fan set to {speed}% via NVML")),
            Err(e) if e == "NVML_AUTH_CANCELLED"
                   => return Err("Fan test cancelled (authentication dismissed)".to_string()),
            Err(e) => {
                // On Wayland the nvidia-settings fallback can never work (no
                // GPU device targets on XWayland without Coolbits, and Coolbits
                // has no effect in a Wayland session anyway).  Surface the real
                // NVML error so the user knows what to fix.
                if crate::global_settings::detect_session_type() == "wayland" {
                    return Err(format!("NVML fan control failed: {e}"));
                }
                // X11 session: try nvidia-settings fallback below.
            }
        }
    }

    // Fallback: nvidia-settings over XWayland (needs Coolbits=4).
    // On a pure Wayland session, XWayland may not be running , give an
    // actionable error instead of the generic "no display found" message.
    let display = match find_x_display() {
        Some(d) => d,
        None => {
            let session = crate::global_settings::detect_session_type();
            if session == "wayland" {
                return Err(
                    "Fan control on Wayland requires the NVML helper. \
                     Run: sudo install.sh".to_string()
                );
            }
            return Err(
                "Fan control unavailable: NVML helper not installed \
                 and no X display found. Run: sudo install.sh".to_string()
            );
        }
    };
    let _ = nvidia_settings_set(&display, "[gpu:0]/GPUFanControlState", "1");
    match nvidia_settings_set(&display, "[fan:0]/GPUTargetFanSpeed", &speed.to_string()) {
        Ok(_) => Ok(format!("Fan set to {speed}% via DISPLAY={display}")),
        Err(stderr) => {
            if stderr.contains("No targets match") || stderr.contains("no valid targets") {
                Err(format!("NEEDS_COOLBITS:DISPLAY={display}"))
            } else {
                Err(format!("GPUTargetFanSpeed write failed: {stderr}"))
            }
        }
    }
}

pub fn set_fan_auto() -> Result<String, String> {
    // Primary: NVML.
    if nvml_fan_script().is_some() {
        match nvml_fan_run(&["auto"]) {
            Ok(_)  => return Ok("Fan returned to automatic control via NVML".to_string()),
            Err(e) if e == "NVML_AUTH_CANCELLED"
                   => return Ok("Fan auto: authentication dismissed , leaving current state".to_string()),
            Err(e) => {
                if crate::global_settings::detect_session_type() == "wayland" {
                    return Err(format!("NVML fan auto failed: {e}"));
                }
            }
        }
    }
    // Fallback: nvidia-settings (X11 only).
    if let Some(display) = find_x_display() {
        let _ = nvidia_settings_set(&display, "[gpu:0]/GPUFanControlState", "0");
        return Ok(format!("Fan returned to automatic control (DISPLAY={display})"));
    }
    Ok("Fan auto: no control path available".to_string())
}

/// Streaming modal: enable fan control.
///
/// Primary path (Wayland-native, no X11 required):
///   nvml_fan.py is already installed → confirm and return immediately.
///   nvml_fan.py not yet installed AND session is Wayland → guide user to run
///   sudo install.sh; writing Coolbits to xorg.conf is useless without X server.
///
/// Fallback path (X11/XWayland sessions only):
///   nvml_fan.py missing AND X11 session → write Coolbits=4 to xorg.conf so
///   nvidia-settings can control the fan after a re-login.
pub fn enable_fan_control_streaming(send: impl Fn(String)) -> i32 {
    // NVML helper available , no Coolbits or X11 needed at all.
    if nvml_fan_script().is_some() {
        send("NVML-based fan control is available , Coolbits not required.".to_string());
        send("Fan control uses libnvidia-ml.so.1 directly (Wayland-native, no reboot needed).".to_string());
        return 0;
    }

    let session = crate::global_settings::detect_session_type();
    if session == "wayland" {
        // xorg.conf is never read on a pure Wayland session , writing Coolbits
        // would be a no-op and confuse the user with a "reboot required" message
        // that will never fix anything.
        send("Pure Wayland session detected.".to_string());
        send(String::new());
        send("Coolbits / xorg.conf is an X11 mechanism , it has no effect on Wayland.".to_string());
        send("Fan control on Wayland requires the NVML helper (nvml_fan.py).".to_string());
        send(String::new());
        send("Install it by running the GreenBoost installer:".to_string());
        send("  sudo install.sh".to_string());
        send(String::new());
        send("After installation the fan test will work immediately , no reboot needed.".to_string());
        return 1;
    }

    // X11/XWayland session , write Coolbits=4 so nvidia-settings can control fans.
    const CONF_PATH: &str = "/etc/X11/xorg.conf.d/20-nvidia-cooling.conf";
    const CONF_BODY: &str = "Section \"Device\"\n    Identifier \"NVIDIA GPU\"\n    Driver \"nvidia\"\n    Option \"Coolbits\" \"4\"\nEndSection\n";

    send("X11 session detected , configuring Coolbits for nvidia-settings fan control.".to_string());
    send("Checking for existing fan-control configuration…".to_string());

    if std::path::Path::new(CONF_PATH).exists() {
        send(format!("  {CONF_PATH} already exists , no change needed."));
        send("Fan control (Coolbits=4) is already configured.".to_string());
        send("If the fan test still fails, log out and back in to apply the change.".to_string());
        return 0;
    }

    send(format!("Writing {CONF_PATH} (requires admin password)…"));

    let elevate = match elevate_cmd() {
        Some(e) => e,
        None => {
            send("ERROR: neither pkexec nor sudo found , cannot write system file.".to_string());
            return 1;
        }
    };

    let tmp = "/tmp/20-nvidia-cooling.conf";
    if let Err(e) = std::fs::write(tmp, CONF_BODY) {
        send(format!("ERROR: could not write temp file: {e}"));
        return 1;
    }

    let _ = Command::new(elevate).args(["mkdir", "-p", "/etc/X11/xorg.conf.d"]).status();
    let ok = Command::new(elevate).args(["cp", tmp, CONF_PATH])
        .status().map(|s| s.success()).unwrap_or(false);
    let _ = std::fs::remove_file(tmp);

    if ok {
        send(format!("✓  Written {CONF_PATH}"));
        send("Fan control enabled (Coolbits=4).".to_string());
        send(String::new());
        send("Log out and back in to activate. The fan test will work on next login.".to_string());
        0
    } else {
        send(format!("ERROR: failed to write {CONF_PATH}"));
        send("Run this manually in a terminal:".to_string());
        send("  sudo mkdir -p /etc/X11/xorg.conf.d".to_string());
        send(format!("  sudo bash -c 'cat > {CONF_PATH}' << 'EOF'"));
        send(CONF_BODY.to_string());
        send("EOF".to_string());
        1
    }
}

/// Read current GPU temperature (°C), interpolate duty from the given
/// [temp, duty] curve, and apply it via nvidia-settings.
/// Points must be sorted ascending by temperature.
pub fn apply_fan_curve(points: Vec<[f64; 2]>) -> Result<String, String> {
    if points.len() < 2 {
        return Err("Fan curve needs at least 2 points".into());
    }
    // Read current GPU temp via nvidia-smi.
    let cells = nvidia_smi_query(&["temperature.gpu"]);
    let temp_c: f64 = cells.get(0).cloned().flatten()
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);

    // Linear interpolation.
    let duty: f64 = if temp_c <= points[0][0] {
        points[0][1]
    } else if temp_c >= points[points.len() - 1][0] {
        points[points.len() - 1][1]
    } else {
        let mut d = points[points.len() - 1][1];
        for w in points.windows(2) {
            let (t0, p0) = (w[0][0], w[0][1]);
            let (t1, p1) = (w[1][0], w[1][1]);
            if temp_c >= t0 && temp_c <= t1 {
                d = p0 + (p1 - p0) * (temp_c - t0) / (t1 - t0);
                break;
            }
        }
        d
    };
    let duty_i = duty.round() as i32;
    set_fan_manual(duty_i)
        .map_err(|e| format!("Curve: {temp_c}°C → {duty_i}% , fan control failed: {e}"))?;
    Ok(format!("Curve: {temp_c}°C → {duty_i}%"))
}

pub fn get_displays() -> Vec<DisplayInfo> {
    // On KDE Wayland, try kscreen-doctor first for native display info.
    if session_type() == "wayland" && have("kscreen-doctor") {
        if let Ok(out) = Command::new("kscreen-doctor").arg("-o").output() {
            let text = String::from_utf8_lossy(&out.stdout);
            let displays = parse_kscreen_output(&text);
            if !displays.is_empty() { return displays; }
        }
    }
    // GNOME Wayland: query Mutter DisplayConfig via Python helper (python3-gi).
    // This is the primary path on GNOME 47+ , uses D-Bus bindings directly,
    // avoids fragile regex parsing of gdbus GVariant text output.
    if session_type() == "wayland" { if let Some(py) = python3_with_gi() {
        let script = format!(
            "{}from gb_gaming import _display_config as dc\nimport sys\nsys.exit(dc.cmd_get_state())\n",
            crate::py_bootstrap::py_bootstrap()
        );
        let dbus_addr  = std::env::var("DBUS_SESSION_BUS_ADDRESS").unwrap_or_default();
        let wayland    = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".to_string());
        let xdg_rt     = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        if let Ok(out) = Command::new(py)
            .args(["-c", &script])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("WAYLAND_DISPLAY", &wayland)
            .env("XDG_RUNTIME_DIR", &xdg_rt)
            .output()
        {
            let code = out.status.code().unwrap_or(99);
            if code == 0 {
                let text = String::from_utf8_lossy(&out.stdout);
                if let Ok(displays) = serde_json::from_str::<Vec<DisplayInfo>>(text.trim()) {
                    if !displays.is_empty() { return displays; }
                }
            }
            // code == 1 → python3-gi unavailable; fall through to gdbus text path.
        }
    } }
    // GNOME Wayland fallback: gdbus text parsing (works without python3-gi).
    if session_type() == "wayland" && have("gdbus") {
        let dbus_addr = std::env::var("DBUS_SESSION_BUS_ADDRESS").unwrap_or_default();
        let xdg_rt    = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        if let Ok(out) = Command::new("gdbus")
            .args([
                "call", "--session",
                "--dest", "org.gnome.Mutter.DisplayConfig",
                "--object-path", "/org/gnome/Mutter/DisplayConfig",
                "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
            ])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("XDG_RUNTIME_DIR", &xdg_rt)
            .output()
        {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout);
                let displays = parse_mutter_display_config(&text);
                if !displays.is_empty() { return displays; }
            }
        }
    }
    // XWayland / X11 path , works on all desktops where xrandr is available.
    let output = match Command::new("xrandr").arg("--query").output() {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };
    let text = String::from_utf8_lossy(&output.stdout);
    parse_xrandr_output(&text)
}

/// Parse the GVariant text output of org.gnome.Mutter.DisplayConfig.GetCurrentState
/// into a DisplayInfo vec.  The output format is too complex for a full parser;
/// we use targeted regexes to extract the fields we need:
///   - connector name (e.g. "DP-2")
///   - current mode (e.g. "2560x1440@74.968")
///   - whether the connector is primary (from logical_monitors block)
///   - VRR capability (any mode has '+vrr' variant available)
fn parse_mutter_display_config(text: &str) -> Vec<DisplayInfo> {
    use regex::Regex;
    let mut result: Vec<DisplayInfo> = Vec::new();

    // Extract monitor blocks: each starts with a connector name
    // Pattern: (('CONNECTOR', 'vendor', 'product', 'serial'), [...], {...})
    let mon_re = Regex::new(r#"\(\('([^']+)',\s*'[^']*',\s*'[^']*',\s*'[^']*'\),\s*\[([^\]]*(?:\[[^\]]*\][^\]]*)*)\]"#).ok();
    let mode_re = Regex::new(r#"'(\d+)x(\d+)@([\d.]+)(?:\+vrr)?',\s*\d+,\s*\d+,\s*[\d.]+,\s*[\d.]+,\s*\[[^\]]*\],\s*\{([^}]*)\}"#).ok();

    // Collect primary + enabled connectors from logical_monitors section.
    // The logical_monitors section appears after the main monitors list.
    // "Enabled" (same definition as the python3-gi path in
    // _display_config.py): a connector is enabled iff it appears as an
    // output of ANY logical monitor, not just the primary one , a monitor
    // physically detected (in the main monitors list, parsed separately
    // below) but absent from every logical monitor block is powered off.
    let (primary_connectors, enabled_connectors): (
        std::collections::HashSet<String>, std::collections::HashSet<String>,
    ) = {
        let mut primary = std::collections::HashSet::new();
        let mut enabled = std::collections::HashSet::new();
        let prim_block_re = Regex::new(
            r#"\((\d+),\s*(\d+),\s*[\d.]+,\s*\w+\s*\d*,\s*(true|false),\s*\[([^\]]*)\]"#
        ).ok();
        if let Some(re) = prim_block_re {
            for cap in re.captures_iter(text) {
                let is_primary = cap.get(3).map_or(false, |m| m.as_str() == "true");
                let mons_str = cap.get(4).map_or("", |m| m.as_str());
                let conn_re = Regex::new(r#"'([A-Z]+-[\d-]+)'"#).ok();
                if let Some(cre) = conn_re {
                    for ccap in cre.captures_iter(mons_str) {
                        if let Some(m) = ccap.get(1) {
                            enabled.insert(m.as_str().to_string());
                            if is_primary { primary.insert(m.as_str().to_string()); }
                        }
                    }
                }
            }
        }
        (primary, enabled)
    };

    let drm_vrr = get_drm_vrr_capable_outputs();

    if let (Some(mr), Some(mode_r)) = (mon_re, mode_re) {
        for cap in mr.captures_iter(text) {
            let connector = cap.get(1).map_or("", |m| m.as_str()).to_string();
            let modes_text = cap.get(2).map_or("", |m| m.as_str());

            let mut current_mode = String::new();
            let mut current_rate = 0.0f32;
            let mut all_modes: Vec<(String, Vec<f32>)> = Vec::new();
            let mut has_vrr_mode = false;

            // Group modes by resolution
            let mut res_map: std::collections::BTreeMap<String, Vec<f32>> =
                std::collections::BTreeMap::new();

            for mcap in mode_r.captures_iter(modes_text) {
                let w = mcap.get(1).map_or("0", |m| m.as_str());
                let h = mcap.get(2).map_or("0", |m| m.as_str());
                let r_str = mcap.get(3).map_or("0", |m| m.as_str());
                let props = mcap.get(4).map_or("", |m| m.as_str());
                let rate: f32 = r_str.parse().unwrap_or(0.0);
                let res = format!("{w}x{h}");

                // Check for +vrr variant in the original text near this mode
                if modes_text.contains(&format!("'{w}x{h}@{r_str}+vrr'")) {
                    has_vrr_mode = true;
                }

                if props.contains("is-current") && props.contains("true") {
                    current_mode = format!("{w}x{h}");
                    current_rate = rate;
                }

                res_map.entry(res).or_default().push(rate);
            }
            for (res, mut rates) in res_map {
                rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
                rates.dedup_by(|a, b| (*a - *b).abs() < 0.1);
                all_modes.push((res, rates));
            }

            let is_primary = primary_connectors.contains(&connector);
            let vrr_drm   = drm_vrr.iter().any(|n| drm_name_matches(n, &connector));

            result.push(DisplayInfo {
                name:         connector.clone(),
                connected:    true,
                enabled:      enabled_connectors.contains(&connector),
                primary:      is_primary,
                current_mode: current_mode.clone(),
                current_rate: current_rate,
                modes:        all_modes.into_iter()
                                .map(|(r, rates)| crate::manager::DisplayMode {
                                    resolution: r, rates })
                                .collect(),
                gsync_compatible: vrr_drm,
                vrr:          has_vrr_mode,
                connector:    connector,
                width_mm:     0,
                height_mm:    0,
            });
        }
    }
    result
}

/// Parse `kscreen-doctor -o` text into DisplayInfo vec (KDE/Wayland path).
fn parse_kscreen_output(text: &str) -> Vec<DisplayInfo> {
    let mut displays: Vec<DisplayInfo> = Vec::new();
    let mut current: Option<DisplayInfo> = None;
    let mut in_modes = false;

    for line in text.lines() {
        let trimmed = line.trim();

        // "Output: 1 HDMI-A-1 enabled connected"
        if trimmed.starts_with("Output:") {
            if let Some(prev) = current.take() { displays.push(prev); }
            in_modes = false;
            let parts: Vec<&str> = trimmed.split_whitespace().collect();
            if parts.len() < 3 { continue; }
            let name = parts[2].to_string();
            let enabled = parts.iter().any(|&p| p == "enabled");
            let connected = parts.iter().any(|&p| p == "connected");
            let connector = if name.starts_with("HDMI") { "HDMI" }
                else if name.starts_with("DP") { "DisplayPort" }
                else if name.starts_with("eDP") { "eDP (Built-in)" }
                else { name.split('-').next().unwrap_or("") }.to_string();
            current = Some(DisplayInfo {
                name, connected: connected || enabled,
                enabled,
                primary: false, current_mode: String::new(),
                current_rate: 0.0, modes: Vec::new(),
                gsync_compatible: false, vrr: false,
                connector, width_mm: 0, height_mm: 0,
            });
        }

        let Some(ref mut d) = current else { continue };

        // "Geometry: 0,0,3840x2160"
        if trimmed.starts_with("Geometry:") {
            if let Some(geo) = trimmed.split_whitespace().nth(1) {
                if let Some(res) = geo.split(',').nth(2) {
                    d.current_mode = res.to_string();
                }
            }
        }

        // "Priority: 1" → primary display
        if trimmed.starts_with("Priority: 1") { d.primary = true; }

        // "VRR policy: Always" or "VRR policy: Never"
        if trimmed.starts_with("VRR policy:") {
            let policy = trimmed.trim_start_matches("VRR policy:").trim();
            if policy == "Always" || policy == "IfRequested" {
                d.vrr = true;
                d.gsync_compatible = true;
            }
        }

        // "  Modes:" , start of modes section
        if trimmed == "Modes:" { in_modes = true; continue; }
        if !trimmed.starts_with("Mode:") { in_modes = false; }

        // "    Mode: 1 3840x2160@60 *"
        if in_modes && trimmed.starts_with("Mode:") {
            let parts: Vec<&str> = trimmed.split_whitespace().collect();
            if parts.len() < 3 { continue; }
            let active = parts.last() == Some(&"*");
            let mode_str = parts[2]; // "3840x2160@60"
            if let Some((res, rate_str)) = mode_str.split_once('@') {
                let rate: f32 = rate_str.parse().unwrap_or(0.0);
                if let Some(existing) = d.modes.iter_mut().find(|m| m.resolution == res) {
                    if !existing.rates.contains(&rate) { existing.rates.push(rate); }
                } else {
                    d.modes.push(DisplayMode { resolution: res.to_string(), rates: vec![rate] });
                }
                if active {
                    d.current_mode = res.to_string();
                    d.current_rate = rate;
                }
            }
        }
    }
    if let Some(prev) = current { displays.push(prev); }

    // Enrich with DRM VRR capability.
    let drm_vrr = get_drm_vrr_capable_outputs();
    for d in &mut displays {
        if drm_vrr.iter().any(|n| drm_name_matches(n, &d.name)) {
            d.gsync_compatible = true;
        }
    }
    displays.into_iter().filter(|d| d.connected).collect()
}

fn parse_xrandr_output(text: &str) -> Vec<DisplayInfo> {
    let mut displays = Vec::new();
    let mut current: Option<DisplayInfo> = None;
    let mut current_res: Option<String> = None;

    for line in text.lines() {
        // Display header line: "HDMI-0 connected primary 3840x2160+0+0 (normal ...) 600mm x 340mm"
        // or: "DP-1 disconnected ..."
        if !line.starts_with(' ') && !line.starts_with('\t') {
            if let Some(prev) = current.take() {
                displays.push(prev);
            }

            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 2 { continue; }
            let name = parts[0].to_string();
            let connected = parts[1] == "connected";

            // Determine connector type from name prefix
            let connector = if name.starts_with("HDMI") { "HDMI".to_string() }
                else if name.starts_with("DP") || name.starts_with("DisplayPort") { "DisplayPort".to_string() }
                else if name.starts_with("eDP") { "eDP (Built-in)".to_string() }
                else if name.starts_with("VGA") { "VGA".to_string() }
                else { name.split('-').next().unwrap_or("").to_string() };

            let primary = line.contains("primary");

            // Parse current mode from header (e.g. "3840x2160+0+0")
            let current_mode_re = Regex::new(r"(\d+x\d+)\+\d+\+\d+").unwrap();
            let current_mode = current_mode_re.captures(line)
                .map(|c| c[1].to_string())
                .unwrap_or_default();

            // Parse physical size
            let size_re = Regex::new(r"(\d+)mm x (\d+)mm").unwrap();
            let (width_mm, height_mm) = size_re.captures(line)
                .map(|c| (c[1].parse().unwrap_or(0), c[2].parse().unwrap_or(0)))
                .unwrap_or((0, 0));

            current = Some(DisplayInfo {
                name,
                connected,
                // xrandr shows a connected-but-off output with no
                // "<res>+<x>+<y>" geometry block , same signal current_mode
                // already keys off, just also treated as "enabled" here.
                enabled: connected && !current_mode.is_empty(),
                primary,
                current_mode,
                current_rate: 0.0,
                modes: Vec::new(),
                gsync_compatible: false,
                vrr: false,
                connector,
                width_mm,
                height_mm,
            });
            current_res = None;
        } else if let Some(ref mut disp) = current {
            // Mode line: "   3840x2160     60.00*+  59.94  "
            let trimmed = line.trim();
            if trimmed.is_empty() { continue; }

            let mode_res_re = Regex::new(r"^(\d+x\d+)\s+(.+)$").unwrap();
            if let Some(caps) = mode_res_re.captures(trimmed) {
                let res = caps[1].to_string();
                current_res = Some(res.clone());
                let rates_str = caps[2].to_string();
                let mut rates = Vec::new();
                let mut active_rate = 0.0f32;
                for token in rates_str.split_whitespace() {
                    let is_active = token.contains('*');
                    let clean = token.trim_matches(|c| c == '*' || c == '+');
                    if let Ok(r) = clean.parse::<f32>() {
                        rates.push(r);
                        if is_active { active_rate = r; }
                    }
                }
                if active_rate > 0.0 && disp.current_mode == res {
                    disp.current_rate = active_rate;
                }
                // Merge with existing mode entry if resolution already present
                if let Some(existing) = disp.modes.iter_mut().find(|m| m.resolution == res) {
                    for r in rates {
                        if !existing.rates.contains(&r) { existing.rates.push(r); }
                    }
                } else {
                    disp.modes.push(DisplayMode { resolution: res, rates });
                }
            }
        }
        let _ = current_res.as_ref(); // suppress unused warning
    }

    if let Some(prev) = current {
        displays.push(prev);
    }

    // Query nvidia-settings for G-Sync / VRR capability
    let gsync_names = get_gsync_displays();
    for d in &mut displays {
        if gsync_names.iter().any(|n| n.contains(&d.name) || d.name.contains(n.as_str())) {
            d.gsync_compatible = true;
            d.vrr = true;
        }
    }

    // Best-effort kernel DRM probe , works under Wayland where xrandr
    // and nvidia-settings can't reach the running compositor.  The DRM
    // sysfs nodes are `card0-HDMI-A-1/vrr_capable` etc.  We OR-in the
    // capability flag; the `vrr` (currently-enabled) flag stays false
    // unless we positively observed it via the other paths.
    let drm_vrr = get_drm_vrr_capable_outputs();
    for d in &mut displays {
        if drm_vrr.iter().any(|n| drm_name_matches(n, &d.name)) {
            d.gsync_compatible = true;
        }
    }

    displays.into_iter().filter(|d| d.connected).collect()
}

/// Walk `/sys/class/drm/card*-*` for connectors that report
/// `vrr_capable=1`.  Returns the DRM connector names ("HDMI-A-1",
/// "DP-2", …) , these differ slightly from xrandr's names ("HDMI-0",
/// "DP-1"), so [`drm_name_matches`] normalises both sides for comparison.
fn get_drm_vrr_capable_outputs() -> Vec<String> {
    let mut out = Vec::new();
    let entries = match std::fs::read_dir("/sys/class/drm") {
        Ok(e) => e,
        Err(_) => return out,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let fname = match path.file_name().and_then(|s| s.to_str()) {
            Some(s) => s.to_string(),
            None => continue,
        };
        // Filter to per-connector dirs: "card<N>-<connector>" , skip
        // the bare "card0" devices.
        if !fname.starts_with("card") || !fname.contains('-') { continue; }
        let vrr_path = path.join("vrr_capable");
        let v = std::fs::read_to_string(&vrr_path).unwrap_or_default();
        if v.trim() == "1" {
            // Strip the "cardN-" prefix → "HDMI-A-1"
            if let Some(connector) = fname.splitn(2, '-').nth(1) {
                out.push(connector.to_string());
            }
        }
    }
    out
}

/// Loose equality between a DRM connector name ("HDMI-A-1") and an
/// xrandr output name ("HDMI-0" or "HDMI-A-1").  Collapses kernel
/// connector type suffixes ("-A", "-B") and compares the trailing index.
fn drm_name_matches(drm: &str, xrandr: &str) -> bool {
    if drm.eq_ignore_ascii_case(xrandr) { return true; }
    // Normalise: split into (kind, index)
    fn split(n: &str) -> Option<(String, String)> {
        let mut parts: Vec<&str> = n.split('-').collect();
        if parts.len() < 2 { return None; }
        let idx = parts.pop().unwrap().to_string();
        // Drop trailing single-letter connector-type tag if present
        if parts.last().map(|p| p.len() == 1).unwrap_or(false) {
            parts.pop();
        }
        Some((parts.join("-").to_lowercase(), idx))
    }
    match (split(drm), split(xrandr)) {
        (Some((ka, ia)), Some((kb, ib))) => ka == kb && ia == ib,
        _ => false,
    }
}

fn get_gsync_displays() -> Vec<String> {
    let out = Command::new("nvidia-settings")
        .args(["-q", "AllowGSyncCompatible"])
        .output();
    match out {
        Ok(o) => {
            let text = String::from_utf8_lossy(&o.stdout);
            // Look for lines like: Attribute 'AllowGSyncCompatible' (hostname:0[dpy:DP-4]): 1.
            let re = Regex::new(r"\[dpy:([^\]]+)\]").unwrap();
            re.captures_iter(&text).map(|c| c[1].to_string()).collect()
        }
        Err(_) => Vec::new(),
    }
}

/// Build `gnome-monitor-config set` args that preserve all current monitors
/// except the one named `target`, for which the provided override args are
/// substituted.  Returns `None` when the current display state cannot be
/// determined (no active monitors found).
fn gnome_monitor_config_args(
    target: &str,
    target_extra: &[(&str, String)],  // e.g. [("--mode", "2560x1440@74.968")]
    scale_override: Option<f32>,      // when Some, --scale is added for the target
) -> Option<Vec<String>> {
    let displays = get_displays();
    if displays.is_empty() { return None; }
    let mut args = vec!["set".to_string()];
    for d in &displays {
        if d.current_mode.is_empty() || !d.connected { continue; }
        args.push("--logical-monitor".to_string());
        if d.primary { args.push("--primary".to_string()); }
        if d.name == target {
            if let Some(s) = scale_override {
                args.push("--scale".to_string());
                args.push(format!("{s:.4}"));
            }
            for (flag, val) in target_extra {
                args.push(flag.to_string());
                args.push(val.clone());
            }
        }
        args.push("--monitor".to_string());
        args.push(d.name.clone());
        args.push("--mode".to_string());
        // Keep other displays at their current mode; target mode comes from caller.
        if d.name == target {
            // mode already pushed via target_extra above for --mode flag;
            // if target_extra doesn't include --mode we still need it.
            let mode_in_extra = target_extra.iter().any(|(f, _)| *f == "--mode");
            if !mode_in_extra {
                args.push(format!("{}@{:.3}", d.current_mode, d.current_rate));
            }
        } else {
            args.push(format!("{}@{:.3}", d.current_mode, d.current_rate));
        }
    }
    Some(args)
}

fn run_gnome_monitor_config(args: Vec<String>) -> Result<String, String> {
    let out = Command::new("gnome-monitor-config").args(&args).output()
        .map_err(|e| format!("gnome-monitor-config: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

pub fn set_display_mode(name: String, resolution: String, rate: f32) -> Result<String, String> {
    // KDE Wayland native path.
    if session_type() == "wayland" && have("kscreen-doctor") {
        let mode_arg = format!("output.{}.mode.{}@{:.0}", name, resolution, rate);
        let out = Command::new("kscreen-doctor").arg(&mode_arg).output()
            .map_err(|e| format!("kscreen-doctor: {e}"))?;
        return if out.status.success() {
            Ok(format!("Display {name} set to {resolution} @ {rate:.0}Hz via kscreen-doctor"))
        } else {
            let err = String::from_utf8_lossy(&out.stderr);
            Err(format!("kscreen-doctor mode failed: {}", err.trim()))
        };
    }
    // GNOME Wayland native path , use python3-gi helper to call
    // org.gnome.Mutter.DisplayConfig.ApplyMonitorsConfig.
    if session_type() == "wayland" && python3_with_gi().is_some() {
        let py = python3_with_gi().unwrap();
        let script = format!(
            "{}from gb_gaming import _display_config as dc\nimport sys\nsys.exit(dc.cmd_apply_mode(sys.argv[1], sys.argv[2], float(sys.argv[3])))\n",
            crate::py_bootstrap::py_bootstrap()
        );
        let dbus_addr = std::env::var("DBUS_SESSION_BUS_ADDRESS").unwrap_or_default();
        let wayland   = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".to_string());
        let xdg_rt    = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        let out = Command::new(py)
            .args(["-c", &script, &name, &resolution, &format!("{rate:.3}")])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("WAYLAND_DISPLAY", &wayland)
            .env("XDG_RUNTIME_DIR", &xdg_rt)
            .output()
            .map_err(|e| format!("python3 invoke failed: {e}"))?;
        let code = out.status.code().unwrap_or(99);
        if code == 0 {
            let msg = String::from_utf8_lossy(&out.stdout).trim().to_string();
            return Ok(if msg.is_empty() {
                format!("Display {name} set to {resolution} @ {rate:.0}Hz")
            } else {
                msg
            });
        }
        if code != 1 {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(format!("DisplayConfig ApplyMonitorsConfig failed: {}", err.trim()));
        }
        // code == 1 → python3-gi unavailable; fall through to gnome-monitor-config / xrandr.
    }
    // Legacy GNOME path (gnome-monitor-config tool, if installed).
    if session_type() == "wayland" && have("gnome-monitor-config") {
        let mode_str = format!("{}@{:.3}", resolution, rate);
        let args = gnome_monitor_config_args(&name, &[("--mode", mode_str)], None)
            .ok_or_else(|| "gnome-monitor-config: could not read current display state".to_string())?;
        return run_gnome_monitor_config(args)
            .map(|_| format!("Display {name} set to {resolution} @ {rate:.0}Hz via gnome-monitor-config"))
            .map_err(|e| format!("gnome-monitor-config mode failed: {e}"));
    }
    // X11 / XWayland path.
    let out = Command::new("xrandr")
        .args(["--output", &name, "--mode", &resolution, "--rate", &format!("{rate:.2}")])
        .output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(format!("Display {name} set to {resolution} @ {rate:.0}Hz"))
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        Err(format!("xrandr failed for {name} {resolution} @ {rate:.0}Hz: {}", err.trim()))
    }
}

pub fn set_display_rotation(name: String, rotation: String) -> Result<String, String> {
    // KDE Wayland native path.
    if session_type() == "wayland" && have("kscreen-doctor") {
        // kscreen-doctor uses degrees: normal=0, left=90, inverted=180, right=270
        let degrees = match rotation.as_str() {
            "normal"   => "0",
            "left"     => "90",
            "inverted" => "180",
            "right"    => "270",
            other      => other,
        };
        let arg = format!("output.{}.rotation.{}", name, degrees);
        let out = Command::new("kscreen-doctor").arg(&arg).output()
            .map_err(|e| format!("kscreen-doctor: {e}"))?;
        return if out.status.success() {
            Ok(format!("Display {name} rotated to {rotation} via kscreen-doctor"))
        } else {
            let err = String::from_utf8_lossy(&out.stderr);
            Err(format!("kscreen-doctor rotation failed: {}", err.trim()))
        };
    }
    // GNOME / mutter native path.
    if session_type() == "wayland" && have("gnome-monitor-config") {
        let degrees = match rotation.as_str() {
            "normal"   => "0",
            "left"     => "90",
            "inverted" => "180",
            "right"    => "270",
            other      => other,
        };
        let args = gnome_monitor_config_args(
            &name,
            &[("--rotate", degrees.to_string())],
            None,
        ).ok_or_else(|| "gnome-monitor-config: could not read display state".to_string())?;
        return run_gnome_monitor_config(args)
            .map(|_| format!("Display {name} rotated to {rotation} via gnome-monitor-config"))
            .map_err(|e| format!("gnome-monitor-config rotation failed: {e}"));
    }
    // X11 / XWayland path.
    let out = Command::new("xrandr")
        .args(["--output", &name, "--rotate", &rotation])
        .output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(format!("Display {name} rotated to {rotation}"))
    } else {
        Err(format!("xrandr rotate failed for {name}"))
    }
}

// ── PR-KKK: new display controls wired to xrandr ─────────────────────
//
// All three commands shell out to xrandr.  xrandr expects every option
// to land in a single invocation so changes apply atomically.  On
// Wayland these calls have no effect , the unit tests for that path
// will live elsewhere; for now the React UI shows the toggles as
// "platform-limited" when xrandr fails.

/// Set the display's HiDPI scale factor (e.g. 100/125/150/175/200%).
/// xrandr's `--scale` argument is a multiplier; 100% = 1.0.  We invert
/// the factor (a 200% scale means the display is rendered at 0.5×
/// resolution, which xrandr's quirky semantics expect).
pub fn set_display_scale(name: String, percent: u32) -> Result<String, String> {
    // KDE Wayland native path , kscreen-doctor accepts fractional scale.
    if session_type() == "wayland" && have("kscreen-doctor") {
        let scale = percent.clamp(50, 300) as f32 / 100.0;
        let arg = format!("output.{}.scale.{scale:.2}", name);
        let out = Command::new("kscreen-doctor").arg(&arg).output()
            .map_err(|e| format!("kscreen-doctor: {e}"))?;
        return if out.status.success() {
            Ok(format!("Display {name} scaled to {percent}% via kscreen-doctor"))
        } else {
            let err = String::from_utf8_lossy(&out.stderr);
            Err(format!("kscreen-doctor scale failed: {}", err.trim()))
        };
    }
    // GNOME/mutter native path (GNOME 47+, primary): same ApplyMonitorsConfig
    // D-Bus call apply_display_mode/toggle_vrr use, just overriding the
    // logical monitor's scale field. gnome-monitor-config (the fallback
    // below) is a separate CLI tool that isn't installed by default on most
    // GNOME systems, so without this path scale changes silently no-op.
    if session_type() == "wayland" { if let Some(py) = python3_with_gi() {
        let script = format!(
            "{}from gb_gaming import _display_config as dc\nimport sys\nsys.exit(dc.cmd_apply_scale(sys.argv[1], int(sys.argv[2])))\n",
            crate::py_bootstrap::py_bootstrap());
        let dbus_addr = std::env::var("DBUS_SESSION_BUS_ADDRESS").unwrap_or_default();
        let wayland   = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".to_string());
        let xdg_rt    = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        if let Ok(out) = Command::new(py)
            .args(["-c", &script, &name, &percent.to_string()])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("WAYLAND_DISPLAY", &wayland)
            .env("XDG_RUNTIME_DIR", &xdg_rt)
            .output()
        {
            let code = out.status.code().unwrap_or(99);
            if code == 0 {
                return Ok(format!("Display {name} scaled to {percent}% via DisplayConfig"));
            }
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(format!("DisplayConfig scale failed: {}", stderr.trim()));
        }
    } }
    // GNOME / mutter fallback path , pass scale via gnome_monitor_config_args helper.
    if session_type() == "wayland" && have("gnome-monitor-config") {
        let scale = percent.clamp(50, 300) as f32 / 100.0;
        let args = gnome_monitor_config_args(&name, &[], Some(scale))
            .ok_or_else(|| "gnome-monitor-config: could not read display state".to_string())?;
        return run_gnome_monitor_config(args)
            .map(|_| format!("Display {name} scaled to {percent}% via gnome-monitor-config"))
            .map_err(|e| format!("gnome-monitor-config scale failed: {e}"));
    }
    // X11 / XWayland path.
    let p = percent.clamp(50, 300) as f32 / 100.0;
    let factor = 1.0_f32 / p;
    let arg = format!("{factor:.4}x{factor:.4}");
    let out = Command::new("xrandr")
        .args(["--output", &name, "--scale", &arg])
        .output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(format!("Display {name} scaled to {percent}%"))
    } else {
        Err(format!("xrandr --scale failed for {name} ({arg})"))
    }
}

/// A10: Enable or disable HDR output on the named display.
/// KDE Plasma 6+: kscreen-doctor output.<name>.hdr.<on|off>
/// GNOME 47+: gsettings set org.gnome.mutter experimental-features with 'hdr'
pub fn set_hdr_enabled(name: String, enabled: bool) -> Result<String, String> {
    let state = if enabled { "enable" } else { "disable" };

    // KDE path.
    if have("kscreen-doctor") {
        let arg = format!("output.{name}.hdr.{state}");
        let out = Command::new("kscreen-doctor").arg(&arg).output()
            .map_err(|e| format!("kscreen-doctor: {e}"))?;
        if out.status.success() {
            return Ok(format!("HDR {state}d on {name} via kscreen-doctor"));
        }
        // Fall through to GNOME if kscreen-doctor rejected it.
    }

    // GNOME path , toggle the 'hdr' experimental feature.
    if have("gsettings") {
        let cur = Command::new("gsettings")
            .args(["get", "org.gnome.mutter", "experimental-features"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();

        // Parse the GVariant array like "['hdr', 'scale-monitor-framebuffer']"
        let mut features: Vec<String> = cur
            .trim_matches(|c| c == '[' || c == ']')
            .split(',')
            .map(|s| s.trim().trim_matches('\'').to_string())
            .filter(|s| !s.is_empty())
            .collect();

        if enabled {
            if !features.contains(&"hdr".to_string()) {
                features.push("hdr".to_string());
            }
        } else {
            features.retain(|f| f != "hdr");
        }

        let new_val = format!("[{}]",
            features.iter().map(|f| format!("'{f}'")).collect::<Vec<_>>().join(", "));

        let out = Command::new("gsettings")
            .args(["set", "org.gnome.mutter", "experimental-features", &new_val])
            .output().map_err(|e| format!("gsettings: {e}"))?;

        if out.status.success() {
            return Ok(format!("HDR {state}d via GNOME mutter experimental-features"));
        }
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("gsettings failed: {}", err.trim()));
    }

    Err(format!(
        "HDR control requires KDE Plasma 6+ (kscreen-doctor) or GNOME 47+ (gsettings). \
         Neither was found on PATH."))
}

/// Mark `name` as the primary display.  Other monitors keep their
/// current geometry / mode; only the primary flag changes.
/// PR-ZZZ: per-display power on/off.  `enabled=false` runs
/// `xrandr --output NAME --off`; `enabled=true` re-enables with
/// `--auto`.  Returns the new state.
pub fn set_display_enabled(name: String, enabled: bool)
    -> Result<String, String>
{
    if session_type() == "wayland" {
        return set_display_enabled_wayland(&name, enabled);
    }
    let arg = if enabled { "--auto" } else { "--off" };
    // PR-CCCC: capture stderr so failure messages are useful.  xrandr
    // refuses on Wayland, refuses non-RandR drivers, and may refuse
    // disable on a primary display , surfacing its actual stderr is
    // far better than the previous opaque "xrandr --off failed".
    let out = Command::new("xrandr")
        .args(["--output", &name, arg])
        .output()
        .map_err(|e| format!("xrandr invoke failed: {e}"))?;
    if out.status.success() {
        return Ok(format!("Display {} {}",
                   name, if enabled { "enabled" } else { "disabled" }));
    }
    let stderr = String::from_utf8_lossy(&out.stderr);
    let stderr_trim = stderr.trim();
    Err(format!(
        "xrandr {arg} failed for {name}: {}{}",
        if stderr_trim.is_empty() { "no stderr" } else { stderr_trim },
        if name.starts_with("DP-") || name.starts_with("HDMI-") {
            "  (some drivers don't support disabling the only \
             active display on a connector group)"
        } else { "" }
    ))
}

/// Wayland branch: try compositor-native tools.  First match wins.
/// kscreen-doctor handles KDE (Plasma 5.27+); gnome-monitor-config covers
/// GNOME mutter ≥ 45.  When neither tool is on PATH we return a clear
/// hint pointing the user at the compositor's own display panel.
fn set_display_enabled_wayland(name: &str, enabled: bool)
    -> Result<String, String>
{
    // GNOME 47+ primary path: same ApplyMonitorsConfig D-Bus call as
    // mode/scale/VRR. gnome-monitor-config (tried below) is a separate CLI
    // tool not installed by default on most systems , without this, the
    // toggle only ever reached the "no compositor CLI available, use
    // GNOME Settings" dead end on a stock GNOME Wayland box.
    if let Some(py) = python3_with_gi() {
        let script = format!(
            "{}from gb_gaming import _display_config as dc\nimport sys\nsys.exit(dc.cmd_set_enabled(sys.argv[1], sys.argv[2] == '--enable'))\n",
            crate::py_bootstrap::py_bootstrap());
        let flag = if enabled { "--enable" } else { "--disable" };
        let dbus_addr = std::env::var("DBUS_SESSION_BUS_ADDRESS").unwrap_or_default();
        let wayland   = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".to_string());
        let xdg_rt    = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        if let Ok(out) = Command::new(py)
            .args(["-c", &script, name, flag])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("WAYLAND_DISPLAY", &wayland)
            .env("XDG_RUNTIME_DIR", &xdg_rt)
            .output()
        {
            let code = out.status.code().unwrap_or(99);
            if code == 0 {
                return Ok(format!("Display {} {} via DisplayConfig",
                    name, if enabled { "enabled" } else { "disabled" }));
            }
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(format!("DisplayConfig {} failed: {}",
                if enabled { "enable" } else { "disable" }, stderr.trim()));
        }
    }

    // KDE / Plasma , kscreen-doctor uses dotted property syntax,
    // e.g. `kscreen-doctor output.HDMI-A-1.disable`.
    if have("kscreen-doctor") {
        let prop = format!("output.{}.{}",
            name, if enabled { "enable" } else { "disable" });
        let out = Command::new("kscreen-doctor")
            .arg(&prop)
            .output()
            .map_err(|e| format!("kscreen-doctor invoke failed: {e}"))?;
        if out.status.success() {
            return Ok(format!("Display {} {} via kscreen-doctor",
                name, if enabled { "enabled" } else { "disabled" }));
        }
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("kscreen-doctor failed for {name}: {}",
            stderr.trim()));
    }

    // GNOME / mutter , gnome-monitor-config doesn't ship a one-shot
    // disable flag, but `set --primary --monitor NAME --mode auto` will
    // re-enable, and `set --no-monitor NAME` (new in mutter 46) disables.
    if have("gnome-monitor-config") {
        let args: Vec<&str> = if enabled {
            vec!["set", "--logical-monitor", "--primary",
                 "--monitor", name, "--mode", "auto"]
        } else {
            vec!["set", "--no-monitor", name]
        };
        let out = Command::new("gnome-monitor-config")
            .args(&args)
            .output()
            .map_err(|e| format!("gnome-monitor-config invoke failed: {e}"))?;
        if out.status.success() {
            return Ok(format!("Display {} {} via gnome-monitor-config",
                name, if enabled { "enabled" } else { "disabled" }));
        }
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "gnome-monitor-config failed for {name}: {}.  Older mutter \
             versions (< 46) can only toggle displays from GNOME Settings → \
             Displays , please use that panel.", stderr.trim()));
    }

    Err(format!(
        "Wayland session detected but no compositor CLI is available \
         (looked for kscreen-doctor, gnome-monitor-config).  Please {} \
         {name} from your compositor's display panel: GNOME Settings → \
         Displays, or KDE System Settings → Display.",
        if enabled { "enable" } else { "disable" }))
}

/// Restore every connected display in one shot.  Returns a summary so
/// the UI gets a single message instead of N round-trips.
pub fn restore_all_displays_impl() -> Result<String, String> {
    let displays = get_displays();
    if displays.is_empty() {
        return Ok("No displays detected to restore.".into());
    }
    let mut ok_n = 0u32;
    let mut err_n = 0u32;
    let mut first_err: Option<String> = None;
    for d in &displays {
        match set_display_enabled(d.name.clone(), true) {
            Ok(_)  => ok_n += 1,
            Err(e) => {
                err_n += 1;
                if first_err.is_none() { first_err = Some(e); }
            }
        }
    }
    let via = if session_type() == "wayland" {
        if have("kscreen-doctor") { " via kscreen-doctor" }
        else if have("gnome-monitor-config") { " via gnome-monitor-config" }
        else { "" }
    } else { "" };
    if err_n == 0 {
        Ok(format!("Restored {ok_n} display(s){via}."))
    } else if ok_n == 0 {
        Err(first_err.unwrap_or_else(||
            format!("Could not restore any of {err_n} display(s).")))
    } else {
        Ok(format!("Restored {ok_n} display(s){via}; {err_n} failed \
                    (first: {}).",
            first_err.unwrap_or_default()))
    }
}

pub fn set_display_primary(name: String) -> Result<String, String> {
    let status = Command::new("xrandr")
        .args(["--output", &name, "--primary"])
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(format!("Display {} marked primary", name))
    } else {
        Err(format!("xrandr --primary failed for {}", name))
    }
}

/// PR-QQQ: apply absolute (x, y) positions to a set of displays.
/// Pairs each `(name, x, y)` with `xrandr --output <name> --pos <x>x<y>`
/// in a single xrandr invocation so the arrangement is atomic.
///
/// Use this *instead of* set_display_arrangement when the user has
/// dragged monitors into custom positions in the diagram.  Pass `--auto`
/// implicitly for each output so the mode stays its current one.
#[derive(Deserialize, Debug)]
pub struct DisplayPosition {
    pub name: String,
    pub x:    i32,
    pub y:    i32,
}

pub fn set_display_positions(positions: Vec<DisplayPosition>)
    -> Result<String, String>
{
    if positions.is_empty() {
        return Ok("Nothing to apply.".into());
    }
    // xrandr accepts negative coordinates, but the leftmost output
    // typically lives at x=0.  Normalise so the bounding box starts
    // at (0,0) , matches gnome-control-center's convention and avoids
    // surprises on some drivers.
    let min_x = positions.iter().map(|p| p.x).min().unwrap_or(0);
    let min_y = positions.iter().map(|p| p.y).min().unwrap_or(0);
    let mut cmd = Command::new("xrandr");
    for p in &positions {
        cmd.args(["--output", &p.name,
                  "--auto",
                  "--pos", &format!("{}x{}", p.x - min_x, p.y - min_y)]);
    }
    let status = cmd.status().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(format!("Arrangement applied: {} displays positioned",
                   positions.len()))
    } else {
        Err("xrandr --pos failed (check log for details)".into())
    }
}

/// Switch multi-display arrangement between "join" (each monitor shows
/// independent content, side-by-side) and "clone" (all monitors mirror
/// the same image).
///
/// `displays` is the ordered list of display names to arrange.  In
/// clone mode every display past the first is told `--same-as <first>`.
/// In join mode every display past the first is `--right-of <prev>`.
pub fn set_display_arrangement(mode: String, displays: Vec<String>)
    -> Result<String, String>
{
    if displays.len() < 2 {
        return Ok("Only one display detected , nothing to arrange.".into());
    }
    let first = &displays[0];
    let mut cmd = Command::new("xrandr");
    cmd.args(["--output", first, "--auto", "--pos", "0x0"]);
    match mode.as_str() {
        "clone" => {
            for d in displays.iter().skip(1) {
                cmd.args(["--output", d, "--auto", "--same-as", first]);
            }
        }
        "join" => {
            let mut prev = first.clone();
            for d in displays.iter().skip(1) {
                cmd.args(["--output", d, "--auto", "--right-of", &prev]);
                prev = d.clone();
            }
        }
        other => return Err(format!("unknown arrangement mode: {other}")),
    }
    let status = cmd.status().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(format!("Arrangement set to {mode} across {} displays",
                   displays.len()))
    } else {
        Err(format!("xrandr arrangement failed (mode={mode})"))
    }
}

/// PR-SSS: toggle Variable Refresh Rate (G-SYNC / FreeSync) per display.
///
/// We try the NVIDIA-style property first (`AllowVRR`) , that's what
/// the proprietary driver exposes via xrandr.  Falls back to the
/// generic `vrr_capable` flag used by Mesa/AMDGPU.
pub fn set_display_vrr(name: String, enabled: bool) -> Result<String, String> {
    if session_type() == "wayland" {
        return set_display_vrr_wayland(&name, enabled);
    }
    let val = if enabled { "1" } else { "0" };
    // First attempt: NVIDIA AllowVRR (proprietary driver)
    let nv = Command::new("xrandr")
        .args(["--output", &name, "--set", "AllowVRR", val])
        .status();
    if nv.map(|s| s.success()).unwrap_or(false) {
        return Ok(format!("VRR {} for {}", if enabled {"enabled"} else {"disabled"}, name));
    }
    // Second attempt: Mesa-style vrr_capable / VRR_Enabled
    let mesa = Command::new("xrandr")
        .args(["--output", &name, "--set", "vrr_capable", val])
        .status()
        .map_err(|e| e.to_string())?;
    if mesa.success() {
        Ok(format!("VRR {} for {} (Mesa path)",
                   if enabled {"enabled"} else {"disabled"}, name))
    } else {
        Err(format!("Could not toggle VRR on {} , driver may not expose \
                     AllowVRR or vrr_capable; check that the monitor + GPU \
                     both support G-SYNC / FreeSync.", name))
    }
}

/// Wayland branch.
///   • KDE  , kscreen-doctor vrrpolicy (Never / Always)
///   • GNOME , toggle 'variable-refresh-rate' in
///             org.gnome.mutter experimental-features via gsettings.
///             Mutter ≥ 45 supports this; older versions (< 44) need
///             the feature list to already contain the key.
fn set_display_vrr_wayland(name: &str, enabled: bool)
    -> Result<String, String>
{
    // ── KDE path ─────────────────────────────────────────────────────
    if have("kscreen-doctor") {
        let policy = if enabled { "Always" } else { "Never" };
        let prop = format!("output.{name}.vrrpolicy.{policy}");
        let out = Command::new("kscreen-doctor")
            .arg(&prop)
            .output()
            .map_err(|e| format!("kscreen-doctor invoke failed: {e}"))?;
        if out.status.success() {
            return Ok(format!("VRR {} for {name} via kscreen-doctor",
                if enabled { "enabled" } else { "disabled" }));
        }
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("kscreen-doctor vrrpolicy failed for {name}: {}",
            stderr.trim()));
    }

    // ── GNOME/mutter path ────────────────────────────────────────────
    // Primary path (GNOME 47+): the official Display panel toggles VRR
    // by swapping the monitor's mode to one with refresh-rate-mode=variable
    // via org.gnome.Mutter.DisplayConfig.ApplyMonitorsConfig.  Our Python
    // helper at gb_gaming/_vrr_gnome.py mirrors what gnome-control-center
    // does internally (see cc-display-config.c::cc_display_monitor_set_
    // refresh_rate_mode).  Returns 0 on success.  Falls through to the
    // legacy experimental-features gsettings path only if the helper
    // exits with the "no DBus / no PyGObject" code (1).
    if let Some(py) = python3_with_gi() {
        let flag = if enabled { "--enable" } else { "--disable" };
        let script = format!(
            "{bootstrap}from gb_gaming import _vrr_gnome\nsys.exit(_vrr_gnome.set_vrr(sys.argv[1], sys.argv[2] == '--enable'))\n",
            bootstrap = crate::py_bootstrap::py_bootstrap());
        let script = script.as_str();
        // Propagate session-bus and display vars so python3-gi can reach
        // GNOME's D-Bus session (Tauri strips them from the child env).
        let dbus_addr = std::env::var("DBUS_SESSION_BUS_ADDRESS")
            .unwrap_or_default();
        let wayland_disp = std::env::var("WAYLAND_DISPLAY")
            .unwrap_or_else(|_| "wayland-0".to_string());
        let xdg_runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
        let out = Command::new(py)
            .args(["-c", script, name, flag])
            .env("DBUS_SESSION_BUS_ADDRESS", &dbus_addr)
            .env("WAYLAND_DISPLAY", &wayland_disp)
            .env("XDG_RUNTIME_DIR", &xdg_runtime)
            .output()
            .map_err(|e| format!("python3 invoke failed: {e}"))?;
        let code = out.status.code().unwrap_or(99);
        let stderr = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        if code == 0 {
            return Ok(stdout.trim().to_string());
        }
        // Code 1 = python3-gi unavailable → fall through to legacy.
        // Codes 2..6 = monitor / mode / DBus failures we should surface.
        if code != 1 {
            return Err(format!(
                "GNOME DisplayConfig VRR toggle failed for {name}: {}",
                stderr.trim()));
        }
        // else: fall through to legacy gsettings path below.
    }

    // Legacy path (GNOME ≤46): the experimental-features list.  Modern
    // GNOME removed this key when VRR became a first-class per-monitor
    // setting, so we only get here on older shells.
    if have("gsettings") {
        let raw = Command::new("gsettings")
            .args(["get", "org.gnome.mutter", "experimental-features"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();

        // Parse the GVariant string-array into a Vec<String>.
        // Format is: ['feature1', 'feature2']
        let mut features: Vec<String> = raw
            .trim_matches(|c| c == '[' || c == ']')
            .split(',')
            .map(|s| s.trim().trim_matches('\'').to_string())
            .filter(|s| !s.is_empty())
            .collect();

        let key = "variable-refresh-rate";
        if enabled {
            if !features.iter().any(|f| f == key) {
                features.push(key.to_string());
            }
        } else {
            features.retain(|f| f != key);
        }

        // Rebuild GVariant string-array literal.
        let new_val = format!("[{}]",
            features.iter()
                .map(|f| format!("'{f}'"))
                .collect::<Vec<_>>()
                .join(", "));

        let status = Command::new("gsettings")
            .args(["set", "org.gnome.mutter", "experimental-features", &new_val])
            .status()
            .map_err(|e| format!("gsettings set failed: {e}"))?;

        if status.success() {
            return Ok(format!(
                "VRR {} via GNOME mutter experimental-features (legacy path)",
                if enabled { "enabled" } else { "disabled" }));
        }
        return Err(
            "VRR toggle failed: GNOME mutter's experimental-features gsettings \
             key was rejected (GNOME 47+ removed it).  Install python3-gi so \
             DisplayConfig can be driven directly: \
             sudo apt install python3-gi gir1.2-gtk-3.0   (Debian/Ubuntu) or \
             sudo dnf install python3-gobject              (Fedora)."
            .to_string());
    }

    Err("VRR toggle on Wayland: neither kscreen-doctor (KDE) nor \
         gsettings (GNOME) found. Install one of these tools.".into())
}

/// PR-SSS: GNOME night-light controls.  We drive GNOME's own
/// `org.gnome.settings-daemon.plugins.color` schema rather than
/// implementing our own redshift-style daemon.  Works on Ubuntu /
/// Fedora / Arch GNOME sessions.  KDE has its own equivalent
/// (`kwinrc` Night Color section); not handled here.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct NightLightState {
    pub enabled:        bool,
    pub temperature:    i32,    // Kelvin, typically 1700–6500
    pub schedule_auto:  bool,   // sunset → sunrise
    pub manual_from:    f32,    // hours, 0.0–24.0
    pub manual_to:      f32,
    pub available:      bool,   // false if gsettings or the schema is missing
}

fn gsettings_get(key: &str) -> Option<String> {
    let out = Command::new("gsettings")
        .args(["get", "org.gnome.settings-daemon.plugins.color", key])
        .output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn gsettings_set(key: &str, value: &str) -> Result<(), String> {
    let status = Command::new("gsettings")
        .args(["set", "org.gnome.settings-daemon.plugins.color", key, value])
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() { Ok(()) }
    else { Err(format!("gsettings set {key} failed")) }
}

pub fn read_night_light() -> Result<NightLightState, String> {
    if which::which("gsettings").is_err() {
        return Ok(NightLightState {
            enabled: false, temperature: 4000, schedule_auto: true,
            manual_from: 22.0, manual_to: 6.0, available: false,
        });
    }
    // Each key returns a typed literal , "true"/"false"/"uint32 4000"/etc.
    // Strip the leading type marker if present.
    fn parse_bool(s: Option<String>) -> bool {
        s.as_deref().map(str::trim).unwrap_or("") == "true"
    }
    fn parse_num(s: Option<String>, default: f32) -> f32 {
        s.as_deref()
            .map(|raw| raw.trim_start_matches("uint32 ").trim())
            .and_then(|t| t.parse::<f32>().ok())
            .unwrap_or(default)
    }
    Ok(NightLightState {
        enabled:       parse_bool(gsettings_get("night-light-enabled")),
        temperature:   parse_num(gsettings_get("night-light-temperature"), 4000.0) as i32,
        schedule_auto: parse_bool(gsettings_get("night-light-schedule-automatic")),
        manual_from:   parse_num(gsettings_get("night-light-schedule-from"), 22.0),
        manual_to:     parse_num(gsettings_get("night-light-schedule-to"),    6.0),
        available:     true,
    })
}

pub fn apply_night_light(state: NightLightState) -> Result<String, String> {
    if which::which("gsettings").is_err() {
        return Err("gsettings not on PATH , GNOME settings daemon needed".into());
    }
    gsettings_set("night-light-enabled",
                  if state.enabled { "true" } else { "false" })?;
    gsettings_set("night-light-temperature",
                  &format!("uint32 {}", state.temperature.clamp(1700, 6500)))?;
    gsettings_set("night-light-schedule-automatic",
                  if state.schedule_auto { "true" } else { "false" })?;
    gsettings_set("night-light-schedule-from",
                  &format!("{:.2}", state.manual_from.clamp(0.0, 24.0)))?;
    gsettings_set("night-light-schedule-to",
                  &format!("{:.2}", state.manual_to.clamp(0.0, 24.0)))?;
    Ok(format!("Night light: enabled={} temp={}K schedule={}",
        state.enabled, state.temperature,
        if state.schedule_auto { "auto" } else { "manual" }))
}

/// PR-SSS: Vulkan layer install , re-runs the Gaming Suite's own
/// install.sh in privilege-escalated mode (pkexec → sudo fallback).
///
/// The previous implementation pointed at a stale hard-coded developer
/// tree path from when the Suite shared a tree with the main GreenBoost
/// repo.  Now we use the project root resolved via
/// CARGO_MANIFEST_DIR at compile time, walking up from `src-tauri/`.
fn gaming_project_root() -> Result<std::path::PathBuf, String> {
    // src-tauri lives under <project>/src/src-tauri at build time.
    // Two `parent()` calls land on the project root.
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let p = std::path::Path::new(manifest_dir)
        .parent()
        .and_then(|p| p.parent())
        .ok_or("could not resolve project root from CARGO_MANIFEST_DIR")?;
    if !p.join("install.sh").exists() {
        return Err(format!(
            "install.sh not found under {} , Suite layout broken?",
            p.display()
        ));
    }
    Ok(p.to_path_buf())
}

fn run_privileged(args: &[&str]) -> Result<std::process::Output, String> {
    // pkexec gives us a graphical sudo prompt , better UX than terminal
    // sudo (which would just hang invisibly).  If pkexec isn't there
    // (some minimal installs), fall back to terminal sudo via x-terminal.
    if which::which("pkexec").is_ok() {
        Command::new("pkexec").args(args).output()
            .map_err(|e| format!("pkexec invoke failed: {e}"))
    } else {
        Command::new("sudo").args(args).output()
            .map_err(|e| format!("sudo invoke failed: {e}"))
    }
}

// ── PR-UUU: streaming script runner ─────────────────────────────────
// Spawns a child process, line-buffers stdout + stderr through a Tauri
// `Channel<String>` so the React modal renders progress live.  The
// return value is the exit code (or -1 if signalled/missing); the
// success/failure split happens at the call site so the React modal
// can flip between green and red banners based on whichever wrap-up
// message the caller decides to send.
//
// We deliberately don't merge stdout and stderr in the child , keeping
// the two streams separate would matter for log parsing, but for a
// live console view of an install script we want them interleaved.
// Two reader threads push into the same channel; the channel is
// cheap to clone (it's a Tauri-side IPC handle).

use tauri::ipc::Channel;
use std::io::{BufRead, BufReader};
use std::process::Stdio;

fn run_script_streaming(
    argv: &[&str], channel: &Channel<String>
) -> Result<i32, String> {
    if argv.is_empty() {
        return Err("empty argv".into());
    }
    let _ = channel.send(format!("$ {}", argv.join(" ")));
    let mut child = Command::new(argv[0])
        .args(&argv[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    // Take stdout / stderr handles so we can move them into reader
    // threads.  Unwrap is safe , we explicitly set Stdio::piped above.
    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");
    let ch_out = channel.clone();
    let ch_err = channel.clone();

    let t_out = std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = ch_out.send(line);
        }
    });
    let t_err = std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = ch_err.send(line);
        }
    });

    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
    let _ = t_out.join();
    let _ = t_err.join();
    Ok(status.code().unwrap_or(-1))
}

// ── Streaming entry points ──────────────────────────────────────────

/// Streaming Vulkan layer install , runs install.sh under pkexec.
/// Send a "look at the polkit prompt" heads-up before the spawn so
/// users know where the password dialog will appear.
pub fn install_layers_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    let project = gaming_project_root()?;
    let script  = project.join("install.sh");
    let script_str = script.to_str().ok_or("non-UTF8 install.sh path")?;

    let _ = channel.send("Awaiting authorization (pkexec)…".into());
    let argv: Vec<&str> = if which::which("pkexec").is_ok() {
        vec!["pkexec", script_str]
    } else {
        vec!["sudo", script_str]
    };
    run_script_streaming(&argv, &channel)
}

/// Upgrade NVIDIA drivers via the system package manager.
/// Streams output to the frontend modal (same as module install).
/// Requires a reboot after the upgrade to load the new kernel module.
pub fn upgrade_nvidia_streaming(channel: Channel<String>) {
    let pm = if which::which("apt-get").is_ok() { "apt" }
             else if which::which("dnf").is_ok() { "dnf" }
             else if which::which("pacman").is_ok() { "pacman" }
             else { "unknown" };

    let _ = channel.send(format!("── Upgrading NVIDIA drivers via {pm} …"));

    let use_pkexec = which::which("pkexec").is_ok();

    let result = match pm {
        "apt" => {
            let _ = channel.send("Awaiting authorization (pkexec)…".into());
            if use_pkexec {
                run_script_streaming(&["pkexec", "apt-get", "-y", "upgrade"], &channel)
            } else {
                run_script_streaming(&["sudo", "apt-get", "-y", "upgrade"], &channel)
            }
        }
        "dnf" => {
            let _ = channel.send("Awaiting authorization (pkexec)…".into());
            if use_pkexec {
                run_script_streaming(&["pkexec", "dnf", "-y", "upgrade"], &channel)
            } else {
                run_script_streaming(&["sudo", "dnf", "-y", "upgrade"], &channel)
            }
        }
        "pacman" => {
            let _ = channel.send("Awaiting authorization (pkexec)…".into());
            if use_pkexec {
                run_script_streaming(&["pkexec", "pacman", "-Syu", "--noconfirm"], &channel)
            } else {
                run_script_streaming(&["sudo", "pacman", "-Syu", "--noconfirm"], &channel)
            }
        }
        _ => Err("No supported package manager found (apt, dnf, pacman)".into()),
    };

    match result {
        Ok(0) => { let _ = channel.send("── Done. Reboot to activate the new driver.".into()); }
        Ok(n) => { let _ = channel.send(format!("[error] Upgrade exited {n}")); }
        Err(e) => { let _ = channel.send(format!("[error] {e}")); }
    }
}

pub fn uninstall_layers_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    let project = gaming_project_root()?;
    let script  = project.join("install.sh");
    let script_str = script.to_str().ok_or("non-UTF8 install.sh path")?;

    let _ = channel.send("Awaiting authorization (pkexec)…".into());
    let argv: Vec<&str> = if which::which("pkexec").is_ok() {
        vec!["pkexec", script_str, "--uninstall"]
    } else {
        vec!["sudo", script_str, "--uninstall"]
    };
    run_script_streaming(&argv, &channel)
}

pub fn install_proton_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    let project = gaming_project_root()?;
    let script  = project.join("greenboost_proton").join("install.sh");
    let script_str = script.to_str().ok_or("non-UTF8 proton install.sh")?;
    // Proton writes only to ~/.steam/... , no privilege escalation.
    run_script_streaming(&["bash", script_str], &channel)
}

pub fn uninstall_proton_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    let project = gaming_project_root()?;
    let script  = project.join("greenboost_proton").join("install.sh");
    let script_str = script.to_str().ok_or("non-UTF8 proton install.sh")?;
    run_script_streaming(&["bash", script_str, "--uninstall"], &channel)
}

pub fn install_greenboost_layers() -> Result<String, String> {
    let project = gaming_project_root()?;
    let script = project.join("install.sh");
    // Limit the install to its layer-only mode by passing an env var
    // that the script can read.  install.sh already idempotently
    // re-applies the layer manifest each run, so this is safe to spam.
    let out = run_privileged(&[script.to_str().unwrap_or("install.sh")])?;
    if out.status.success() {
        Ok("GreenBoost Vulkan layer installed.".to_string())
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        Err(format!(
            "install.sh failed (exit {}): {}",
            out.status.code().unwrap_or(-1),
            // Show the last 4 lines of the combined stream so the GUI
            // alert isn't dominated by setup chatter.
            (stderr.lines().chain(stdout.lines())
                .collect::<Vec<_>>())
                .iter().rev().take(4).rev()
                .copied().collect::<Vec<_>>().join(" , ")
        ))
    }
}

/// PR-SSS: GreenBoost Proton install , copies the bundled
/// `greenboost_proton/` directory (shipped with this Suite) into
/// `~/.steam/root/compatibilitytools.d/`.  Honours the Flatpak Steam
/// path as well.
pub fn install_greenboost_proton() -> Result<String, String> {
    let project   = gaming_project_root()?;
    let proton_src = project.join("greenboost_proton");
    if !proton_src.is_dir() {
        return Err(format!(
            "GreenBoost Proton bundle not found at {} , \
             Suite distribution is incomplete.",
            proton_src.display()));
    }

    let home = std::env::var("HOME").unwrap_or_default();
    // Try every known Steam root; install into every one that exists.
    let candidates = [
        format!("{home}/.local/share/Steam"),
        format!("{home}/.steam/root"),
        format!("{home}/.steam/steam"),
        format!("{home}/.var/app/com.valvesoftware.Steam/data/Steam"),
    ];
    let mut installed_into: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    for root in &candidates {
        if !std::path::Path::new(root).is_dir() { continue; }
        let compat_dir = format!("{root}/compatibilitytools.d");
        if std::fs::create_dir_all(&compat_dir).is_err() {
            errors.push(format!("mkdir {compat_dir}: failed"));
            continue;
        }
        let dst = format!("{compat_dir}/greenboost-proton");
        let out = Command::new("cp")
            .args(["-r", "-T", proton_src.to_str().unwrap_or(""), &dst])
            .output()
            .map_err(|e| e.to_string())?;
        if out.status.success() {
            installed_into.push(compat_dir);
        } else {
            errors.push(format!(
                "{compat_dir}: {}",
                String::from_utf8_lossy(&out.stderr).trim()));
        }
    }

    if installed_into.is_empty() {
        if errors.is_empty() {
            Err("No Steam installation detected , install Steam first, \
                 launch it once, then try again.".into())
        } else {
            Err(format!("Install failed: {}", errors.join("; ")))
        }
    } else {
        Ok(format!(
            "GreenBoost Proton installed into {} Steam root(s). \
             Restart Steam, then pick it in any game's \
             Properties → Compatibility.",
            installed_into.len()))
    }
}

/// Byte-range of the `{ ... }` block (open brace through its matching
/// close, inclusive) that follows the quoted key `"key"` in `text`. Brace
/// matching is done byte-wise, which is safe even with non-ASCII game
/// names elsewhere in the file: `{`/`}` are single-byte ASCII and never
/// appear as a UTF-8 continuation byte, so every returned offset lands on
/// a valid `str` boundary.
fn find_brace_block(text: &str, key: &str) -> Option<(usize, usize)> {
    let needle = format!("\"{key}\"");
    let key_pos = text.find(&needle)?;
    let after_key = &text[key_pos + needle.len()..];
    let open = key_pos + needle.len() + after_key.find('{')?;
    let bytes = text.as_bytes();
    let mut depth = 0i32;
    let mut i = open;
    while i < bytes.len() {
        match bytes[i] {
            b'{' => depth += 1,
            b'}' => { depth -= 1; if depth == 0 { return Some((open, i + 1)); } }
            _ => {}
        }
        i += 1;
    }
    None
}

/// Leading-tab indent of a sibling numeric-appid key line inside a
/// `CompatToolMapping` block (e.g. `\t\t\t\t\t"2680010"`), so a freshly
/// inserted entry matches Steam's own formatting instead of guessing.
fn detect_sibling_indent(block: &str) -> Option<String> {
    let re = Regex::new(r#"(?m)^(\t+)"\d+"\s*$"#).ok()?;
    re.captures(block).map(|c| c[1].to_string())
}

/// Writes (or corrects) the `CompatToolMapping["<appid>"].name` entry in
/// Steam's `config.vdf` to `greenboost-proton`. Returns `Ok(false)` when it
/// already was , no write, no backup. This is deliberately narrow: it edits
/// only the one child block, not a general VDF rewrite, because config.vdf
/// is Steam's own live state and a hand-rolled full-file parse is exactly
/// the kind of thing that quietly corrupts a file nobody notices until
/// Steam won't start.
fn set_compat_tool_mapping(config_path: &Path, content: &str, appid: &str) -> Result<bool, String> {
    let (ctm_start, ctm_end) = find_brace_block(content, "CompatToolMapping")
        .ok_or_else(|| "CompatToolMapping section not found in config.vdf".to_string())?;
    let ctm_block = &content[ctm_start..ctm_end];

    let new_content = if let Some((sub_start, sub_end)) = find_brace_block(ctm_block, appid) {
        let sub_block = &ctm_block[sub_start..sub_end];
        let name_re = Regex::new(r#""name"\s*"([^"]*)""#).map_err(|e| e.to_string())?;
        match name_re.captures(sub_block) {
            Some(cap) if &cap[1] == "greenboost-proton" => return Ok(false),
            Some(_) => {
                let new_sub = name_re
                    .replace(sub_block, "\"name\"\t\t\"greenboost-proton\"")
                    .into_owned();
                format!("{}{}{}{}",
                    &content[..ctm_start + sub_start], new_sub,
                    &ctm_block[sub_end..], &content[ctm_end..])
            }
            None => return Err(format!(
                "appid {appid} entry in config.vdf has no \"name\" key , \
                 unexpected shape, refusing to edit it")),
        }
    } else {
        let indent = detect_sibling_indent(ctm_block).unwrap_or_else(|| "\t\t\t\t\t".to_string());
        let entry = format!(
            "{indent}\"{appid}\"\n{indent}{{\n\
             {indent}\t\"name\"\t\t\"greenboost-proton\"\n\
             {indent}\t\"config\"\t\t\"\"\n\
             {indent}\t\"priority\"\t\t\"250\"\n\
             {indent}}}\n{indent}");
        let insert_at = ctm_block.rfind('}')
            .ok_or_else(|| "malformed CompatToolMapping block in config.vdf".to_string())?;
        let new_ctm_block = format!("{}{}{}",
            &ctm_block[..insert_at], entry, &ctm_block[insert_at..]);
        format!("{}{}{}", &content[..ctm_start], new_ctm_block, &content[ctm_end..])
    };

    let ts = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs()).unwrap_or(0);
    let backup_path = PathBuf::from(format!("{}.gb.{ts}", config_path.display()));
    std::fs::copy(config_path, &backup_path).map_err(|e| format!("backup config.vdf: {e}"))?;

    let tmp_path = PathBuf::from(format!("{}.gb_tmp", config_path.display()));
    std::fs::write(&tmp_path, &new_content).map_err(|e| format!("write tmp: {e}"))?;
    std::fs::rename(&tmp_path, config_path).map_err(|e| format!("rename into place: {e}"))?;
    Ok(true)
}

/// Ensure Steam's `CompatToolMapping` maps `appid` to `greenboost-proton`
/// before every launch. `launch_game_ext` used to just open
/// `steam://rungameid/<id>` and its own success message admitted the gap:
/// "Steam will use your per-game Proton selection" , nothing actually
/// guaranteed GreenBoost Proton ran. Best-effort: if GreenBoost Proton
/// isn't installed at all, or config.vdf can't be found/parsed, this
/// returns a warning string rather than blocking the launch , the game
/// still launches with whatever Steam already has configured, same as
/// before this change.
pub fn ensure_greenboost_proton_mapping(appid: &str) -> Option<String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let roots = [
        format!("{home}/.local/share/Steam"),
        format!("{home}/.steam/root"),
        format!("{home}/.steam/steam"),
        format!("{home}/.var/app/com.valvesoftware.Steam/data/Steam"),
    ];

    let has_greenboost_proton = roots.iter().any(|r| {
        std::fs::read_dir(format!("{r}/compatibilitytools.d")).ok()
            .map(|rd| rd.flatten().any(|e|
                e.file_name().to_string_lossy().starts_with("greenboost-proton")))
            .unwrap_or(false)
    });
    if !has_greenboost_proton {
        return Some("warn: GreenBoost Proton isn't installed , launching with \
                      Steam's existing Proton selection instead. Install it \
                      from the Status view to force it on every launch."
                      .to_string());
    }

    let config_path = match roots.iter()
        .map(|r| PathBuf::from(format!("{r}/config/config.vdf")))
        .find(|p| p.exists())
    {
        Some(p) => p,
        None => return Some("warn: Steam's config.vdf not found , \
                              couldn't verify the Proton mapping.".to_string()),
    };

    let content = match std::fs::read_to_string(&config_path) {
        Ok(c) => c,
        Err(e) => return Some(format!("warn: couldn't read config.vdf ({e}) , \
                                        Proton mapping not verified.")),
    };

    match set_compat_tool_mapping(&config_path, &content, appid) {
        Ok(false) => None, // already correct , nothing to report
        Ok(true) => {
            let steam_running = Command::new("pgrep").args(["-x", "steam"]).output()
                .map(|o| o.status.success()).unwrap_or(false);
            Some(if steam_running {
                "GreenBoost Proton mapping written , Steam is currently running \
                 and holds config.vdf in memory, so restart Steam for this to \
                 take effect on THIS launch (it will take on the next one \
                 regardless).".to_string()
            } else {
                "GreenBoost Proton mapping set for this game.".to_string()
            })
        }
        Err(e) => Some(format!("warn: couldn't set Proton mapping ({e})")),
    }
}

/// PR-XXX: same as launch_game, but if `disable_secondary_displays=true`
/// runs `xrandr --output <n> --off` for every connected non-primary
/// display before invoking Steam's URL handler.  No automatic restore ,
/// the user re-enables via the Displays panel when they're done
/// gaming, or via the `Restore displays` button we add to that panel.
pub fn launch_game_ext(appid: &str, disable_secondary_displays: bool)
    -> Result<String, String>
{
    // Strict integer parse , refuses anything that could be construed
    // as a path or shell metacharacter.
    let id: u64 = appid.parse()
        .map_err(|_| format!("invalid appid: {appid:?} (must be a positive integer)"))?;
    let url = format!("steam://rungameid/{id}");

    let mut prep_notes: Vec<String> = Vec::new();
    if let Some(note) = ensure_greenboost_proton_mapping(&id.to_string()) {
        prep_notes.push(note);
    }
    if disable_secondary_displays {
        // Enumerate connected non-primary displays via xrandr and switch
        // them off.  Best-effort: a failure here doesn't block the
        // launch , gaming still works on the primary alone.
        for d in get_displays().into_iter()
                                 .filter(|d| d.connected && !d.primary)
        {
            match set_display_enabled(d.name.clone(), false) {
                Ok(msg)  => prep_notes.push(msg),
                Err(err) => prep_notes.push(format!("warn: {err}")),
            }
        }
    }

    // Prefer xdg-open (most-portable) → fall back to steam → fall back
    // to gtk-launch.  Run detached so the Suite stays responsive while
    // the game launches.
    for argv in [
        vec!["xdg-open", &url],
        vec!["steam",    &url],
    ] {
        if let Ok(status) = Command::new(argv[0]).arg(argv[1]).status() {
            if status.success() {
                spawn_gaming_mode_watcher();
                let mut msg = format!(
                    "Launching appid {id} via {argv0} , GreenBoost Proton mapping \
                     verified.",
                    argv0 = argv[0]);
                if !prep_notes.is_empty() {
                    msg.push_str("\nPre-launch: ");
                    msg.push_str(&prep_notes.join("; "));
                }
                return Ok(msg);
            }
        }
    }
    Err("Neither xdg-open nor steam is on PATH , install one to launch games.".to_string())
}

/// Keep greenboost.ko's `gaming_mode` correct for the lifetime of a launch,
/// independent of whatever the user does in the UI afterward.
///
/// `live_stats::find_game_pid_impl()` already writes `gaming_mode`
/// host-side on every call where the state actually changed , the one
/// place that reaches it is a real fix, since the Proton wrapper itself
/// runs inside pressure-vessel's read-only /sys and can never write it
/// (see that function's doc comment). But the ONLY caller was Live.tsx's
/// 2 s poll, so gaming_mode was only ever set while the user happened to
/// have the Live tab open , launching from Games and switching away (the
/// common case) left it at 0 for the whole session, exactly as observed
/// live 2026-08-08 on "The First Berserker: Khazan": the process ran fine,
/// but nothing ever called find_game_pid_impl() to notice. This spawns a
/// short-lived poll of its own, scoped to one launch, so the signal no
/// longer depends on which tab is on screen.
fn spawn_gaming_mode_watcher() {
    std::thread::spawn(|| {
        // Steam/Proton/wine startup is slow , give the process time to
        // actually appear before giving up on ever seeing it.
        let mut seen = false;
        for _ in 0..30 { // ~60s
            if crate::live_stats::find_game_pid_impl().is_some() { seen = true; break; }
            std::thread::sleep(std::time::Duration::from_secs(2));
        }
        if !seen { return; } // launch failed or isn't a wine/Proton game , nothing to track
        loop {
            std::thread::sleep(std::time::Duration::from_secs(3));
            if crate::live_stats::find_game_pid_impl().is_none() { break; }
        }
    });
}

/// Build the Steam launch options string that injects Linux NVIDIA driver-level
/// env vars equivalent to what nvidiaProfileInspector applies on Windows via NVAPI DRS.
///
/// Preset names: "gaming_performance" | "gaming_competitive" | "gaming_quality" | "power_saving"
/// Returns a string like  `__GL_THREADED_OPTIMIZATIONS=1 ... %command%`
/// that the user can paste into Steam > Properties > Launch Options.
pub fn get_steam_launch_options(preset: &str) -> Result<String, String> {
    let gb_py = find_gb_python_script("nvapi_linux.py")?;
    let out = Command::new("python3")
        .args([gb_py.to_str().unwrap_or("nvapi_linux.py"), "prefix", preset])
        .output()
        .map_err(|e| format!("python3 nvapi_linux: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).into_owned());
    }
    let prefix = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok(format!("{prefix} %command%"))
}

/// Apply system-level NVIDIA performance settings (nvidia-smi pm=1, power limit)
/// for the given preset.  Requires root , uses pkexec / sudo.
pub fn apply_nvidia_system_settings(preset: &str) -> Result<String, String> {
    let gb_py = find_gb_python_script("nvapi_linux.py")?;
    let out = Command::new("python3")
        .args([gb_py.to_str().unwrap_or("nvapi_linux.py"), "apply", preset])
        .output()
        .map_err(|e| format!("python3 nvapi_linux apply: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if out.status.success() || !stdout.is_empty() {
        Ok(if stderr.is_empty() { stdout } else { format!("{stdout}\n{stderr}") })
    } else {
        Err(stderr)
    }
}

fn find_gb_python_script(name: &str) -> Result<std::path::PathBuf, String> {
    let candidates = [
        "/usr/local/lib/greenboost-gaming/gb_gaming",
        "/usr/lib/greenboost-gaming/gb_gaming",
    ];
    for dir in &candidates {
        let p = std::path::Path::new(dir).join(name);
        if p.exists() { return Ok(p); }
    }
    // dev fallback
    let exe = std::env::current_exe().unwrap_or_default();
    for ancestor in exe.ancestors().take(8) {
        let p = ancestor.join("gb_gaming").join(name);
        if p.exists() { return Ok(p); }
    }
    Err(format!("{name} not found , is greenboost-gaming installed?"))
}

pub fn apply_gpu_profile(core: i32, mem: i32, power: i32) -> Result<String, String> {
    let mut applied: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    let session = crate::global_settings::detect_session_type();
    let is_wayland = session == "wayland";

    // ── Clock offsets ────────────────────────────────────────────────────────
    if core != 0 || mem != 0 {
        if !is_wayland {
            // X11/XWayland: use nvidia-settings clock offsets (original path).
            if Command::new("nvidia-settings")
                .args(["-a", &format!("[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={core}")])
                .status().map(|s| !s.success()).unwrap_or(true)
            {
                warnings.push("core clock offset (nvidia-settings failed)".into());
            } else {
                applied.push(format!("core offset={core} MHz"));
            }
            if Command::new("nvidia-settings")
                .args(["-a", &format!("[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={mem}")])
                .status().map(|s| !s.success()).unwrap_or(true)
            {
                warnings.push("memory clock offset (nvidia-settings failed)".into());
            } else {
                applied.push(format!("mem offset={mem} MT/s"));
            }
        } else {
            // Wayland: translate core offset → NVML locked clock.
            // Query the GPU max boost clock, then lock to (max + offset).
            if let Some(_script) = nvml_control_script() {
                if core != 0 {
                    // Query max clock via nvml_control query, parse clock_graphics_max_mhz.
                    let max_mhz = nvml_control_run(&["query"])
                        .ok()
                        .and_then(|out| {
                            out.lines()
                                .find(|l| l.starts_with("clock_graphics_max_mhz="))
                                .and_then(|l| l.split('=').nth(1))
                                .and_then(|v| v.trim().parse::<i32>().ok())
                        });
                    if let Some(max) = max_mhz {
                        let locked = (max + core).max(100) as u32;
                        let min_locked = locked.saturating_sub(50);
                        match nvml_control_run(&["lock-clocks",
                            &min_locked.to_string(), &locked.to_string()]) {
                            Ok(_) => applied.push(format!("GPU clocks locked {min_locked}–{locked} MHz")),
                            Err(e) if e == "NVML_AUTH_CANCELLED" => {
                                return Err("Profile apply cancelled (authentication dismissed)".into());
                            }
                            Err(e) => warnings.push(format!("clock lock: {e}")),
                        }
                    } else {
                        warnings.push("clock lock: could not query GPU max clock".into());
                    }
                }
                // Memory clock offsets on Wayland: skip silently , GDDR memory clocks
                // don't use the same offset semantics and are fixed on most Ampere/Ada GPUs.
                if mem != 0 {
                    warnings.push("memory clock offset skipped on Wayland (use mem clock lock separately)".into());
                }
            } else {
                warnings.push("clock control skipped: NVML helper not installed (run sudo install.sh)".into());
            }
        }
    }

    // ── Power limit (NVML primary, nvidia-smi fallback , both Wayland-native) ─
    if power > 0 {
        let power_ok = if nvml_control_script().is_some() {
            match nvml_control_run(&["set-power", &power.to_string()]) {
                Ok(_) => true,
                Err(e) if e == "NVML_AUTH_CANCELLED" => {
                    return Err("Profile apply cancelled (authentication dismissed)".into());
                }
                Err(_) => false,
            }
        } else {
            false
        };

        if !power_ok {
            // Fallback: nvidia-smi -pl (also Wayland-native, requires root).
            match run_privileged(&["nvidia-smi", "-pl", &power.to_string()]) {
                Ok(out) if out.status.success() => { applied.push(format!("power={power} W")); }
                Ok(out) => warnings.push(format!(
                    "power limit (nvidia-smi exit {}): {}",
                    out.status.code().unwrap_or(-1),
                    String::from_utf8_lossy(&out.stderr).trim()
                )),
                Err(e) => warnings.push(format!("power limit: {e}")),
            }
        } else {
            applied.push(format!("power={power} W"));
        }
    }

    if applied.is_empty() && warnings.is_empty() {
        return Ok("No changes requested.".to_string());
    }

    let mut msg = String::new();
    if !applied.is_empty() {
        msg.push_str(&format!("GPU profile applied , {}.", applied.join(", ")));
    }
    if !warnings.is_empty() {
        if !msg.is_empty() { msg.push_str("  "); }
        msg.push_str(&format!("Skipped: {}.", warnings.join(", ")));
    }
    Ok(msg)
}

/// Lock GPU to its peak boost clock (eliminates boost variance for consistent
/// frame pacing). Wayland-native via nvml_control.py.
pub fn lock_gpu_clocks_max() -> Result<String, String> {
    match nvml_control_run(&["lock-clocks", "max"]) {
        Ok(out) => Ok(out.trim().to_string()),
        Err(e) if e == "NVML_AUTH_CANCELLED"
               => Err("Clock lock cancelled (authentication dismissed)".into()),
        Err(e) => Err(e),
    }
}

/// Restore dynamic GPU boost (undo lock_gpu_clocks_max).
pub fn reset_gpu_clocks() -> Result<String, String> {
    match nvml_control_run(&["reset-clocks"]) {
        Ok(out) => Ok(out.trim().to_string()),
        Err(e) if e == "NVML_AUTH_CANCELLED"
               => Err("Clock reset cancelled (authentication dismissed)".into()),
        Err(e) => Err(e),
    }
}

/// Set GPU power limit in watts. Wayland-native via nvml_control.py.
pub fn set_power_limit_w(watts: f32) -> Result<String, String> {
    match nvml_control_run(&["set-power", &format!("{watts:.1}")]) {
        Ok(out) => Ok(out.trim().to_string()),
        Err(e) if e == "NVML_AUTH_CANCELLED"
               => Err("Power limit cancelled (authentication dismissed)".into()),
        Err(e) => Err(e),
    }
}

/// Reset GPU power limit to factory default.
pub fn reset_power_limit() -> Result<String, String> {
    match nvml_control_run(&["reset-power"]) {
        Ok(out) => Ok(out.trim().to_string()),
        Err(e) if e == "NVML_AUTH_CANCELLED"
               => Err("Power reset cancelled (authentication dismissed)".into()),
        Err(e) => Err(e),
    }
}

pub fn set_performance_mode(enabled: bool) -> Result<String, String> {
    // GPU PowerMizer mode , runs as the user (nvidia-settings reads
    // DISPLAY/Wayland session, no root needed).
    let mode = if enabled { "1" } else { "0" };
    let _ = Command::new("nvidia-settings")
        .args(["-a", &format!("[gpu:0]/GPUPowerMizerMode={}", mode)])
        .output();

    // CPU governor write , requires root.  The previous implementation
    // piped through `sudo` directly, but `sudo` has no way to prompt
    // for a password from a non-terminal GUI process , the command
    // would just silently fail with "sudo required".  We route through
    // pkexec instead, which pops a graphical PolicyKit dialog (the
    // same one apt-get/gnome-software uses).
    //
    // Falls back to `sudo` only if pkexec is genuinely missing, with a
    // clear error so the user knows to install policykit-1.
    let gov = if enabled { "performance" } else { "powersave" };
    let script = format!(
        "for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; \
         do echo {gov} > \"$f\"; done");
    let out = run_privileged(&["bash", "-c", &script])?;
    if out.status.success() {
        Ok(format!("CPU governor set to {gov}.  GPU PowerMizer = {mode}."))
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        Err(format!(
            "Failed to set CPU governor , exit {}: {}",
            out.status.code().unwrap_or(-1),
            stderr.trim()
        ))
    }
}

// ── Session history ──────────────────────────────────────────────────────
//
// Unified 2026-07-30 (greenboost_gaming_polish.md G3): this used to read a
// separate, independently-written ~/.local/share/greenboost/sessions.jsonl.
// The Proton wrapper (greenboost_proton/proton) already emitted a
// `{"kind": "gaming_session", "action": "stop", ...}` event into core
// GreenBoost's shared dataflux log (~/.local/share/greenboost/dataflux.jsonl,
// the same stream get_dataflux_recent_impl below reads for the Live activity
// panel) carrying the same session-summary fields , two independent write
// paths for the same data. sessions.jsonl is no longer written; this now
// reads the dataflux event instead, one write path, one read path.
//
// Note: dataflux.jsonl is size/age-bounded (core's GB_DATAFLUX_MAX_BYTES
// rotation + GB_DATAFLUX_RETAIN_DAYS archive compaction, ~7 days by
// default) , unlike the old sessions.jsonl, which grew forever. Session
// history here reflects that same retention window, not unlimited history.

/// One finished game session. Field values are pulled defensively out of
/// the raw dataflux event JSON (`.get()` + fallback), not via a strict
/// `serde_json` struct deserialize , dataflux event shape varies by `kind`
/// and core adds fields routinely (this project's own rule; see
/// get_dataflux_recent_impl's doc comment below), so trusting a fixed
/// schema here would silently break the moment a field is renamed.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SessionRecord {
    pub appid:      String,
    #[serde(default)]
    pub game_name:  String,
    pub gpu:        String,
    /// GPU's total VRAM capacity (nvidia-smi memory.total) at launch time ,
    /// NOT usage. Kept for the frontend's "peak/total" display; never use
    /// this as a fallback for peak/avg usage (see analyze_game_sessions_impl).
    pub vram_mb:    i64,
    /// Peak VRAM seen during the session via background NVML polling.
    /// Per-process (this game's own footprint) when `vram_source ==
    /// "process"`; whole-GPU when `"gpu_total"` (older records, or when
    /// per-process NVML accounting was unavailable at capture time).
    #[serde(default)]
    pub peak_vram_mb: i64,
    /// Mean of all VRAM samples taken during the session (same source as
    /// peak_vram_mb). 0 when `vram_samples == 0` (session too short to
    /// sample, or predates this field).
    #[serde(default)]
    pub avg_vram_mb: i64,
    #[serde(default)]
    pub vram_samples: i64,
    /// "process" (this game's own PID tree) or "gpu_total" (whole-GPU
    /// fallback) , empty string for records written before this field
    /// existed. See greenboost_proton/proton's `_vram_tracker_worker`.
    #[serde(default)]
    pub vram_source: String,
    /// Peak T2 DDR spill (MB) observed during the session, from the kernel
    /// module's `status` sysfs file via `_check_t2t3_pressure`. 0 when
    /// nothing spilled or the kernel module wasn't loaded.
    #[serde(default)]
    pub peak_t2_mb: i64,
    pub duration_s: f64,
    pub rc:         i32,
    pub ts:         String,
}

/// Days-since-epoch -> (year, month, day), Howard Hinnant's civil_from_days
/// algorithm (public domain, standard Gregorian-calendar inverse , no date
/// crate is a dependency of this project, so this is self-contained).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Unix epoch seconds -> UTC ISO-8601 string ("...Z"), matching the exact
/// format the old sessions.jsonl writer used (`time.strftime("%Y-%m-%dT%H:%M:%SZ",
/// time.gmtime())`) so the frontend's `new Date(s.ts)` parsing in
/// Status.tsx's formatRelativeTime() needs no change.
fn epoch_to_iso(ts: f64) -> String {
    let secs = ts.floor() as i64;
    let days = secs.div_euclid(86400);
    let rem = secs.rem_euclid(86400);
    let (y, m, d) = civil_from_days(days);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
            y, m, d, rem / 3600, (rem % 3600) / 60, rem % 60)
}

/// Read every finished-session (`gaming_session`/`stop`) event out of
/// dataflux.jsonl, oldest-first (the log's natural append order). Missing
/// file (core not installed, or no session has run yet) returns empty, not
/// an error , same convention as get_dataflux_recent_impl.
fn read_gaming_session_stops() -> Vec<SessionRecord> {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::path::Path::new(&home)
        .join(".local/share/greenboost/dataflux.jsonl");
    let text = match std::fs::read_to_string(&path) {
        Ok(t)  => t,
        Err(_) => return Vec::new(),
    };
    text.lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter(|v| {
            v.get("kind").and_then(|k| k.as_str()) == Some("gaming_session")
                && v.get("action").and_then(|a| a.as_str()) == Some("stop")
        })
        .map(|v| {
            let duration_s = v.get("elapsed_s").and_then(|x| x.as_f64()).unwrap_or(0.0);
            SessionRecord {
                appid:         v.get("appid").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                game_name:     String::new(),
                gpu:           v.get("gpu").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                vram_mb:       v.get("vram_mb").and_then(|x| x.as_i64()).unwrap_or(0),
                peak_vram_mb:  v.get("peak_vram_mb").and_then(|x| x.as_i64()).unwrap_or(0),
                avg_vram_mb:   v.get("avg_vram_mb").and_then(|x| x.as_i64()).unwrap_or(0),
                vram_samples:  v.get("vram_samples").and_then(|x| x.as_i64()).unwrap_or(0),
                vram_source:   v.get("vram_source").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                peak_t2_mb:    v.get("peak_t2_mb").and_then(|x| x.as_i64()).unwrap_or(0),
                duration_s:    (duration_s * 10.0).round() / 10.0,
                rc:            v.get("rc").and_then(|x| x.as_i64()).unwrap_or(0) as i32,
                ts:            v.get("ts").and_then(|x| x.as_f64())
                                   .map(epoch_to_iso).unwrap_or_default(),
            }
        })
        // Steam spawns short-lived auxiliary wrapper invocations (compat-path
        // probes, launcher shims) with SteamGameId unset and a 1-4s lifetime ,
        // not real game sessions. Without this filter they crowd out real
        // sessions in get_session_history_impl's last-20 window.
        .filter(|r| !(r.appid.is_empty() && r.duration_s < 5.0))
        .collect()
}

pub fn get_session_history_impl() -> Vec<SessionRecord> {
    let mut records = read_gaming_session_stops();
    // Keep the last 20, then reverse so the newest is first.
    let len = records.len();
    if len > 20 {
        records = records.into_iter().skip(len - 20).collect();
    }
    records.reverse();

    // Enrich with Steam game names.
    let name_map = crate::scanner::steam_game_name_map();
    for r in &mut records {
        if r.game_name.is_empty() {
            r.game_name = name_map.get(&r.appid).cloned().unwrap_or_default();
        }
    }

    records
}

#[derive(serde::Serialize, Clone, Debug)]
pub struct GameAnalytics {
    pub appid:             String,
    pub game_name:         String,
    pub session_count:     u32,
    /// Mean of per-session avg_vram_mb, over sessions that have samples.
    /// 0 when no session recorded any samples yet.
    pub avg_vram_mb:       i64,
    /// Max of per-session peak_vram_mb, over sessions that have samples.
    /// 0 when no session recorded any samples yet ("no data" , never
    /// falls back to the GPU's total capacity).
    pub peak_vram_mb:      i64,
    /// True when peak/avg above are known to include VRAM from other GPU
    /// processes (any sampled session used the "gpu_total" fallback) ,
    /// lets the UI caveat the figure instead of presenting it as this
    /// game's exact footprint.
    pub vram_includes_other_apps: bool,
    pub avg_duration_min:  f64,
    pub total_play_hours:  f64,
    /// Total GPU VRAM in MB (from NVML at query time); 0 when NVML unavailable.
    pub gpu_total_vram_mb: i64,
    /// Max T2 DDR spill (MB) seen across recorded sessions; 0 if this game
    /// never spilled (or no session recorded T2 data).
    pub peak_t2_mb:        i64,
}

pub fn analyze_game_sessions_impl(appid: &str) -> GameAnalytics {
    let records: Vec<SessionRecord> = read_gaming_session_stops()
        .into_iter()
        .filter(|r| r.appid == appid)
        .collect();

    let gpu_total_vram_mb: i64 = crate::nvml_reader::read_snapshot()
        .and_then(|s| s.mem_total)
        .map(|b| (b / 1_048_576) as i64)
        .unwrap_or(0);

    if records.is_empty() {
        return GameAnalytics {
            appid:             appid.to_string(),
            game_name:         String::new(),
            session_count:     0,
            avg_vram_mb:       0,
            peak_vram_mb:      0,
            vram_includes_other_apps: false,
            avg_duration_min:  0.0,
            total_play_hours:  0.0,
            gpu_total_vram_mb,
            peak_t2_mb:        0,
        };
    }

    let session_count = records.len() as u32;
    // Only records that actually sampled VRAM carry a meaningful
    // peak/avg , `vram_mb` is the GPU's static total capacity, never a
    // usage figure, so it must never be used as a peak/avg fallback (that
    // was the source of the "every session reads the same GB" bug).
    let sampled: Vec<&SessionRecord> = records.iter().filter(|r| r.vram_samples > 0).collect();
    let peak_vram_mb: i64 = sampled.iter().map(|r| r.peak_vram_mb).max().unwrap_or(0);
    let avg_vram_mb: i64 = if sampled.is_empty() {
        0
    } else {
        sampled.iter().map(|r| r.avg_vram_mb).sum::<i64>() / sampled.len() as i64
    };
    let vram_includes_other_apps = sampled.iter().any(|r| r.vram_source == "gpu_total");
    let peak_t2_mb: i64 = records.iter().map(|r| r.peak_t2_mb).max().unwrap_or(0);

    let total_dur_s: f64 = records.iter().map(|r| r.duration_s).sum();
    let avg_duration_min = (total_dur_s / session_count as f64) / 60.0;
    let total_play_hours = total_dur_s / 3600.0;

    let game_name = {
        let name_map = crate::scanner::steam_game_name_map();
        name_map.get(appid).cloned().unwrap_or_default()
    };

    GameAnalytics {
        appid: appid.to_string(),
        game_name,
        session_count,
        avg_vram_mb,
        peak_vram_mb,
        vram_includes_other_apps,
        avg_duration_min,
        total_play_hours,
        gpu_total_vram_mb,
        peak_t2_mb,
    }
}

/// Streaming GreenBoost kernel module install.
/// 1. Clones https://gitlab.com/IsolatedOctopi/greenboost into
///    ~/Dev/greenboost_all/greenboost (or does `git pull` if the dir
///    already exists).
/// 2. Runs `install.sh` from that directory via pkexec/sudo so DKMS
///    and the kernel module are installed system-wide.
/// All stdout + stderr lines are forwarded to the Tauri Channel so the
/// frontend InstallStreamModal can display them live.
pub fn install_greenboost_module_streaming(channel: Channel<String>)
    -> Result<i32, String>
{
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    let target = format!("{home}/Dev/greenboost_all/greenboost");
    let target_path = std::path::Path::new(&target);

    // ── Step 1: clone or pull ──────────────────────────────────────
    let _ = channel.send("── Fetching GreenBoost sources from GitLab …".into());
    if target_path.join(".git").exists() {
        let _ = channel.send(format!("  → Found existing repo at {target}, pulling latest …"));
        let rc = run_script_streaming(&["git", "-C", &target, "pull", "--rebase"], &channel)?;
        if rc != 0 {
            let _ = channel.send(format!("  WARNING: git pull exited {rc} , proceeding with existing sources."));
        }
    } else {
        std::fs::create_dir_all(format!("{home}/Dev/greenboost_all"))
            .map_err(|e| format!("Could not create parent dir: {e}"))?;
        let _ = channel.send(format!("  → Cloning into {target} …"));
        let rc = run_script_streaming(
            &["git", "clone",
              "https://gitlab.com/IsolatedOctopi/greenboost.git",
              &target],
            &channel,
        )?;
        if rc != 0 {
            return Ok(rc);
        }
    }

    // ── Step 2: run the module installer (needs root for DKMS / insmod) ─
    // The parent repo ships install_module.sh (DKMS-only, hardware-agnostic).
    // install.sh is kept as a forward-compat fallback; if neither exists we
    // fall back to `make install` which covers the dkms-install + install-libs
    // targets.
    let script_candidates = ["install_module.sh", "install.sh"];
    let script_path = script_candidates.iter()
        .map(|name| format!("{target}/{name}"))
        .find(|p| std::path::Path::new(p).exists());

    match script_path {
        Some(script) => {
            let _ = channel.send(
                format!("── Running {} (requesting authorization) …",
                    std::path::Path::new(&script)
                        .file_name().unwrap_or_default()
                        .to_string_lossy()));
            let argv: Vec<&str> = if which::which("pkexec").is_ok() {
                vec!["pkexec", "bash", &script]
            } else {
                vec!["sudo", "bash", &script]
            };
            run_script_streaming(&argv, &channel)
        }
        None => {
            // Neither install_module.sh nor install.sh present , use the
            // Makefile `install` target (dkms-install + install-libs).
            let _ = channel.send(
                "  → No install script found , falling back to `make install` …".into());
            let argv: Vec<&str> = if which::which("pkexec").is_ok() {
                vec!["pkexec", "make", "-C", &target, "install"]
            } else {
                vec!["sudo", "make", "-C", &target, "install"]
            };
            run_script_streaming(&argv, &channel)
        }
    }
}

#[cfg(test)]
mod session_history_tests {
    use super::*;

    #[test]
    fn epoch_to_iso_matches_known_utc_instant() {
        // 2026-07-30T18:00:00Z per `date -u -d "2026-07-30T18:00:00Z" +%s`.
        assert_eq!(epoch_to_iso(1785434400.0), "2026-07-30T18:00:00Z");
        // Unix epoch itself.
        assert_eq!(epoch_to_iso(0.0), "1970-01-01T00:00:00Z");
    }

    #[test]
    fn read_gaming_session_stops_filters_and_parses_dataflux_events() {
        let dir = std::env::temp_dir().join(format!(
            "gb_session_test_{}_{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(dir.join(".local/share/greenboost")).unwrap();
        let log_path = dir.join(".local/share/greenboost/dataflux.jsonl");
        std::fs::write(&log_path, concat!(
            r#"{"kind":"snapshot","ts":1785600000.0,"node":"host"}"#, "\n",
            r#"{"kind":"gaming_session","action":"start","appid":"123","gpu":"RTX 5070","ts":1785600100.0}"#, "\n",
            r#"{"kind":"gaming_session","action":"stop","appid":"123","gpu":"RTX 5070","vram_mb":12288,"peak_vram_mb":9000,"elapsed_s":125.449,"rc":0,"ts":1785600200.0}"#, "\n",
        )).unwrap();

        let saved_home = std::env::var("HOME").ok();
        std::env::set_var("HOME", &dir);
        let records = read_gaming_session_stops();
        if let Some(h) = saved_home { std::env::set_var("HOME", h); }
        let _ = std::fs::remove_dir_all(&dir);

        // Only the one "stop" event survives , snapshot and "start" are excluded.
        assert_eq!(records.len(), 1);
        let r = &records[0];
        assert_eq!(r.appid, "123");
        assert_eq!(r.gpu, "RTX 5070");
        assert_eq!(r.vram_mb, 12288);
        assert_eq!(r.peak_vram_mb, 9000);
        assert_eq!(r.duration_s, 125.4); // rounded to 1 decimal, matching the old sessions.jsonl format
        assert_eq!(r.rc, 0);
        assert_eq!(r.ts, epoch_to_iso(1785600200.0));
    }
}

#[cfg(test)]
mod compat_tool_mapping_tests {
    use super::*;

    fn temp_config_vdf(name: &str, content: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "gb_vdf_test_{name}_{}_{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .unwrap().as_nanos()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("config.vdf");
        std::fs::write(&path, content).unwrap();
        path
    }

    // Trimmed but structurally real snippet, matching what's actually on
    // disk in ~/.local/share/Steam/config/config.vdf (verified live
    // 2026-08-08) , tab-indented, one sibling entry already present.
    const SAMPLE: &str = "\"InstallConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"CompatToolMapping\"\n\t\t\t\t{\n\t\t\t\t\t\"3768760\"\n\t\t\t\t\t{\n\t\t\t\t\t\t\"name\"\t\t\"proton_11\"\n\t\t\t\t\t\t\"config\"\t\t\"\"\n\t\t\t\t\t\t\"priority\"\t\t\"250\"\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n";

    #[test]
    fn inserts_new_entry_when_appid_absent() {
        let path = temp_config_vdf("insert", SAMPLE);
        let content = std::fs::read_to_string(&path).unwrap();
        let changed = set_compat_tool_mapping(&path, &content, "2680010").unwrap();
        assert!(changed);

        let new_content = std::fs::read_to_string(&path).unwrap();
        // The pre-existing sibling entry must survive untouched.
        assert!(new_content.contains("\"3768760\""));
        assert!(new_content.contains("\"proton_11\""));
        // The new entry must be present, well-formed, and mapped correctly.
        let (s, e) = find_brace_block(&new_content, "2680010")
            .expect("new appid block not found or malformed");
        let sub = &new_content[s..e];
        assert!(sub.contains("\"name\"\t\t\"greenboost-proton\""));
        // Overall brace balance must still hold , a corrupt VDF would
        // silently break Steam.
        assert_eq!(new_content.matches('{').count(), new_content.matches('}').count());

        // A timestamped backup must exist.
        let dir = path.parent().unwrap();
        assert!(std::fs::read_dir(dir).unwrap()
            .flatten().any(|e| e.file_name().to_string_lossy().contains("config.vdf.gb.")));

        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn corrects_existing_entry_with_wrong_tool() {
        let path = temp_config_vdf("correct", SAMPLE);
        let content = std::fs::read_to_string(&path).unwrap();
        let changed = set_compat_tool_mapping(&path, &content, "3768760").unwrap();
        assert!(changed);

        let new_content = std::fs::read_to_string(&path).unwrap();
        let (s, e) = find_brace_block(&new_content, "3768760").unwrap();
        assert!(new_content[s..e].contains("\"name\"\t\t\"greenboost-proton\""));
        assert!(!new_content[s..e].contains("proton_11"));
        assert_eq!(new_content.matches('{').count(), new_content.matches('}').count());

        std::fs::remove_dir_all(path.parent().unwrap()).ok();
    }

    #[test]
    fn already_correct_makes_no_change_and_no_backup() {
        let already = SAMPLE.replace("proton_11", "greenboost-proton");
        let path = temp_config_vdf("noop", &already);
        let content = std::fs::read_to_string(&path).unwrap();
        let changed = set_compat_tool_mapping(&path, &content, "3768760").unwrap();
        assert!(!changed);

        let dir = path.parent().unwrap();
        assert!(!std::fs::read_dir(dir).unwrap()
            .flatten().any(|e| e.file_name().to_string_lossy().contains("config.vdf.gb.")),
            "no-op call must not create a backup file");

        std::fs::remove_dir_all(dir).ok();
    }
}
