# Copyright 2026 Ferran Duarri , GPL v2
# GreenBoost is an independent open-source project and is not affiliated with,
# endorsed by, or sponsored by NVIDIA Corporation.
# NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.
#
# nvml_fan.py , NVML-based fan speed control helper.
# Must be run as root (via pkexec / sudo):
#   pkexec python3 /path/to/nvml_fan.py set <0-100>
#   pkexec python3 /path/to/nvml_fan.py auto
#
# Fully Wayland-native: uses libnvidia-ml.so.1 directly, no X11 required.
import ctypes
import sys

NVML_SUCCESS = 0
# nvmlFanControlPolicy_t values
NVML_FAN_POLICY_TEMPERATURE_CONTINOUS_SW = 0
NVML_FAN_POLICY_MANUAL = 1


def _load_nvml():
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: nvml_fan.py set <0-100> | auto", file=sys.stderr, flush=True)
        sys.exit(1)

    action = sys.argv[1]
    speed = 0

    if action == "set":
        if len(sys.argv) < 3:
            print("Usage: nvml_fan.py set <0-100>", file=sys.stderr, flush=True)
            sys.exit(1)
        try:
            speed = int(sys.argv[2])
            speed = max(0, min(100, speed))
        except ValueError:
            print(f"Invalid speed: {sys.argv[2]}", file=sys.stderr, flush=True)
            sys.exit(1)
    elif action != "auto":
        print(f"Unknown action: {action}", file=sys.stderr, flush=True)
        sys.exit(1)

    nvml = _load_nvml()
    if nvml is None:
        print("ERROR: libnvidia-ml.so.1 not found , is the NVIDIA driver installed?",
              flush=True)
        sys.exit(1)

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

    num_fans = ctypes.c_uint(0)
    ret = nvml.nvmlDeviceGetNumFans(device, ctypes.byref(num_fans))
    if ret != NVML_SUCCESS:
        print(f"ERROR: nvmlDeviceGetNumFans returned {ret}", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)

    n = num_fans.value
    if n == 0:
        print("ERROR: GPU reports 0 fans , passive cooler or unsupported card", flush=True)
        nvml.nvmlShutdown()
        sys.exit(1)

    print(f"GPU has {n} fan(s)", flush=True)

    errors = 0
    for i in range(n):
        if action == "set":
            ret = nvml.nvmlDeviceSetFanSpeed_v2(
                device, ctypes.c_uint(i), ctypes.c_uint(speed))
            if ret == NVML_SUCCESS:
                print(f"  Fan {i}: set to {speed}%", flush=True)
            else:
                print(f"  ERROR: Fan {i}: nvmlDeviceSetFanSpeed_v2 returned {ret}", flush=True)
                errors += 1
        else:
            ret = nvml.nvmlDeviceSetDefaultFanSpeed_v2(device, ctypes.c_uint(i))
            if ret == NVML_SUCCESS:
                print(f"  Fan {i}: reset to automatic control", flush=True)
            else:
                print(f"  ERROR: Fan {i}: nvmlDeviceSetDefaultFanSpeed_v2 returned {ret}",
                      flush=True)
                errors += 1

    nvml.nvmlShutdown()

    if errors:
        print(f"DONE with {errors} error(s)", flush=True)
        sys.exit(1)

    print("DONE", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
