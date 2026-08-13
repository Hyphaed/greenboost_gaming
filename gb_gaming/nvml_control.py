# Copyright 2026 Ferran Duarri , GPL v2
# GreenBoost is an independent open-source project and is not affiliated with,
# endorsed by, or sponsored by NVIDIA Corporation.
# NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.
#
# nvml_control.py , NVML-based GPU power limit and clock lock control.
# Must be run as root (via pkexec / sudo):
#
#   pkexec python3 nvml_control.py set-power <watts>
#   pkexec python3 nvml_control.py reset-power
#   pkexec python3 nvml_control.py lock-clocks <min_mhz> <max_mhz>
#   pkexec python3 nvml_control.py lock-clocks max        # lock at peak boost
#   pkexec python3 nvml_control.py reset-clocks
#   pkexec python3 nvml_control.py lock-mem-clocks <min_mhz> <max_mhz>
#   pkexec python3 nvml_control.py reset-mem-clocks
#   pkexec python3 nvml_control.py query                  # print current state
#
# Fully Wayland-native: uses libnvidia-ml.so.1 directly, no X11 required.
# All write operations require Maxwell+ (clock locks require Volta+ for memory
# locks, Volta+ for GPU clock lock; power limits require Kepler+).
import ctypes
import sys

NVML_SUCCESS = 0
NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_MEM = 2
NVML_TEMPERATURE_GPU = 0


def _load_nvml():
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def _init(nvml):
    ret = nvml.nvmlInit_v2()
    if ret != NVML_SUCCESS:
        print(f"ERROR: nvmlInit_v2 returned {ret}", flush=True)
        sys.exit(1)
    device = ctypes.c_void_p()
    ret = nvml.nvmlDeviceGetHandleByIndex(ctypes.c_uint(0), ctypes.byref(device))
    if ret != NVML_SUCCESS:
        print(f"ERROR: nvmlDeviceGetHandleByIndex returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)
    return device


def _get_max_clock_mhz(nvml, device, clock_type: int) -> int | None:
    """Query the maximum supported clock for a given clock domain."""
    # nvmlDeviceGetMaxClockInfo(device, clockType, *clock)
    f = ctypes.c_uint(0)
    ret = nvml.nvmlDeviceGetMaxClockInfo(device, ctypes.c_uint(clock_type), ctypes.byref(f))
    if ret == NVML_SUCCESS:
        return int(f.value)
    return None


def cmd_set_power(nvml, device, args):
    if not args:
        print("Usage: set-power <watts>", file=sys.stderr, flush=True)
        sys.exit(1)
    try:
        watts = float(args[0])
    except ValueError:
        print(f"Invalid watts value: {args[0]}", file=sys.stderr, flush=True)
        sys.exit(1)
    milliwatts = int(watts * 1000)
    # nvmlDeviceSetPowerManagementLimit(device, limit_mW)
    ret = nvml.nvmlDeviceSetPowerManagementLimit(device, ctypes.c_uint(milliwatts))
    if ret == NVML_SUCCESS:
        print(f"Power limit set to {watts:.1f} W ({milliwatts} mW)", flush=True)
    else:
        print(f"ERROR: nvmlDeviceSetPowerManagementLimit returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_reset_power(nvml, device, _args):
    # Read the default power limit first.
    default_mw = ctypes.c_uint(0)
    ret = nvml.nvmlDeviceGetPowerManagementDefaultLimit(device, ctypes.byref(default_mw))
    if ret != NVML_SUCCESS:
        print(f"ERROR: nvmlDeviceGetPowerManagementDefaultLimit returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)
    ret = nvml.nvmlDeviceSetPowerManagementLimit(device, default_mw)
    if ret == NVML_SUCCESS:
        print(f"Power limit reset to default ({default_mw.value / 1000:.1f} W)", flush=True)
    else:
        print(f"ERROR: nvmlDeviceSetPowerManagementLimit returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_lock_clocks(nvml, device, args):
    if not args:
        print("Usage: lock-clocks <min_mhz> <max_mhz>  |  lock-clocks max",
              file=sys.stderr, flush=True)
        sys.exit(1)

    if args[0] == "max":
        max_mhz = _get_max_clock_mhz(nvml, device, NVML_CLOCK_GRAPHICS)
        if max_mhz is None:
            print("ERROR: could not query max GPU clock", flush=True)
            nvml.nvmlShutdown()
            sys.exit(1)
        min_mhz = max_mhz
    else:
        if len(args) < 2:
            print("Usage: lock-clocks <min_mhz> <max_mhz>", file=sys.stderr, flush=True)
            sys.exit(1)
        try:
            min_mhz = int(args[0])
            max_mhz = int(args[1])
        except ValueError:
            print(f"Invalid clock values: {args[:2]}", file=sys.stderr, flush=True)
            sys.exit(1)

    # nvmlDeviceSetGpuLockedClocks(device, minGpuClockMHz, maxGpuClockMHz)
    ret = nvml.nvmlDeviceSetGpuLockedClocks(
        device, ctypes.c_uint(min_mhz), ctypes.c_uint(max_mhz))
    if ret == NVML_SUCCESS:
        print(f"GPU clocks locked to {min_mhz}–{max_mhz} MHz", flush=True)
    else:
        print(f"ERROR: nvmlDeviceSetGpuLockedClocks returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_reset_clocks(nvml, device, _args):
    # nvmlDeviceResetGpuLockedClocks(device)
    ret = nvml.nvmlDeviceResetGpuLockedClocks(device)
    if ret == NVML_SUCCESS:
        print("GPU clocks unlocked (dynamic boost restored)", flush=True)
    else:
        print(f"ERROR: nvmlDeviceResetGpuLockedClocks returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_lock_mem_clocks(nvml, device, args):
    if len(args) < 2:
        print("Usage: lock-mem-clocks <min_mhz> <max_mhz>", file=sys.stderr, flush=True)
        sys.exit(1)
    try:
        min_mhz = int(args[0])
        max_mhz = int(args[1])
    except ValueError:
        print(f"Invalid memory clock values: {args[:2]}", file=sys.stderr, flush=True)
        sys.exit(1)
    # nvmlDeviceSetMemoryLockedClocks(device, minMemClockMHz, maxMemClockMHz)
    ret = nvml.nvmlDeviceSetMemoryLockedClocks(
        device, ctypes.c_uint(min_mhz), ctypes.c_uint(max_mhz))
    if ret == NVML_SUCCESS:
        print(f"Memory clocks locked to {min_mhz}–{max_mhz} MHz", flush=True)
    else:
        print(f"ERROR: nvmlDeviceSetMemoryLockedClocks returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_reset_mem_clocks(nvml, device, _args):
    # nvmlDeviceResetMemoryLockedClocks(device)
    ret = nvml.nvmlDeviceResetMemoryLockedClocks(device)
    if ret == NVML_SUCCESS:
        print("Memory clocks unlocked (dynamic control restored)", flush=True)
    else:
        print(f"ERROR: nvmlDeviceResetMemoryLockedClocks returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)


def cmd_query(nvml, device, _args):
    """Print current power and clock state (read-only, non-privileged paths)."""
    power_mw = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetPowerUsage(device, ctypes.byref(power_mw)) == NVML_SUCCESS:
        print(f"power_usage_w={power_mw.value / 1000:.1f}", flush=True)

    limit_mw = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetPowerManagementLimit(device, ctypes.byref(limit_mw)) == NVML_SUCCESS:
        print(f"power_limit_w={limit_mw.value / 1000:.1f}", flush=True)

    default_mw = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetPowerManagementDefaultLimit(device, ctypes.byref(default_mw)) == NVML_SUCCESS:
        print(f"power_limit_default_w={default_mw.value / 1000:.1f}", flush=True)

    min_mw = ctypes.c_uint(0)
    max_mw = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetPowerManagementLimitConstraints(
            device, ctypes.byref(min_mw), ctypes.byref(max_mw)) == NVML_SUCCESS:
        print(f"power_limit_min_w={min_mw.value / 1000:.1f}", flush=True)
        print(f"power_limit_max_w={max_mw.value / 1000:.1f}", flush=True)

    gpu_clock = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetClockInfo(device, ctypes.c_uint(NVML_CLOCK_GRAPHICS),
                                    ctypes.byref(gpu_clock)) == NVML_SUCCESS:
        print(f"clock_graphics_mhz={gpu_clock.value}", flush=True)

    max_gpu = _get_max_clock_mhz(nvml, device, NVML_CLOCK_GRAPHICS)
    if max_gpu:
        print(f"clock_graphics_max_mhz={max_gpu}", flush=True)

    mem_clock = ctypes.c_uint(0)
    if nvml.nvmlDeviceGetClockInfo(device, ctypes.c_uint(NVML_CLOCK_MEM),
                                    ctypes.byref(mem_clock)) == NVML_SUCCESS:
        print(f"clock_mem_mhz={mem_clock.value}", flush=True)

    max_mem = _get_max_clock_mhz(nvml, device, NVML_CLOCK_MEM)
    if max_mem:
        print(f"clock_mem_max_mhz={max_mem}", flush=True)

    print("DONE", flush=True)


COMMANDS = {
    "set-power":        cmd_set_power,
    "reset-power":      cmd_reset_power,
    "lock-clocks":      cmd_lock_clocks,
    "reset-clocks":     cmd_reset_clocks,
    "lock-mem-clocks":  cmd_lock_mem_clocks,
    "reset-mem-clocks": cmd_reset_mem_clocks,
    "query":            cmd_query,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(
            "Usage: nvml_control.py <command> [args]\n"
            "Commands: " + " | ".join(COMMANDS),
            file=sys.stderr, flush=True)
        sys.exit(1)

    nvml = _load_nvml()
    if nvml is None:
        print("ERROR: libnvidia-ml.so.1 not found , is the NVIDIA driver installed?", flush=True)
        sys.exit(1)

    device = _init(nvml)
    try:
        COMMANDS[sys.argv[1]](nvml, device, sys.argv[2:])
    finally:
        nvml.nvmlShutdown()

    print("DONE", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
