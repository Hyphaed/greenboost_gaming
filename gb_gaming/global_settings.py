# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.global_settings , system-wide defaults that the Proton wrapper
exports as environment variables at game-launch time.

This module is the single source of truth for the **Global Settings**
tab in the GUI.  The shape mirrors what NVIDIA's app calls "Global
Settings" (one row per knob, applied to every game by default).  Each
field maps to a specific environment variable consumed by:

  • DXVK-NVAPI  → DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE,
                  DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION
  • Proton      → PROTON_DLSS_INDICATOR, PROTON_DLSS_UPGRADE,
                  PROTON_ENABLE_WAYLAND
  • Vulkan WSI  → ENABLE_HDR_WSI

Why a JSON config instead of plain env vars?
  - The GUI persists the user's choice and we need it on every game
    launch , even when the GUI isn't running.
  - The Proton wrapper reads this file before invoking upstream Proton
    so the env vars get exported even for Steam-launched games where
    the user never opens our app at session start.

Layout: ~/.config/greenboost-gaming/global_settings.json

Smart defaults:
  - Wayland/HDR/preset are auto-detected on first read and the JSON is
    written with those defaults; the user can override anything by
    flipping toggles in the Global Settings tab.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path


CONFIG_DIR  = Path.home() / ".config" / "greenboost-gaming"
CONFIG_PATH = CONFIG_DIR / "global_settings.json"


# ──────────────────────────────────────────────────────────────────────
# DLSS preset table (NVIDIA's render-preset values + our extras)
# ──────────────────────────────────────────────────────────────────────

# The NVIDIA presets the DXVK-NVAPI env var accepts.
#   render_preset_k        → recommended for RTX 20/30 (fast, stable)
#   render_preset_m        → recommended for RTX 40/50 (transformer)
#   render_preset_l        → max sharpness, more artifacts
#   render_preset_latest   → NVIDIA's current recommendation
#   default                → let the game decide
#   off                    → no override (env var unset)
DLSS_PRESETS = [
    ("auto",                "Auto (recommended for your GPU)"),
    ("default",             "Default (game decides)"),
    ("off",                 "Off (no override)"),
    ("render_preset_k",     "Preset K , best on RTX 20 / 30"),
    ("render_preset_m",     "Preset M , best on RTX 40 / 50"),
    ("render_preset_l",     "Preset L , maximum sharpness (more artifacts)"),
    ("render_preset_latest","Latest / Recommended (NVIDIA's current pick)"),
]


# ──────────────────────────────────────────────────────────────────────
# Dataclass
# ──────────────────────────────────────────────────────────────────────

@dataclass
class GlobalSettings:
    # DLSS-related
    dlss_preset:      str  = "auto"           # one of DLSS_PRESETS' ids
    dlss_indicator:   bool = False            # PROTON_DLSS_INDICATOR=1
    dlss_upgrade:     bool = True             # PROTON_DLSS_UPGRADE=1
    # System overrides
    wayland:          bool = False            # PROTON_ENABLE_WAYLAND=1
    hdr:              bool = False            # ENABLE_HDR_WSI=1
    # PR-XXX: cinema mode , disable secondary displays when launching a
    # game so the primary monitor gets full-screen focus.  The Suite's
    # launch_game command honours this when set.  User restores
    # secondaries manually from the Displays panel afterwards.
    auto_disable_secondary_on_launch: bool = False
    # Launch Steam minimised to tray (`steam -silent`) when the Suite starts a
    # game and Steam is not already running, and launch via `steam -applaunch`
    # rather than the steam:// URL handler, whose desktop activation is what
    # raises the Steam window over the game. Read by the Tauri backend, not by
    # the Proton wrapper. Steam has no option to hide its tray ICON , that
    # stays regardless.
    steam_silent_launch: bool = True
    # Explicit upstream Proton for the wrapper to build on , the `proton`
    # script or the directory holding it. Empty means auto-detect. Exists
    # because a user with only a distro Proton build (Proton-CachyOS, reported
    # 2026-08-20) previously had to hand-edit the wrapper to launch anything.
    proton_upstream:  str  = ""         # GREENBOOST_PROTON_UPSTREAM

    # ── PR-GGGG: GreenBoost runtime feature toggles ────────────────────
    # These flip GREENBOOST_* env vars consumed by the Proton wrapper +
    # the Vulkan layer.  Defaults mirror the proton-script behaviour so
    # the JSON file stays predictable even when fields are missing.
    nis_enable:              bool = False   # GREENBOOST_NIS , build NIS pipeline
    nis_dispatch:            bool = False   # GREENBOOST_NIS_DISPATCH , actually run sharpen
    gplasync:                bool = True    # GREENBOOST_GPLASYNC , overlay dxvk-gplasync DLLs
    perf_lock:               bool = True    # GREENBOOST_PERF_LOCK , CPU governor + GPU clock lock
    compositor_suspend:      bool = True    # GREENBOOST_COMPOSITOR_SUSPEND , KWin/GNOME pause
    ddr_prewarm:             bool = True    # GREENBOOST_DDR_PREWARM , touch T2 pages
    memlock_unlimited:       bool = True    # GREENBOOST_MEMLOCK_UNLIMITED , RLIMIT_MEMLOCK=∞
    vk_pipeline_cache:       bool = True    # GREENBOOST_VK_PIPELINE_CACHE , persistent VkPipelineCache
    vk_queue_priority:       bool = True    # GREENBOOST_VK_QUEUE_PRIORITY , HIGH queue priority
    vk_memory_priority:      bool = True    # GREENBOOST_VK_MEMORY_PRIORITY , T1/T2/T3 priority hints

    # ── C3/C4: Frame-pacing, Reflex, NIS tuning, advanced ────────────────
    nis_sharpness:    float = 0.5   # GREENBOOST_NIS_SHARPNESS (0..1)
    nis_scale:        float = 1.0   # GREENBOOST_NIS_SCALE (0.5..1.0; 1.0 = sharpen-only)
    reflex_enable:    bool  = False  # GREENBOOST_REFLEX
    fps_cap:          int   = 0      # DXVK_FRAME_RATE (0 = disabled)
    # Always on; not user-visible. See global_settings.rs for why.
    stream_priority:  bool  = True   # GREENBOOST_STREAM_PRIORITY (CUDA high-prio streams)
    vk_debug:         bool  = False  # GREENBOOST_VK_DEBUG
    vk_overflow_min_mb: int = 32     # GREENBOOST_VK_OVERFLOW_MIN_MB
    vk_t3_min_mb:     int  = 0      # GREENBOOST_VK_T3_MIN_MB (0 = no minimum)
    log_ttl_days:     int  = 14     # GREENBOOST_LOG_TTL_DAYS
    shader_threads:   int  = 0      # GREENBOOST_SHADER_THREADS (0 = auto / nproc-2)
    shader_cache_gb:  int  = 8      # GREENBOOST_SHADER_CACHE_GB
    gplasync_version: str  = "current"  # GREENBOOST_GPLASYNC_VERSION
    nvapi_hud:        bool = False       # GREENBOOST_NVAPI_HUD=1 (DXVK's own HUD only ,
                                          # does nothing for DX12/vkd3d-proton games)
    mangohud_enabled: bool = False       # GREENBOOST_MANGOHUD_DEFAULT=1 , works for any
                                          # translation layer, unlike nvapi_hud above
    vkd3d_config:     str  = ""         # VKD3D_CONFIG=<flags>
    affinity_mode:    str  = "all"      # GREENBOOST_AFFINITY , "all" | "pcores" | "numa".
                                          # "all" (default): every logical CPU stays
                                          # schedulable , confirmed live 2026-08-07 that a
                                          # 150+-thread UE4 title left 16 of 32 threads idle
                                          # under the previous hard-coded P-cores-only pin.
                                          # "pcores"/"numa" remain selectable per-game for
                                          # titles that measurably prefer less scheduler
                                          # jitter on the render thread.

    # ── OpenGL LD_PRELOAD layer ──────────────────────────────────────────
    gl_layer_enabled:   bool = True  # GREENBOOST_OPENGL=1 (injected by proton wrapper)
    gl_overflow_min_mb: int  = 32    # GREENBOOST_GL_OVERFLOW_MIN_MB (T2 routing threshold)

    # Detected info (read-only fields)
    detected_session: str  = ""               # "wayland" | "x11" | ""
    detected_gpu:     str  = ""               # e.g. "NVIDIA GeForce RTX 5070"
    detected_series:  str  = ""               # "RTX 50" | "RTX 40" | …
    recommended_preset: str = "render_preset_m"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# ──────────────────────────────────────────────────────────────────────
# Auto-detection helpers
# ──────────────────────────────────────────────────────────────────────

def detect_session_type() -> str:
    """Return 'wayland', 'x11', or '' , based on XDG_SESSION_TYPE and
    WAYLAND_DISPLAY environment variables."""
    s = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if s in ("wayland", "x11"):
        return s
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return ""


def detect_gpu() -> tuple[str, str]:
    """Return (full_name, series_label).

    Series labels: "RTX 50", "RTX 40", "RTX 30", "RTX 20", "GTX 16",
    "GTX 10", "" for unrecognised.  Parsed from `nvidia-smi --query-gpu
    =name`; falls back to /proc/driver/nvidia/gpus/*/information.
    """
    name = ""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        name = out.splitlines()[0] if out else ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        name = ""

    series = ""
    m = re.search(r"\bRTX\s+(\d{2})\d{2}", name, re.I)
    if m:
        series = f"RTX {m.group(1)}"
    else:
        m = re.search(r"\bGTX\s+(\d{2,4})", name, re.I)
        if m:
            n = m.group(1)
            series = f"GTX {n[:2]}" if len(n) >= 3 else f"GTX {n}"
    return name, series


def recommended_preset_for(series: str) -> str:
    """Pick the best DLSS preset based on the GPU series.

    RTX 20/30: render_preset_k (better FPS, fewer artifacts on Turing/Ampere)
    RTX 40/50: render_preset_m (transformer-friendly hardware)
    Anything else (GTX, unknown): render_preset_latest (NVIDIA's pick)
    """
    if series in ("RTX 20", "RTX 30"):
        return "render_preset_k"
    if series in ("RTX 40", "RTX 50"):
        return "render_preset_m"
    return "render_preset_latest"


def detect_hdr_capable_display() -> bool:
    """True if any connected display reports HDR support.

    We check xrandr output for HDR-related properties (`HDR`, `Colorspace`).
    On Wayland this returns False , wlr-randr and similar don't expose
    HDR via this interface.  Conservative default: only set HDR when
    we're confident the display supports it.
    """
    if not shutil.which("xrandr"):
        return False
    try:
        out = subprocess.check_output(
            ["xrandr", "--props"],
            stderr=subprocess.DEVNULL, timeout=3).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return False
    # Indicators that an output is HDR-capable.
    return any(t in out for t in
               ("Colorspace: BT2020_RGB",
                "HDR_OUTPUT_METADATA",
                "max_bpc"))


# ──────────────────────────────────────────────────────────────────────
# Load / save
# ──────────────────────────────────────────────────────────────────────

def _fill_detected(s: GlobalSettings) -> GlobalSettings:
    """Refresh the read-only `detected_*` fields without touching the
    user-controlled ones."""
    s.detected_session = detect_session_type()
    name, series = detect_gpu()
    s.detected_gpu     = name
    s.detected_series  = series
    s.recommended_preset = recommended_preset_for(series)
    return s


def load() -> GlobalSettings:
    """Read settings.  First-call behaviour: populate sensible defaults
    by auto-detecting Wayland / HDR / GPU and persist them so the
    config file exists for the Proton wrapper to read."""
    if not CONFIG_PATH.exists():
        s = GlobalSettings()
        # Smart defaults , only on first ever load.
        _fill_detected(s)
        s.wayland = (s.detected_session == "wayland")
        s.hdr     = detect_hdr_capable_display()
        save(s)
        return s

    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return _fill_detected(GlobalSettings())

    # Only accept fields we know about , defends against forwards-
    # incompatible config files written by a future Suite version.
    known = {f.name for f in fields(GlobalSettings)}
    s = GlobalSettings(**{k: v for k, v in data.items() if k in known})
    # Re-detect on every load so the read-only fields stay accurate
    # if the user swapped GPU / session type since last save.
    return _fill_detected(s)


def save(s: GlobalSettings) -> None:
    """Atomic write."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(s.to_json())
    os.replace(str(tmp), str(CONFIG_PATH))


# ──────────────────────────────────────────────────────────────────────
# Env-var generation , used by greenboost_proton/proton at launch
# ──────────────────────────────────────────────────────────────────────

def as_env_dict(s: GlobalSettings | None = None) -> dict[str, str]:
    """Translate settings into the env vars Proton + DXVK-NVAPI + WSI
    consume.  Pass the result to subprocess.run(..., env={**os.environ,
    **as_env_dict()}) or write to a sourceable .sh file."""
    if s is None:
        s = load()
    out: dict[str, str] = {}

    # DLSS preset override.
    chosen = s.dlss_preset
    if chosen == "auto":
        chosen = s.recommended_preset
    if chosen and chosen != "off":
        out["DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE"] = "on"
        if chosen != "default":
            out["DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION"] = chosen

    if s.dlss_indicator: out["PROTON_DLSS_INDICATOR"] = "1"
    if s.dlss_upgrade:   out["PROTON_DLSS_UPGRADE"]   = "1"
    if s.wayland:        out["PROTON_ENABLE_WAYLAND"] = "1"
    if s.hdr:            out["ENABLE_HDR_WSI"]        = "1"

    # ── PR-GGGG: GreenBoost runtime toggles ─────────────────────────────
    # Always set so the wrapper's setdefault() sees an authoritative value
    # , the user toggling something OFF must actually disable it, not just
    # leave it unset (which would inherit the wrapper's own default).
    out["GREENBOOST_NIS"]                = "1" if s.nis_enable          else "0"
    out["GREENBOOST_NIS_DISPATCH"]       = "1" if s.nis_dispatch        else "0"
    out["GREENBOOST_GPLASYNC"]           = "1" if s.gplasync            else "0"
    out["GREENBOOST_PERF_LOCK"]          = "1" if s.perf_lock           else "0"
    out["GREENBOOST_COMPOSITOR_SUSPEND"] = "1" if s.compositor_suspend  else "0"
    out["GREENBOOST_DDR_PREWARM"]        = "1" if s.ddr_prewarm         else "0"
    out["GREENBOOST_MEMLOCK_UNLIMITED"]  = "1" if s.memlock_unlimited   else "0"
    out["GREENBOOST_VK_PIPELINE_CACHE"]  = "1" if s.vk_pipeline_cache   else "0"
    out["GREENBOOST_VK_QUEUE_PRIORITY"]  = "1" if s.vk_queue_priority   else "0"
    out["GREENBOOST_VK_MEMORY_PRIORITY"] = "1" if s.vk_memory_priority  else "0"

    # ── C3/C4: Frame-pacing, Reflex, NIS tuning, advanced ────────────────
    out["GREENBOOST_NIS_SHARPNESS"]     = str(s.nis_sharpness)
    out["GREENBOOST_NIS_SCALE"]         = str(s.nis_scale)
    out["GREENBOOST_REFLEX"]            = "1" if s.reflex_enable   else "0"
    out["GREENBOOST_STREAM_PRIORITY"]   = "1" if s.stream_priority else "0"
    out["GREENBOOST_VK_DEBUG"]          = "1" if s.vk_debug        else "0"
    out["GREENBOOST_VK_OVERFLOW_MIN_MB"] = str(s.vk_overflow_min_mb)
    out["GREENBOOST_LOG_TTL_DAYS"]      = str(s.log_ttl_days)
    out["GREENBOOST_SHADER_CACHE_GB"]   = str(s.shader_cache_gb)
    if s.fps_cap > 0:
        out["DXVK_FRAME_RATE"]              = str(s.fps_cap)
    if s.vk_t3_min_mb > 0:
        out["GREENBOOST_VK_T3_MIN_MB"]      = str(s.vk_t3_min_mb)
    if s.shader_threads > 0:
        out["GREENBOOST_SHADER_THREADS"]    = str(s.shader_threads)
    if s.gplasync_version and s.gplasync_version != "current":
        out["GREENBOOST_GPLASYNC_VERSION"]  = s.gplasync_version
    if s.nvapi_hud:
        out["GREENBOOST_NVAPI_HUD"]         = "1"
    out["GREENBOOST_MANGOHUD_DEFAULT"]      = "1" if s.mangohud_enabled else "0"
    if s.vkd3d_config:
        out["VKD3D_CONFIG"]                 = s.vkd3d_config
    out["GREENBOOST_AFFINITY"]              = s.affinity_mode or "all"

    if s.proton_upstream:
        out["GREENBOOST_PROTON_UPSTREAM"]   = s.proton_upstream

    # OpenGL layer , proton wrapper reads GREENBOOST_OPENGL to enable/disable
    # injection of libgb_gl.so, and the layer reads GREENBOOST_GL_OVERFLOW_MIN_MB
    # to set its T2 routing threshold.
    out["GREENBOOST_OPENGL"]           = "1" if s.gl_layer_enabled else "0"
    out["GREENBOOST_GL_OVERFLOW_MIN_MB"] = str(s.gl_overflow_min_mb)
    return out


def as_shell_export(s: GlobalSettings | None = None) -> str:
    """Bash-sourceable export block.  The Proton wrapper reads this
    instead of re-implementing the env-var translation in shell."""
    lines = ["# generated by gb_gaming.global_settings , do not edit"]
    for k, v in as_env_dict(s).items():
        # Properly shell-quote the value just in case (these are all
        # safe known strings today, but cheap insurance).
        lines.append(f"export {k}={json.dumps(v)}")
    return "\n".join(lines) + "\n"
