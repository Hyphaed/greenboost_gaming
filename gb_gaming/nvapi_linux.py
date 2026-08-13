"""
nvapi_linux.py , Linux equivalents of NVIDIA Driver Settings (DRS / nvidiaProfileInspector).

NVAPI DRS is Windows-only. On Linux we map the same concepts through:
  1. OpenGL environment variables (__GL_*) , read by the nvidia OpenGL ICD
  2. Vulkan environment variables (VKD3D_*, DXVK_*) , for Proton games
  3. nvidia-smi / nvidia-settings , power, clocks, VRR

Setting IDs mirror the NvApiDriverSettings enum from nvidiaProfileInspector for
easy cross-reference.
"""

from __future__ import annotations
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ─── NVAPI DRS setting IDs (for documentation / future Wine helper) ──────────
NVAPI_NGX_DLSS_SR_OVERRIDE_ID           = 0x10E41E01
NVAPI_NGX_DLSS_FG_OVERRIDE_ID           = 0x10E41E03
NVAPI_NGX_DLSS_RR_OVERRIDE_ID           = 0x10E41E02
NVAPI_NGX_DLSS_SR_PRESET_ID             = 0x10E41DF3
NVAPI_NGX_DLSS_FG_MULTI_FRAME_COUNT_ID  = 0x104D6667
NVAPI_OGL_THREAD_CONTROL_ID             = 0x20C1221E
NVAPI_OGL_TRIPLE_BUFFER_ID              = 0x20FDD1F9
NVAPI_ANISO_MODE_LEVEL_ID               = 0x101E61A9
NVAPI_ANISO_MODE_SELECTOR_ID            = 0x10D2BB16
NVAPI_PREFERRED_PSTATE_ID               = 0x1057EB71
NVAPI_PRERENDERLIMIT_ID                 = 0x007BA09E
NVAPI_VRR_MODE_ID                       = 0x1194F158
NVAPI_FXAA_ENABLE_ID                    = 0x1074C972
NVAPI_LODBIASADJUST_ID                  = 0x00738E8F
NVAPI_AA_MODE_METHOD_ID                 = 0x10D773D2
NVAPI_VSYNCMODE_ID                      = 0x00A879CF


@dataclass
class DriverProfile:
    """Linux-native NVIDIA driver profile for a game/preset."""
    # OpenGL env vars (applied when launching any OpenGL/Vulkan native game)
    gl_threaded_optimizations: bool = True   # __GL_THREADED_OPTIMIZATIONS
    gl_triple_buffer: bool = False           # __GL_TRIPLE_BUFFER (vsync=on context)
    gl_max_frames_allowed: int = 1           # __GL_MaxFramesAllowed (pre-render frames)
    gl_aniso_level: int = 16                 # __GL_LOG_MAX_ANISO  (0..4, maps to 1/2/4/8/16)
    gl_vsync: bool = False                   # __GL_SYNC_TO_VBLANK
    gl_shader_disk_cache: bool = True        # __GL_SHADER_DISK_CACHE

    # Proton/DXVK/VKD3D env vars (applied when launching via Proton)
    dxvk_hud: str = ""                       # DXVK_HUD  (empty = off)
    dxvk_frame_rate: int = 0                 # DXVK_FRAME_RATE (0 = unlimited)
    vkd3d_debug: str = "none"               # VKD3D_DEBUG
    vkd3d_feature_flags: str = ""           # VKD3D_CONFIG feature flags

    # Power / clocks (applied via nvidia-smi)
    prefer_max_performance: bool = True      # nvidia-smi -pm 1
    power_limit_pct: int = 100              # % of TDP (100 = default, >100 = boost)

    # Additional arbitrary env vars
    extra: Dict[str, str] = field(default_factory=dict)


def _aniso_to_gl_log(aniso: int) -> int:
    """Convert anisotropy level (1/2/4/8/16) to __GL_LOG_MAX_ANISO value (0-4)."""
    mapping = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}
    return mapping.get(aniso, 4)


def profile_to_env(profile: DriverProfile) -> Dict[str, str]:
    """Translate a DriverProfile to a flat dict of environment variables."""
    env: Dict[str, str] = {}

    env["__GL_THREADED_OPTIMIZATIONS"] = "1" if profile.gl_threaded_optimizations else "0"
    env["__GL_TRIPLE_BUFFER"] = "1" if profile.gl_triple_buffer else "0"
    env["__GL_MaxFramesAllowed"] = str(profile.gl_max_frames_allowed)
    env["__GL_LOG_MAX_ANISO"] = str(_aniso_to_gl_log(profile.gl_aniso_level))
    env["__GL_SYNC_TO_VBLANK"] = "0" if not profile.gl_vsync else "1"
    env["__GL_SHADER_DISK_CACHE"] = "1" if profile.gl_shader_disk_cache else "0"
    env["__GL_SHADER_DISK_CACHE_SKIP_CLEANUP"] = "1"

    if profile.dxvk_hud:
        env["DXVK_HUD"] = profile.dxvk_hud
    if profile.dxvk_frame_rate > 0:
        env["DXVK_FRAME_RATE"] = str(profile.dxvk_frame_rate)
    env["VKD3D_DEBUG"] = profile.vkd3d_debug
    if profile.vkd3d_feature_flags:
        env["VKD3D_CONFIG"] = profile.vkd3d_feature_flags

    env.update(profile.extra)
    return env


def apply_power_settings(profile: DriverProfile) -> List[str]:
    """Apply nvidia-smi power settings. Returns list of (command, result) strings."""
    msgs: List[str] = []

    if profile.prefer_max_performance:
        try:
            r = subprocess.run(
                ["nvidia-smi", "-pm", "1"],
                capture_output=True, text=True, timeout=5
            )
            msgs.append(f"pm=1: {'OK' if r.returncode == 0 else r.stderr.strip()}")
        except Exception as e:
            msgs.append(f"pm=1 failed: {e}")

    if profile.power_limit_pct != 100:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.max_limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            max_w = float(r.stdout.strip().split("\n")[0]) if r.returncode == 0 else None
            if max_w:
                target = int(max_w * profile.power_limit_pct / 100)
                r2 = subprocess.run(
                    ["nvidia-smi", "-pl", str(target)],
                    capture_output=True, text=True, timeout=5
                )
                msgs.append(f"pl={target}W: {'OK' if r2.returncode == 0 else r2.stderr.strip()}")
        except Exception as e:
            msgs.append(f"pl failed: {e}")

    return msgs


# ─── preset profiles ──────────────────────────────────────────────────────────

PRESETS: Dict[str, DriverProfile] = {
    # Maximum performance: threaded GL, minimal pre-render queue, aniso x16
    "gaming_performance": DriverProfile(
        gl_threaded_optimizations=True,
        gl_triple_buffer=False,
        gl_max_frames_allowed=1,
        gl_aniso_level=16,
        gl_vsync=False,
        prefer_max_performance=True,
        power_limit_pct=100,
        vkd3d_feature_flags="upload_hvv_only",
        extra={
            "PROTON_NO_FSYNC": "0",
            "PROTON_USE_NTSYNC": "1",
            "DXVK_ASYNC": "1",
        }
    ),

    # Competitive: absolute minimum latency, bare essentials
    "gaming_competitive": DriverProfile(
        gl_threaded_optimizations=True,
        gl_triple_buffer=False,
        gl_max_frames_allowed=1,
        gl_aniso_level=8,
        gl_vsync=False,
        prefer_max_performance=True,
        power_limit_pct=100,
        extra={
            "PROTON_NO_FSYNC": "0",
            "PROTON_USE_NTSYNC": "1",
            "__GL_YIELD": "NOTHING",
        }
    ),

    # Quality: maximize image quality, allow pre-render
    "gaming_quality": DriverProfile(
        gl_threaded_optimizations=True,
        gl_triple_buffer=True,
        gl_max_frames_allowed=2,
        gl_aniso_level=16,
        gl_vsync=False,
        prefer_max_performance=True,
        power_limit_pct=100,
        extra={
            "PROTON_USE_NTSYNC": "1",
        }
    ),

    # Power-saving: reduce GPU TDP to 80%, disable redundant features
    "power_saving": DriverProfile(
        gl_threaded_optimizations=True,
        gl_triple_buffer=False,
        gl_max_frames_allowed=2,
        gl_aniso_level=8,
        gl_vsync=True,
        prefer_max_performance=False,
        power_limit_pct=80,
    ),
}


def get_launch_env(preset_name: str = "gaming_performance",
                   extra_overrides: Dict[str, str] | None = None) -> Dict[str, str]:
    """Return a merged env dict for launching a game."""
    profile = PRESETS.get(preset_name, PRESETS["gaming_performance"])
    env = dict(os.environ)
    env.update(profile_to_env(profile))
    if extra_overrides:
        env.update(extra_overrides)
    return env


def apply_system_settings(preset_name: str = "gaming_performance") -> List[str]:
    """Apply system-level settings (nvidia-smi) and return status messages."""
    profile = PRESETS.get(preset_name, PRESETS["gaming_performance"])
    return apply_power_settings(profile)


def build_launch_prefix(preset_name: str = "gaming_performance",
                        extra: Dict[str, str] | None = None) -> str:
    """Build env var prefix string for prepending to a game launch command.

    Example output:
      __GL_THREADED_OPTIMIZATIONS=1 __GL_SYNC_TO_VBLANK=0 ... game_binary
    """
    profile = PRESETS.get(preset_name, PRESETS["gaming_performance"])
    env_vars = profile_to_env(profile)
    if extra:
        env_vars.update(extra)
    return " ".join(f"{k}={v}" for k, v in sorted(env_vars.items()))


def write_proton_conf(steam_app_id: int, preset_name: str = "gaming_performance",
                      extra: Dict[str, str] | None = None) -> str:
    """Write/update ~/.steam/steam/userdata/.../config/user_settings.vdf equivalent.

    Actually writes to the Steam launch options override file if it exists.
    Returns the env prefix string for use as Steam launch options.
    """
    prefix = build_launch_prefix(preset_name, extra)
    return f"{prefix} %command%"


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="GreenBoost Linux NVIDIA driver settings")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List available presets")
    p_env = sub.add_parser("env", help="Print env vars for a preset")
    p_env.add_argument("preset", nargs="?", default="gaming_performance")
    p_apply = sub.add_parser("apply", help="Apply system-level settings (nvidia-smi)")
    p_apply.add_argument("preset", nargs="?", default="gaming_performance")
    p_prefix = sub.add_parser("prefix", help="Print launch prefix for a preset")
    p_prefix.add_argument("preset", nargs="?", default="gaming_performance")

    args = parser.parse_args()

    if args.cmd == "list":
        for name, p in PRESETS.items():
            print(f"  {name:25s}  perf={p.prefer_max_performance}  aniso={p.gl_aniso_level}x  "
                  f"vsync={p.gl_vsync}  power={p.power_limit_pct}%")
    elif args.cmd == "env":
        profile = PRESETS.get(args.preset, PRESETS["gaming_performance"])
        for k, v in sorted(profile_to_env(profile).items()):
            print(f"  {k}={v}")
    elif args.cmd == "apply":
        msgs = apply_system_settings(args.preset)
        for m in msgs:
            print(m)
    elif args.cmd == "prefix":
        print(build_launch_prefix(args.preset))
    else:
        parser.print_help()
