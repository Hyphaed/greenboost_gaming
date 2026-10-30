# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.dlss5 , one-toggle DLSS 5 (Neural Rendering) orchestration.

Rewritten 2026-09-02 around gb_gaming.dlss5_runtime (GreenBoost's own DLSS
Neural Rendering runtime) , see that module's docstring for the full history
and why the previous mechanism (patching a leaked RenoDX "Control" add-on's
process-name check) was replaced rather than extended: it hooked NGX
correctly but could never get Neural Rendering itself to run in any game but
Control, because that add-on locates its depth/motion "guides" by matching
Control's own shader signatures. Confirmed live on 007 First Light,
2026-09-01, stuck indefinitely at "RR: WAITING FOR GUIDES".

This module is still a CONFIGURATOR, not a new placement mechanism in the
sense that matters most: GreenBoost never fetches, bundles, or redistributes
nvngx_dlssnr.dll , NVIDIA's model DLL, supplied by the user in asset_dir()
(see list_assets()). The DLSS5 runtime itself IS bundled with this project
(MIT-licensed, GreenBoost's own build) , see gb_gaming.dlss5_runtime.

This needs no ReShade install and no Streamline DLL swap: the runtime hooks
DXGI/D3D12/NGX itself via its own proxy DLL (chosen by
dlss5_runtime.pick_proxy_dll(), avoiding whatever the game already ships),
and a native-DLSS game's own Streamline runtime is left untouched.

enable_for_game() does the entire flow so the Suite's one toggle is the only
thing a user has to touch , the runtime's own Neural Rendering pass is
turned on directly (dlss5_runtime.set_neural_rendering_enabled()), never
requiring anyone to open its in-game overlay.

Asset layout (see asset_dir()):

    assets/dlss5/
      nvngx_dlssnr.dll   <- NVIDIA's, supplied by the user, never GreenBoost's

Everything else (GreenBoostDLSS5.dll, its ini, its forwarder DLL) ships with
this project at libraries/dlssnr/runtime/ and is copied per-game from there
, never staged under assets/dlss5/.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from gb_gaming import dlss5_runtime as runtime

# ── Asset location ──────────────────────────────────────────────────────

_ASSET_DIR_OVERRIDE_PATH = Path(os.path.expanduser(
    "~/.config/greenboost-gaming/dlss5_asset_dir.txt"))


def get_asset_dir_override() -> str:
    """The folder path the user picked via the "Select DLSS5 folder" button
    in the Suite, or "" if they never picked one , in which case asset_dir()
    falls through to its other defaults. Never raises."""
    try:
        return _ASSET_DIR_OVERRIDE_PATH.read_text().strip()
    except OSError:
        return ""


def set_asset_dir_override(path: str) -> dict:
    """Persists the folder the user picked as the one asset_dir() should use
    from now on. An empty path clears the override, going back to the
    $GB_DLSS5_DIR / repo / installed-mirror chain."""
    path = path.strip()
    if path and not Path(path).is_dir():
        return {"ok": False, "msg": f"not a directory: {path}"}
    try:
        _ASSET_DIR_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if path:
            _ASSET_DIR_OVERRIDE_PATH.write_text(path)
        else:
            _ASSET_DIR_OVERRIDE_PATH.unlink(missing_ok=True)
    except OSError as e:
        return {"ok": False, "msg": f"could not save: {e}"}
    return {"ok": True, "msg": f"DLSS5 folder set to {path}" if path else "DLSS5 folder override cleared"}


def asset_dir() -> Path:
    """The user's own "Select DLSS5 folder" pick (set_asset_dir_override())
    -> $GB_DLSS5_DIR (explicit env override) -> the repo's own assets/dlss5/
    (dev tree, same CARGO_MANIFEST_DIR-relative idea as the gb_gaming dev
    fallback documented in CLAUDE.md) -> the installed mirror install.sh
    maintains at ~/.local/share/greenboost/dlss5/. Never creates anything ,
    callers check what they need and report clearly when it's missing."""
    picked = get_asset_dir_override()
    if picked:
        return Path(picked)
    env = os.environ.get("GB_DLSS5_DIR")
    if env:
        return Path(os.path.expanduser(env))
    dev = Path(__file__).resolve().parent.parent / "assets" / "dlss5"
    if dev.is_dir():
        return dev
    return Path(os.path.expanduser("~/.local/share/greenboost/dlss5"))


REQUIRED_ASSET_NAMES = ("nvngx_dlssnr.dll",)


def list_assets() -> dict:
    """The one file this feature needs the user to supply, and whether it's
    actually present in asset_dir() right now. Drives the "files needed"
    modal shown before enabling."""
    assets = asset_dir()
    files = [{
        "name": name,
        "present": (assets / name).is_file(),
        "required": True,
    } for name in REQUIRED_ASSET_NAMES]
    return {"asset_dir": str(assets), "files": files}


# ── Preflight ────────────────────────────────────────────────────────────

_RTX50_RE = re.compile(r"RTX\s*50\d{2}", re.IGNORECASE)


def _nvidia_smi_gpu_driver() -> tuple[str, str] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def _check(check: str, ok: bool, level: str, detail: str) -> dict:
    return {"check": check, "ok": ok, "level": level, "detail": detail}


def preflight(game_dir: Path, appid: str) -> list[dict]:
    """Real, measured checks , never a synthetic pass/fail. Every row
    reports what was actually found, not just true/false, so the UI can show
    the real driver string, the real GPU name, the real file count."""
    del appid
    results: list[dict] = []

    gpu = _nvidia_smi_gpu_driver()
    if gpu is None:
        results.append(_check("GPU / driver", False, "error", "nvidia-smi did not respond"))
    else:
        name, driver = gpu
        is_rtx50 = bool(_RTX50_RE.search(name))
        results.append(_check(
            "GPU is RTX 50-series", is_rtx50, "error" if not is_rtx50 else "info",
            f"detected {name!r} , the Neural Rendering pass does not run on "
            f"anything older (driver {driver!r})"))

    assets = asset_dir()
    nr_dll = assets / "nvngx_dlssnr.dll"
    results.append(_check(
        "nvngx_dlssnr.dll staged", nr_dll.is_file(), "error",
        str(nr_dll) if nr_dll.is_file() else f"missing: {nr_dll}"))

    runtime_ok, runtime_or_msg = runtime.ensure_dlss5_runtime_available()
    results.append(_check(
        "DLSS5 runtime staged", runtime_ok, "error" if not runtime_ok else "info",
        str(runtime_or_msg)))

    proxy = runtime.pick_proxy_dll(game_dir)
    results.append(_check(
        "A DLL proxy slot is free", proxy is not None, "error",
        f"will install as {proxy}" if proxy else
        "every candidate name (" + ", ".join(runtime.PROXY_CANDIDATES) +
        ") already exists in this game's directory"))

    # A game enabled before 2026-09-02 is still running the old ~200 MB
    # vendor-agnostic runtime , OptiScaler.ini, not GreenBoostDLSS5.ini , and
    # migration only runs the next time this game's DLSS 5 gets (re-)enabled
    # (see runtime.has_pending_legacy_install()'s docstring). Surfaced here,
    # not just left to fail silently: every slider/toggle in the config panel
    # writes to GreenBoostDLSS5.ini, which doesn't exist yet for a game in
    # this state, so a setting change looks like it took (the panel updates
    # optimistically) and then quietly reverts, with no explanation , that
    # includes the anti-ghosting DLSS preset and Frame Generation defaults
    # this flow now sets automatically on enable.
    if runtime.has_pending_legacy_install(game_dir):
        results.append(_check(
            "Runtime is up to date", False, "warn",
            "an older DLSS 5 install (OptiScaler.ini) is still on disk for this game , "
            "none of the panel's settings apply until you turn DLSS 5 off and back on "
            "here to upgrade it"))

    return results


# ── Enable / disable ─────────────────────────────────────────────────────

def enable_for_game(game_dir: Path, appid: str) -> dict:
    """Installs the DLSS5 runtime into `game_dir` under whichever proxy DLL
    name is free, copies in the user's own nvngx_dlssnr.dll, and turns the
    Neural Rendering pass on directly in the runtime's own ini , no in-game
    overlay step required. `appid` is accepted for interface symmetry with
    the rest of the DLSS5 command surface but isn't needed by this mechanism
    (the install is entirely per-directory, unlike the old add-on-patch
    approach which staged a per-appid symlink farm)."""
    del appid
    if not game_dir.is_dir():
        return {"ok": False, "msg": f"game directory not found: {game_dir}"}

    nr_dll = asset_dir() / "nvngx_dlssnr.dll"
    if not nr_dll.is_file():
        return {"ok": False, "msg": f"nvngx_dlssnr.dll not found: {nr_dll}"}

    runtime_ok, runtime_or_msg = runtime.ensure_dlss5_runtime_available()
    if not runtime_ok:
        return {"ok": False, "msg": str(runtime_or_msg)}
    runtime_dir = runtime_or_msg
    assert isinstance(runtime_dir, Path)

    proxy = runtime.pick_proxy_dll(game_dir)
    if proxy is None:
        return {"ok": False, "msg": (
            "no free DLL proxy slot , every candidate name ("
            + ", ".join(runtime.PROXY_CANDIDATES) + ") already exists here")}

    result = runtime.install_dlss5_runtime_into_game(runtime_dir, game_dir, proxy, nr_dll)
    if not result.get("ok"):
        return result

    ini_path = game_dir / "GreenBoostDLSS5.ini"
    seed = runtime.set_neural_rendering_enabled(ini_path, True)
    if not seed.get("ok"):
        return {"ok": False, "msg": f"installed but could not enable the pass: {seed.get('msg')}"}

    # Neither Frame Generation ([FrameGen]) nor DLSS Super Resolution's
    # preset ([DLSS]) get touched here. Both were tried as automatic
    # defaults on 2026-09-02 and both were reverted after real testing on
    # 007 First Light:
    #
    # 1. Forcing DLSS.RenderPresetForAll=13 (Preset M, the transformer
    #    model) alongside Neural Rendering produced severe image corruption
    #    (blown highlights, a magenta cast, vertical comb artifacts) , not
    #    a ghosting fix. Reverted first.
    # 2. That alone didn't fix it. What was still forced at that point:
    #    FrameGen.Enabled=true / FGInput=dlssg / FGOutput=dlssg. The
    #    corruption persisted ("now shows pink all around"). DLSS Frame
    #    Generation causing pink/magenta corruption is independently
    #    documented across unrelated games and engines (e.g. a Microsoft
    #    Flight Simulator forum report of "pink flicking" with DLSS FG on),
    #    and the user's *original* report , before either default existed ,
    #    was "Neural Rendering is working but has some ghosting issues": a
    #    much milder complaint than full corruption. That timeline points
    #    at Frame Generation, forced alongside an already-unofficial,
    #    reverse-engineered Neural Rendering pass, as the actual cause.
    #
    # Neither combination has been confirmed safe on this hardware/game, so
    # neither gets forced again. DlssNr.Enabled=true above is the one thing
    # this flow still turns on automatically , that's the feature that was
    # actually confirmed working (with ghosting, not corruption) before any
    # of this. Frame Generation and the preset override both remain
    # available as opt-in controls in the panel, each carrying a warning
    # about what happened here , see Dlss5Panel.tsx.

    # The runtime's own startup splash is silenced unconditionally , this
    # is GreenBoost's feature now, not something that should announce a
    # separate tool on screen every launch.
    runtime.disable_startup_splash(ini_path)

    return {
        "ok": True,
        "msg": f"{result['msg']}; Neural Rendering enabled",
        "proxy_dll": proxy,
        "files": result.get("files", []),
    }


def disable_for_game(game_dir: Path, appid: str) -> dict:
    """Fully removes what enable_for_game() installed , unlike the previous
    mechanism's staging-directory teardown, this module installs directly
    into `game_dir`, so there is no separate "Restore" step: one toggle
    installs, the same toggle off removes it via
    dlss5_runtime.uninstall_dlss5_runtime_from_game()'s manifest."""
    del appid
    return runtime.uninstall_dlss5_runtime_from_game(game_dir)


# ── Neural Rendering config (get/set) ────────────────────────────────────

# Keys the Suite's panel exposes, addressed as "Section.Key" and mapped
# straight onto the runtime's own ini , see libraries/dlssnr/runtime's
# GreenBoostDLSS5.ini for the authoritative comments on what each one does
# and which need a restart. "auto" is a real value here (the runtime's own
# built-in default), never coerced into a number.
CONFIG_KEYS = (
    "DLSS.RenderPresetOverride", "DLSS.RenderPresetForAll",
    "DlssNr.Enabled", "DlssNr.Preset", "DlssNr.Style", "DlssNr.Intensity",
    "DlssNr.LocalStructure", "DlssNr.LocalTone", "DlssNr.SkinStructure",
    "DlssNr.TransferStrength", "DlssNr.ColourStrength", "DlssNr.WorkingScale",
    "DlssNr.MaxRatio", "DlssNr.WhitePointScale", "DlssNr.AutoMask",
    "DlssNr.AutoCapture", "DlssNr.DebugView",
    "FrameGen.Enabled", "FrameGen.FGInput", "FrameGen.FGOutput",
    "Sharpness.OverrideSharpness", "Sharpness.Sharpness", "Sharpness.Shader",
    "OutputScaling.Enabled", "OutputScaling.Multiplier", "OutputScaling.Downscaler",
    "Framerate.FramerateLimit",
)


def read_config(game_dir: Path) -> dict[str, str]:
    """Reads every key in CONFIG_KEYS from `game_dir/GreenBoostDLSS5.ini`.
    A key whose section or entry is missing from the file is simply absent
    from the returned dict , the caller (the Suite's panel) treats an
    absent key the same as "auto"."""
    ini_path = game_dir / "GreenBoostDLSS5.ini"
    sections: dict[str, dict[str, str]] = {}
    out: dict[str, str] = {}
    for full_key in CONFIG_KEYS:
        section, key = full_key.split(".", 1)
        if section not in sections:
            sections[section] = runtime.read_ini_section(ini_path, section)
        if key in sections[section]:
            out[full_key] = sections[section][key]
    return out


def write_config(game_dir: Path, changes: dict[str, str]) -> dict:
    """Writes each "Section.Key": "value" pair in `changes` into
    `game_dir/GreenBoostDLSS5.ini`. Rejects any key not in CONFIG_KEYS
    outright rather than writing an arbitrary ini entry on the panel's
    behalf. Stops at the first failure and reports which key it was on ,
    a partial write here would leave the ini in a state the panel didn't
    ask for and can't explain."""
    ini_path = game_dir / "GreenBoostDLSS5.ini"
    if not ini_path.is_file():
        return {"ok": False, "msg": f"GreenBoostDLSS5.ini not found: {ini_path}"}
    for full_key, value in changes.items():
        if full_key not in CONFIG_KEYS:
            return {"ok": False, "msg": f"unknown DLSS5 config key: {full_key}"}
        section, key = full_key.split(".", 1)
        result = runtime.set_ini_key(ini_path, section, key, value)
        if not result.get("ok"):
            return {"ok": False, "msg": f"{full_key}: {result.get('msg')}"}
    return {"ok": True, "msg": f"{len(changes)} setting(s) written"}
