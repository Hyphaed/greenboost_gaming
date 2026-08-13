#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.fan_daemon , continuous fan-curve follower for NVIDIA GPUs.

The Profile panel's Fan Curve editor only applies its highest anchor on
click.  This daemon makes the curve *actually* follow temperature: it
polls the GPU every N seconds, linearly interpolates the configured
curve, and writes the resulting fan speed via `nvidia-settings`.

Run it as a user systemd service so it tracks login sessions and the
X / Wayland display the user is on (nvidia-settings needs DISPLAY).

Activation contract:
  - Reads ~/.config/greenboost-gaming/active_profile.json , a single
    JSON object {"name": "<profile-name>"}.  When that file is absent
    or empty, the daemon idles (no fan writes) and waits for the file
    to appear (polls every 5 s).
  - The profile itself is loaded via gb_gaming.gpu_profile.load_profile.
  - Curve format: list of [temp_c, fan_pct] pairs, sorted ascending.

Why a separate process and not just a thread inside the Tauri app?
  - Survives the GUI being closed.  Users tune a profile once, the
    daemon keeps applying it for the rest of the session.
  - Decouples failure modes , a crash in the GUI doesn't leave the GPU
    fan stuck at max.  Conversely a hung daemon doesn't freeze the UI.
  - Standard systemd life-cycle (start/stop/status, journald logging).

Stop the daemon to return the fan to driver-default behaviour:
  systemctl --user stop gb_gaming-fan-daemon
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Allow running from source without installing the package.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from gb_gaming import gpu_profile  # noqa: E402  (sys.path tweak above)

POLL_INTERVAL_S    = 2.0    # how often to read temp + write fan
IDLE_RECHECK_S     = 5.0    # how often to look for the active-profile file
# Temperature must drop this many °C below the last ramp-up point before the
# fan is allowed to decrease.  Prevents rapid fan oscillation when the GPU
# hovers near a curve inflection point.
FAN_HYSTERESIS_C   = 3
ACTIVE_PROFILE_PATH = (Path.home() / ".config/greenboost-gaming"
                       / "active_profile.json")
MIN_FAN_PCT        = 0
MAX_FAN_PCT        = 100
GPU_INDEX_ENV      = "GB_FAN_DAEMON_GPU"   # override the default GPU index

# Fallback curve applied when gaming_mode=1 and no explicit profile is active.
# More aggressive than "Normal" (Silent-safe at idle, strong ramp during gaming).
_GAMING_BOOST_CURVE: list[tuple[int, int]] = [
    (35, 30), (50, 40), (65, 58), (75, 72), (82, 88), (88, 100),
]

# sysfs path written by greenboost.ko GB_IOCTL_GAMING_MODE.
_GAMING_MODE_PATH = Path("/sys/module/greenboost/parameters/gaming_mode")

# Path to the privileged NVML fan helper (installed by install.sh).
_NVML_FAN_SCRIPT_CANDIDATES = [
    "/usr/local/lib/greenboost-gaming/gb_gaming/nvml_fan.py",
    "/usr/lib/greenboost-gaming/gb_gaming/nvml_fan.py",
    str(_HERE / "nvml_fan.py"),  # dev fallback
]

def _find_nvml_fan_script() -> str | None:
    for p in _NVML_FAN_SCRIPT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


_df_emit_failed_once = False  # log the failure once per daemon lifetime, not every call


def _df_emit(event: dict) -> None:
    """Best-effort emit into core GreenBoost's shared dataflux log , same
    pattern/path as greenboost_proton/proton's own _df_emit(). Never raises;
    the daemon must never depend on core GreenBoost being importable.

    A bare except:pass here previously discarded every failure silently ,
    same defect fixed in the Proton wrapper's _df_emit 2026-08-07: an import
    failure meant this daemon's fan-curve/profile events vanished with zero
    trace anywhere. This is a long-lived systemd service, so one log line
    for the whole process lifetime is enough , no need to spam every call."""
    global _df_emit_failed_once
    try:
        for _p in (
            "/usr/local/lib/greenboost",
            str(_HERE.parent.parent / "greenboost"),
        ):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import gb_dataflux
        gb_dataflux.emit(event)
    except Exception as e:
        if not _df_emit_failed_once:
            _df_emit_failed_once = True
            log(f"dataflux emit failed ({e}) , telemetry will not be "
                "recorded for this session")

# ── NVML ctypes for temperature reads (no root needed) ──────────────────

_NVML_SUCCESS = 0
_NVML_TEMPERATURE_GPU = 0

def _load_nvml():
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None

_nvml_lib = None
_nvml_device = None

# Minimum GPU utilisation % that triggers a predictive pre-ramp.
# When load spikes above this threshold, the daemon looks up the fan curve
# at (current_temp + PREHEAT_OFFSET_C) so the fan spins up before the GPU
# has had time to actually heat up that far.
_UTIL_PREHEAT_THRESHOLD = 80   # %
_PREHEAT_OFFSET_C       = 5    # °C above current temp used for predictive lookup


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _nvml_init():
    global _nvml_lib, _nvml_device
    _nvml_lib = _load_nvml()
    if _nvml_lib is None:
        return False
    if _nvml_lib.nvmlInit_v2() != _NVML_SUCCESS:
        return False
    dev = ctypes.c_void_p()
    if _nvml_lib.nvmlDeviceGetHandleByIndex(ctypes.c_uint(0), ctypes.byref(dev)) != _NVML_SUCCESS:
        return False
    _nvml_device = dev
    return True


def read_gpu_util_pct() -> int | None:
    """GPU compute utilisation % via NVML.  Returns None when unavailable."""
    global _nvml_lib, _nvml_device
    if _nvml_lib is None or _nvml_device is None:
        return None
    util = _NvmlUtilization()
    fn = getattr(_nvml_lib, "nvmlDeviceGetUtilizationRates", None)
    if fn is None:
        return None
    if fn(_nvml_device, ctypes.byref(util)) == _NVML_SUCCESS:
        return int(util.gpu)
    return None


# ──────────────────────────────────────────────────────────────────────
# Logging , to stderr so journald picks it up automatically
# ──────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[gb-fan-daemon] {msg}", file=sys.stderr, flush=True)


# ──────────────────────────────────────────────────────────────────────
# Linear-interpolate the curve at the current temperature
# ──────────────────────────────────────────────────────────────────────

def interp_fan_pct(curve: list[tuple[int, int]], temp_c: int) -> int:
    """Look up `temp_c` on the curve (sorted ascending by temp).

    Below the first anchor → clamp to that anchor's percent.
    Above the last anchor  → clamp to the last anchor's percent.
    Between two anchors    → linear interpolation."""
    if not curve:
        return MIN_FAN_PCT
    # Make sure the curve is sorted , never trust an external source.
    curve = sorted(curve, key=lambda p: p[0])
    if temp_c <= curve[0][0]:
        return curve[0][1]
    if temp_c >= curve[-1][0]:
        return curve[-1][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t0 <= temp_c <= t1:
            if t1 == t0:
                return p1
            frac = (temp_c - t0) / (t1 - t0)
            return int(round(p0 + frac * (p1 - p0)))
    return curve[-1][1]


# ──────────────────────────────────────────────────────────────────────
# GPU read / write helpers
# ──────────────────────────────────────────────────────────────────────

def _gpu_index() -> int:
    try:
        return int(os.environ.get(GPU_INDEX_ENV, "0"))
    except ValueError:
        return 0


def read_temp_c() -> int | None:
    """GPU temperature via NVML (no subprocess, no root needed).
    Falls back to nvidia-smi when NVML is unavailable."""
    global _nvml_lib, _nvml_device
    if _nvml_lib is None:
        _nvml_init()
    if _nvml_lib is not None and _nvml_device is not None:
        v = ctypes.c_uint(0)
        if _nvml_lib.nvmlDeviceGetTemperature(
                _nvml_device, ctypes.c_uint(_NVML_TEMPERATURE_GPU),
                ctypes.byref(v)) == _NVML_SUCCESS:
            return int(v.value)
    # Fallback: nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", f"--id={_gpu_index()}",
             "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        return int(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, ValueError):
        return None


def _run_nvml_fan_helper(*args: str) -> bool:
    """Run nvml_fan.py via sudo -n (non-interactive, requires sudoers rule)
    or pkexec as fallback.  Returns True on success."""
    script = _find_nvml_fan_script()
    if script is None:
        return False
    last_reason = "no elevation method available"
    for elevate in ("sudo", "pkexec"):
        try:
            result = subprocess.run(
                [elevate, *([] if elevate == "pkexec" else ["-n"]),
                 "python3", script, *args],
                capture_output=True, timeout=5)
            if result.returncode == 0:
                return True
            last_reason = (f"{elevate} exited {result.returncode}: "
                            f"{result.stderr.decode(errors='replace').strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            last_reason = f"{elevate}: {e}"
            continue
    # Previously silent , the caller (write_fan_pct → main loop's
    # "failed to write fan=X%") had no way to tell "no sudoers rule and no
    # polkit prompt answered" apart from "GPU vanished mid-session".
    log(f"nvml_fan.py helper failed via both sudo -n and pkexec ({last_reason})")
    return False


def _is_wayland() -> bool:
    """Return True when running inside a pure Wayland session."""
    xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if xdg in ("wayland", "x11"):
        return xdg == "wayland"
    return "WAYLAND_DISPLAY" in os.environ


def write_fan_pct(pct: int) -> bool:
    """Set fan speed via NVML helper (sudo -n / pkexec).
    Falls back to nvidia-settings on X11 only when the helper is unavailable."""
    pct = max(MIN_FAN_PCT, min(MAX_FAN_PCT, pct))
    if _find_nvml_fan_script() is not None:
        return _run_nvml_fan_helper("set", str(pct))
    # Legacy fallback: nvidia-settings (X11/XWayland only).
    if _is_wayland():
        log("WARNING: NVML fan helper not installed and session is Wayland , "
            "cannot control fan speed. Run: sudo install.sh")
        return False
    global _fan_mode_set
    idx = _gpu_index()
    try:
        if not _fan_mode_set:
            subprocess.check_call(
                ["nvidia-settings", "-a", f"[gpu:{idx}]/GPUFanControlState=1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            _fan_mode_set = True
        subprocess.check_call(
            ["nvidia-settings", "-a", f"[fan:{idx}]/GPUTargetFanSpeed={pct}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return False


_fan_mode_set = False


def restore_auto_fan() -> None:
    """Return fan control to driver-default on graceful exit."""
    if _find_nvml_fan_script() is not None:
        if _run_nvml_fan_helper("auto"):
            log("fan control returned to driver-default (auto) via NVML")
            return
    # Fallback: nvidia-settings (X11 only).
    if _is_wayland():
        log("WARNING: cannot restore fan auto mode on Wayland without NVML helper")
        return
    try:
        subprocess.check_call(
            ["nvidia-settings", "-a",
             f"[gpu:{_gpu_index()}]/GPUFanControlState=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        log("fan control returned to driver-default (auto) via nvidia-settings")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        # Previously silent , the daemon would exit with the fan still
        # pinned at whatever speed the curve last set, and nothing in the
        # journal would say why the restore-to-auto never happened.
        log(f"WARNING: fan auto-restore via nvidia-settings failed ({e}) , "
            "fan may stay pinned at its last curve speed")


# ──────────────────────────────────────────────────────────────────────
# Gaming-mode detection (greenboost.ko sysfs knob)
# ──────────────────────────────────────────────────────────────────────

def _is_gaming_mode() -> bool:
    """Return True when greenboost.ko has gaming_mode == 1."""
    try:
        return _GAMING_MODE_PATH.read_text().strip() == "1"
    except (OSError, ValueError):
        return False


# ──────────────────────────────────────────────────────────────────────
# Active-profile reader
# ──────────────────────────────────────────────────────────────────────

def read_active_profile_name() -> str | None:
    if not ACTIVE_PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(ACTIVE_PROFILE_PATH.read_text())
        name = data.get("name")
        return name if isinstance(name, str) and name else None
    except (json.JSONDecodeError, OSError):
        return None


def load_active_curve() -> list[tuple[int, int]] | None:
    """Return the fan curve of the currently-active profile, or None
    when no profile is active or it has no curve."""
    name = read_active_profile_name()
    if not name:
        return None
    profile = gpu_profile.load_profile(name)
    if profile is None:
        log(f"active profile '{name}' not found on disk , ignoring")
        return None
    if not profile.fan_curve:
        # Empty curve == "leave fan in auto"; the daemon's contract is
        # to do nothing in that case.
        return None
    return [tuple(p) for p in profile.fan_curve]  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────

_keep_running = True


def _on_signal(signum, _frame):
    global _keep_running
    log(f"caught signal {signum}, exiting")
    _keep_running = False


def main() -> int:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    # Initialize NVML for temperature reads.
    if _nvml_init():
        log("NVML initialized , using direct GPU reads (no subprocess)")
    else:
        log("NVML unavailable , falling back to nvidia-smi for temperature reads")

    log(f"starting (poll {POLL_INTERVAL_S}s; active-profile {ACTIVE_PROFILE_PATH}; "
        f"hysteresis {FAN_HYSTERESIS_C}°C)")
    last_curve: list[tuple[int, int]] | None = None
    last_pct_written: int | None = None
    last_curve_check = 0.0
    last_gaming_mode: bool = False
    # Temperature at which we last *increased* the fan speed.
    # Fan decreases are gated on temp dropping FAN_HYSTERESIS_C below this.
    temp_at_ramp: int = 0

    while _keep_running:
        # Refresh the active profile occasionally , cheap, but no need
        # to read JSON every tick.
        now = time.time()
        gaming = _is_gaming_mode()
        if gaming != last_gaming_mode:
            log(f"gaming_mode → {'1 (active)' if gaming else '0 (idle)'}")
            last_gaming_mode = gaming
            # Force re-evaluation of which curve to use.
            last_curve_check = 0.0

        if now - last_curve_check > IDLE_RECHECK_S:
            profile_curve = load_active_curve()
            # When gaming and no explicit profile, use the boost curve.
            if profile_curve is None and gaming:
                new_curve = _GAMING_BOOST_CURVE
                if last_curve != new_curve:
                    log("no active profile , gaming_mode=1; applying gaming boost curve")
            else:
                new_curve = profile_curve
            if new_curve != last_curve:
                if new_curve is None:
                    log("no active fan curve , daemon idle")
                    if last_curve is not None:
                        restore_auto_fan()
                        last_pct_written = None
                        temp_at_ramp = 0
                elif profile_curve is not None:
                    log(f"active fan curve loaded: {new_curve}")
                last_curve = new_curve
            last_curve_check = now

        if last_curve is None:
            time.sleep(IDLE_RECHECK_S)
            continue

        temp = read_temp_c()
        if temp is None:
            log("nvidia-smi unavailable , sleeping")
            time.sleep(POLL_INTERVAL_S * 2)
            continue

        # Predictive pre-ramp: when GPU utilisation spikes above the threshold,
        # look up the curve at (temp + PREHEAT_OFFSET_C) to spin the fan up
        # before heat has propagated to the sensor.  This closes the lag between
        # a workload burst and the thermal response.
        util = read_gpu_util_pct()
        effective_temp = temp
        if util is not None and util >= _UTIL_PREHEAT_THRESHOLD:
            effective_temp = temp + _PREHEAT_OFFSET_C

        raw_target = interp_fan_pct(last_curve, effective_temp)

        # Hysteresis: hold the current fan speed when cooling down until the
        # GPU drops FAN_HYSTERESIS_C degrees below where we last ramped up.
        # This prevents rapid oscillation when temp hovers near a curve anchor.
        if (last_pct_written is not None
                and raw_target < last_pct_written
                and temp > temp_at_ramp - FAN_HYSTERESIS_C):
            target = last_pct_written   # hold , not cool enough to ramp down yet
        else:
            target = raw_target

        # Only push a write if the effective target differs by >= 2 pp.
        if last_pct_written is None or abs(target - last_pct_written) >= 2:
            if write_fan_pct(target):
                if last_pct_written is None or target > last_pct_written:
                    temp_at_ramp = temp     # record ramp-up point for hysteresis
                detail = ""
                if target != raw_target:
                    detail = " (held)"
                elif effective_temp != temp:
                    detail = f" (pre-ramp util={util}%)"
                log(f"temp={temp}°C → fan={target}%{detail}")
                last_pct_written = target
                # Only during an actual game session , a routine idle-desktop
                # fan adjustment isn't gaming telemetry worth correlating
                # against gaming_vram_pressure (proton's own restraint:
                # _check_t2t3_pressure only emits when something's spilling).
                if gaming:
                    _df_emit({
                        "kind": "gaming_fan_curve", "temp_c": temp,
                        "fan_pct": target, "held": target != raw_target,
                    })
            else:
                log(f"failed to write fan={target}%")

        time.sleep(POLL_INTERVAL_S)

    restore_auto_fan()
    return 0


if __name__ == "__main__":
    sys.exit(main())
