// Copyright 2026 Ferran Duarri , GPL v2
// NVML-based GPU monitoring , reads temperature, power, clocks, utilization,
// memory and fan speeds by dlopen-ing libnvidia-ml.so.1 at runtime.
// All reads are non-privileged; no root required.
// Falls back gracefully when NVML is unavailable (libnvidia-ml not found,
// old driver, etc.) , callers always get Option<T>.

use std::ffi::c_void;

// NVML opaque handle
type NvmlDevice = *mut c_void;
type NvmlReturn = u32;

const NVML_SUCCESS: u32 = 0;
const NVML_TEMPERATURE_GPU: u32 = 0;
const NVML_CLOCK_GRAPHICS: u32 = 0;
const NVML_CLOCK_SM: u32 = 1;
const NVML_CLOCK_MEM: u32 = 2;
const NVML_CLOCK_VIDEO: u32 = 3;

// ── Structs that mirror the NVML C layout ────────────────────────────────

#[repr(C)]
struct NvmlMemory { total: u64, free: u64, used: u64 }

#[repr(C)]
struct NvmlUtilization { gpu: u32, memory: u32 }

// ── Public snapshot type ──────────────────────────────────────────────────

pub struct NvmlSnapshot {
    pub temp_gpu:       Option<i32>,   // °C
    pub power_mw:       Option<u32>,   // milliwatts
    pub power_limit_mw: Option<u32>,   // milliwatts (enforced)
    pub clock_graphics: Option<u32>,   // MHz
    pub clock_sm:       Option<u32>,   // MHz
    pub clock_mem:      Option<u32>,   // MHz
    pub clock_video:    Option<u32>,   // MHz
    pub mem_total:      Option<u64>,   // bytes
    pub mem_used:       Option<u64>,   // bytes
    pub gpu_util:       Option<u32>,   // %
    pub mem_util:       Option<u32>,   // %
    pub fan_speeds:     Vec<u32>,      // per-fan %
}

impl NvmlSnapshot {
    pub fn fan_speed_avg(&self) -> Option<u32> {
        if self.fan_speeds.is_empty() { return None; }
        Some(self.fan_speeds.iter().sum::<u32>() / self.fan_speeds.len() as u32)
    }
}

// ── Function-pointer type aliases ────────────────────────────────────────

type FnNoArgs    = unsafe extern "C" fn() -> NvmlReturn;
type FnGetDevice = unsafe extern "C" fn(u32, *mut NvmlDevice) -> NvmlReturn;
type FnGetTemp   = unsafe extern "C" fn(NvmlDevice, u32, *mut u32) -> NvmlReturn;
type FnGetU32    = unsafe extern "C" fn(NvmlDevice, *mut u32) -> NvmlReturn;
type FnGetClock  = unsafe extern "C" fn(NvmlDevice, u32, *mut u32) -> NvmlReturn;
type FnGetMem    = unsafe extern "C" fn(NvmlDevice, *mut NvmlMemory) -> NvmlReturn;
type FnGetUtil   = unsafe extern "C" fn(NvmlDevice, *mut NvmlUtilization) -> NvmlReturn;
type FnGetFanV2  = unsafe extern "C" fn(NvmlDevice, u32, *mut u32) -> NvmlReturn;

unsafe fn load_fn<T: Copy>(lib: &libloading::Library, name: &[u8]) -> Option<T> {
    lib.get::<T>(name).ok().map(|s| *s)
}

/// Read one GPU snapshot from NVML (GPU 0).  Returns None on any init failure.
pub fn read_snapshot() -> Option<NvmlSnapshot> {
    let lib = unsafe {
        libloading::Library::new("libnvidia-ml.so.1")
            .or_else(|_| libloading::Library::new("libnvidia-ml.so"))
            .ok()?
    };

    // Load mandatory symbols , bail if any is missing.
    let nvml_init:   FnNoArgs    = unsafe { load_fn(&lib, b"nvmlInit_v2\0")? };
    let nvml_shutdown: FnNoArgs  = unsafe { load_fn(&lib, b"nvmlShutdown\0")? };
    let get_handle: FnGetDevice  = unsafe { load_fn(&lib, b"nvmlDeviceGetHandleByIndex\0")? };

    // Optional symbols , gracefully degrade when absent.
    let get_temp:      Option<FnGetTemp>  = unsafe { load_fn(&lib, b"nvmlDeviceGetTemperature\0") };
    let get_power:     Option<FnGetU32>   = unsafe { load_fn(&lib, b"nvmlDeviceGetPowerUsage\0") };
    let get_pwr_lim:   Option<FnGetU32>   = unsafe { load_fn(&lib, b"nvmlDeviceGetEnforcedPowerLimit\0") };
    let get_clock:     Option<FnGetClock> = unsafe { load_fn(&lib, b"nvmlDeviceGetClockInfo\0") };
    let get_memory:    Option<FnGetMem>   = unsafe { load_fn(&lib, b"nvmlDeviceGetMemoryInfo\0") };
    let get_util:      Option<FnGetUtil>  = unsafe { load_fn(&lib, b"nvmlDeviceGetUtilizationRates\0") };
    let get_num_fans:  Option<FnGetU32>   = unsafe { load_fn(&lib, b"nvmlDeviceGetNumFans\0") };
    let get_fan_v2:    Option<FnGetFanV2> = unsafe { load_fn(&lib, b"nvmlDeviceGetFanSpeed_v2\0") };

    // Initialize NVML.
    if unsafe { nvml_init() } != NVML_SUCCESS { return None; }

    let mut device: NvmlDevice = std::ptr::null_mut();
    let rc = unsafe { get_handle(0, &mut device) };
    if rc != NVML_SUCCESS {
        unsafe { nvml_shutdown() };
        return None;
    }

    // ── Helpers that read a single u32 attribute ──────────────────────

    let read_temp = || -> Option<i32> {
        let f = get_temp?;
        let mut v: u32 = 0;
        if unsafe { f(device, NVML_TEMPERATURE_GPU, &mut v) } == NVML_SUCCESS {
            Some(v as i32)
        } else { None }
    };

    let read_u32_fn = |f: Option<FnGetU32>| -> Option<u32> {
        let f = f?;
        let mut v: u32 = 0;
        if unsafe { f(device, &mut v) } == NVML_SUCCESS { Some(v) } else { None }
    };

    let read_clock = |clock_type: u32| -> Option<u32> {
        let f = get_clock?;
        let mut v: u32 = 0;
        if unsafe { f(device, clock_type, &mut v) } == NVML_SUCCESS { Some(v) } else { None }
    };

    // ── Read all fields ───────────────────────────────────────────────

    let temp_gpu       = read_temp();
    let power_mw       = read_u32_fn(get_power);
    let power_limit_mw = read_u32_fn(get_pwr_lim);
    let clock_graphics = read_clock(NVML_CLOCK_GRAPHICS);
    let clock_sm       = read_clock(NVML_CLOCK_SM);
    let clock_mem      = read_clock(NVML_CLOCK_MEM);
    let clock_video    = read_clock(NVML_CLOCK_VIDEO);

    let (mem_total, mem_used) = {
        let mut m = NvmlMemory { total: 0, free: 0, used: 0 };
        if let Some(f) = get_memory {
            if unsafe { f(device, &mut m) } == NVML_SUCCESS {
                (Some(m.total), Some(m.used))
            } else { (None, None) }
        } else { (None, None) }
    };

    let (gpu_util, mem_util) = {
        let mut u = NvmlUtilization { gpu: 0, memory: 0 };
        if let Some(f) = get_util {
            if unsafe { f(device, &mut u) } == NVML_SUCCESS {
                (Some(u.gpu), Some(u.memory))
            } else { (None, None) }
        } else { (None, None) }
    };

    let fan_speeds: Vec<u32> = match (get_num_fans, get_fan_v2) {
        (Some(nf), Some(ff)) => {
            let mut n: u32 = 0;
            if unsafe { nf(device, &mut n) } == NVML_SUCCESS && n > 0 {
                (0..n).filter_map(|i| {
                    let mut v: u32 = 0;
                    if unsafe { ff(device, i, &mut v) } == NVML_SUCCESS { Some(v) } else { None }
                }).collect()
            } else { Vec::new() }
        }
        _ => Vec::new(),
    };

    unsafe { nvml_shutdown() };

    Some(NvmlSnapshot {
        temp_gpu,
        power_mw,
        power_limit_mw,
        clock_graphics,
        clock_sm,
        clock_mem,
        clock_video,
        mem_total,
        mem_used,
        gpu_util,
        mem_util,
        fan_speeds,
    })
}
