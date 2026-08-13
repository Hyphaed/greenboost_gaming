# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.gpu_profile , read + apply GPU clock / power / fan profiles.

The reading path is always safe and never requires root: it shells out to
`nvidia-smi --query-gpu=...` and parses CSV.  The writing path requires
either `nvidia-settings` (X11) or root-level access to /proc/driver/nvidia.

Profiles are stored under ~/.config/greenboost-gaming/profiles/<name>.json
so each user has their own set without needing to escalate privileges
just to save preferences.

Public API:
    list_gpus() -> list[GpuInfo]
    read_gpu(index) -> GpuLive
    list_profiles() -> list[str]
    load_profile(name) -> Profile
    save_profile(name, profile)
    apply_profile(index, profile, *, dry_run=False) -> tuple[bool, list[str]]

No third-party deps.  We avoid `pynvml` so the GUI runs on a minimal
Python.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROFILE_DIR = Path.home() / ".config" / "greenboost-gaming" / "profiles"


_df_emit_failed_once = False  # log the failure once per process, not every call


def _df_emit(event: dict) -> None:
    """Best-effort emit into core GreenBoost's shared dataflux log , same
    pattern/path as greenboost_proton/proton's own _df_emit(). Never raises;
    this module must never depend on core GreenBoost being importable.

    A bare except:pass here previously discarded every failure silently ,
    same defect fixed in the Proton wrapper's _df_emit 2026-08-07: an import
    failure meant every "GPU profile applied" event vanished with zero
    trace anywhere it could be diagnosed from."""
    global _df_emit_failed_once
    try:
        _here = Path(__file__).resolve().parent
        for _p in ("/usr/local/lib/greenboost", str(_here.parent.parent / "greenboost")):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import gb_dataflux
        gb_dataflux.emit(event)
    except Exception as e:
        if not _df_emit_failed_once:
            _df_emit_failed_once = True
            print(f"[gpu_profile] dataflux emit failed ({e}) , telemetry "
                  "will not be recorded", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────

@dataclass
class GpuInfo:
    index: int
    name: str
    uuid: str
    pci_bus: str
    vram_total_mib: int


@dataclass
class GpuLive:
    index: int
    name: str
    temperature_c: int            # current temperature
    power_draw_w: float           # current power draw
    power_limit_w: float          # current power limit (target)
    power_limit_min_w: float
    power_limit_max_w: float
    sm_clock_mhz: int             # current core clock
    mem_clock_mhz: int            # current memory clock
    fan_speed_pct: int            # current fan %
    util_gpu_pct: int
    util_mem_pct: int


@dataclass
class Profile:
    name: str
    power_limit_w: float | None = None    # None = leave alone
    core_offset_mhz: int | None = None    # via nvidia-settings GPUGraphicsClockOffset
    mem_offset_mhz:  int | None = None    # via nvidia-settings GPUMemoryTransferRateOffset
    fan_curve: list[tuple[int, int]] = field(default_factory=list)
    # (temp_c, fan_pct) anchor points, sorted ascending.  An empty list
    # means "leave fan in auto mode".

    def to_json(self) -> str:
        d = asdict(self)
        d["fan_curve"] = [list(t) for t in self.fan_curve]
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, blob: str) -> "Profile":
        d = json.loads(blob)
        return cls(
            name=d.get("name", "unnamed"),
            power_limit_w=d.get("power_limit_w"),
            core_offset_mhz=d.get("core_offset_mhz"),
            mem_offset_mhz=d.get("mem_offset_mhz"),
            fan_curve=[tuple(p) for p in d.get("fan_curve", [])],
        )


# ──────────────────────────────────────────────────────────────────────
# nvidia-smi shell-out (read-side, never destructive)
# ──────────────────────────────────────────────────────────────────────

_NVSMI = shutil.which("nvidia-smi")


def _smi_query(fields: list[str]) -> list[list[str]]:
    """Run `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` and
    return rows of stripped string values.  Returns [] on any failure."""
    if not _NVSMI:
        return []
    try:
        out = subprocess.check_output(
            [_NVSMI,
             "--query-gpu=" + ",".join(fields),
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return []
    rows: list[list[str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        rows.append([cell.strip() for cell in line.split(",")])
    return rows


def list_gpus() -> list[GpuInfo]:
    rows = _smi_query(
        ["index", "name", "uuid", "pci.bus_id", "memory.total"])
    out: list[GpuInfo] = []
    for r in rows:
        if len(r) < 5:
            continue
        try:
            out.append(GpuInfo(
                index=int(r[0]),
                name=r[1],
                uuid=r[2],
                pci_bus=r[3],
                vram_total_mib=int(float(r[4] or 0)),
            ))
        except ValueError:
            continue
    return out


def read_gpu(index: int = 0) -> GpuLive | None:
    rows = _smi_query([
        "index", "name", "temperature.gpu", "power.draw",
        "power.limit", "power.min_limit", "power.max_limit",
        "clocks.current.sm", "clocks.current.memory",
        "fan.speed", "utilization.gpu", "utilization.memory",
    ])
    for r in rows:
        if len(r) < 12:
            continue
        try:
            idx = int(r[0])
        except ValueError:
            continue
        if idx != index:
            continue

        def _f(s: str) -> float:
            try: return float(s)
            except ValueError: return 0.0

        def _i(s: str) -> int:
            try: return int(float(s))
            except ValueError: return 0

        return GpuLive(
            index=idx,
            name=r[1],
            temperature_c=_i(r[2]),
            power_draw_w=_f(r[3]),
            power_limit_w=_f(r[4]),
            power_limit_min_w=_f(r[5]),
            power_limit_max_w=_f(r[6]),
            sm_clock_mhz=_i(r[7]),
            mem_clock_mhz=_i(r[8]),
            fan_speed_pct=_i(r[9]),
            util_gpu_pct=_i(r[10]),
            util_mem_pct=_i(r[11]),
        )
    return None


# ──────────────────────────────────────────────────────────────────────
# Profile persistence
# ──────────────────────────────────────────────────────────────────────

def list_profiles() -> list[str]:
    if not PROFILE_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def load_profile(name: str) -> Profile | None:
    p = PROFILE_DIR / f"{name}.json"
    if not p.exists():
        return None
    try:
        return Profile.from_json(p.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def save_profile(profile: Profile) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (PROFILE_DIR / f"{profile.name}.json").write_text(profile.to_json())


# ──────────────────────────────────────────────────────────────────────
# Active-profile pointer , read by gb_gaming.fan_daemon
# ──────────────────────────────────────────────────────────────────────
#
# The fan daemon (gb_gaming/fan_daemon.py) polls
# ~/.config/greenboost-gaming/active_profile.json for a single key:
#
#   { "name": "<profile-name>" }
#
# Writing this file tells the daemon to start following that profile's
# fan curve.  Deleting it tells the daemon to hand control back to the
# driver.  Keeping both writes in one helper here avoids drift between
# the GUI (Rust → Python bridge) and any future CLI tool.

ACTIVE_PROFILE_PATH = (PROFILE_DIR.parent / "active_profile.json")


def get_active_profile() -> str | None:
    """Return the name of the profile the fan daemon is currently
    tracking, or None if no profile is active."""
    if not ACTIVE_PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(ACTIVE_PROFILE_PATH.read_text())
        name = data.get("name")
        return name if isinstance(name, str) and name else None
    except (OSError, json.JSONDecodeError):
        return None


def set_active_profile(name: str | None) -> None:
    """Tell the fan daemon to follow `name` (or to stop, when None).

    Atomic write via a temp sibling + os.replace , the daemon polls
    this file every 5 s and we don't want it to read a torn JSON."""
    ACTIVE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not name:
        # "Clear" , remove the file rather than writing an empty one,
        # so the daemon's `not exists` branch fires.
        try:
            ACTIVE_PROFILE_PATH.unlink()
        except FileNotFoundError:
            pass
        return
    tmp = ACTIVE_PROFILE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": name}))
    os.replace(str(tmp), str(ACTIVE_PROFILE_PATH))


# ──────────────────────────────────────────────────────────────────────
# Apply , write path; requires nvidia-settings or root
# ──────────────────────────────────────────────────────────────────────

_NV_SETTINGS = shutil.which("nvidia-settings")


def _root_or_nvsettings_ok() -> bool:
    """Apply requires at least one writable path."""
    if os.geteuid() == 0:
        return True
    if _NV_SETTINGS and os.environ.get("DISPLAY"):
        return True
    return False


def apply_profile(index: int, profile: Profile,
                  *, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Apply the profile to GPU `index`.

    Returns (ok, log) where `log` is a list of human-readable steps
    actually taken (or that would have been taken in dry_run mode).
    """
    log: list[str] = []
    if not _NVSMI:
        return False, ["nvidia-smi not on PATH , cannot apply"]
    if not _root_or_nvsettings_ok() and not dry_run:
        return False, ["apply requires root OR a running X session with "
                       "nvidia-settings , try `sudo greenboost-gaming` "
                       "or use Wayland with proper polkit rules"]

    cmds: list[list[str]] = []

    # Power limit , `nvidia-smi -i N -pl W` requires root.
    if profile.power_limit_w is not None:
        cmds.append([_NVSMI, "-i", str(index),
                     "-pl", str(int(profile.power_limit_w))])

    # Clock offsets , `nvidia-settings` is the X-side knob.
    if _NV_SETTINGS and profile.core_offset_mhz is not None:
        cmds.append([_NV_SETTINGS,
                     "-a", f"[gpu:{index}]/GPUGraphicsClockOffsetAllPerformanceLevels="
                            f"{profile.core_offset_mhz}"])
    if _NV_SETTINGS and profile.mem_offset_mhz is not None:
        cmds.append([_NV_SETTINGS,
                     "-a", f"[gpu:{index}]/GPUMemoryTransferRateOffsetAllPerformanceLevels="
                            f"{profile.mem_offset_mhz}"])

    # Fan curve , switch from auto to manual then write the table.
    if _NV_SETTINGS and profile.fan_curve:
        cmds.append([_NV_SETTINGS,
                     "-a", f"[gpu:{index}]/GPUFanControlState=1"])
        # We can't write a full curve via nvidia-settings, only a single
        # set point.  Take the highest anchor point , represents the cap.
        max_pct = max(p for _, p in profile.fan_curve)
        cmds.append([_NV_SETTINGS,
                     "-a", f"[fan:{index}]/GPUTargetFanSpeed={int(max_pct)}"])

    if not cmds:
        return True, ["nothing to apply (profile is empty)"]

    for cmd in cmds:
        log.append(" ".join(cmd))
        if dry_run:
            continue
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=10)
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode(errors='replace').strip()
            # nvidia-settings often exits non-zero with an EMPTY stderr (e.g.
            # no X display, GPU busy) , showing only the decoded text then
            # left the log line blank with no clue what actually happened.
            return False, log + [
                f"FAILED (exit {e.returncode}): {stderr_text or '(no stderr output)'}"]
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, log + [f"FAILED: {e}"]
    if not dry_run:
        _df_emit({
            "kind": "gaming_gpu_profile_applied", "gpu_index": index,
            "power_limit_w": profile.power_limit_w,
            "core_offset_mhz": profile.core_offset_mhz,
            "mem_offset_mhz": profile.mem_offset_mhz,
            "has_fan_curve": bool(profile.fan_curve),
        })
    return True, log


# ──────────────────────────────────────────────────────────────────────
# Convenience for the GUI
# ──────────────────────────────────────────────────────────────────────

def summary() -> dict:
    gpus = list_gpus()
    live = [read_gpu(g.index) for g in gpus]
    return {
        "gpus":     [asdict(g) for g in gpus],
        "live":     [asdict(l) for l in live if l],
        "profiles": list_profiles(),
        "writable": _root_or_nvsettings_ok(),
    }
