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

import hashlib
import mmap
import os
import re
import struct
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



# ── Model trust ──────────────────────────────────────────────────────────

def model_trust(nr_dll: Path) -> tuple[bool, str]:
    """Is this nvngx_dlssnr.dll a build GreenBoost knows is NVIDIA's?

    NVIDIA doesn't publish the file, so the only defensible check is the full
    SHA-256 against a pinned allowlist (dlss_updater.TRUSTED_SHA256). A valid
    Authenticode signature proves nothing here: a widely copied patched build
    carries NVIDIA's signature over different content, and one community
    mirror's copy has the right size but another hash. Advanced users who
    knowingly run another build can set GREENBOOST_ALLOW_UNVERIFIED_NR=1.
    Returns (trusted, human-readable detail)."""
    from gb_gaming import dlss_updater
    try:
        h = hashlib.sha256()
        with open(nr_dll, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return False, f"could not read {nr_dll}: {e}"
    digest = h.hexdigest()
    known = dlss_updater.TRUSTED_SHA256.get("nvngx_dlssnr.dll", {})
    if digest in known:
        return True, f"{known[digest]} (sha256 {digest[:12]}…)"
    if os.environ.get("GREENBOOST_ALLOW_UNVERIFIED_NR") == "1":
        return True, f"unverified build allowed by GREENBOOST_ALLOW_UNVERIFIED_NR (sha256 {digest[:12]}…)"
    return False, (f"sha256 {digest[:12]}… is not a build GreenBoost knows is NVIDIA's "
                   f"(known: {', '.join(d[:12] + '…' for d in known)}); not installed")


# ── Is DLSS 5 plausible in this game? ────────────────────────────────────

_ANTICHEAT_MARKERS = ("EasyAntiCheat", "EasyAntiCheat_EOS", "BattlEye", "BEService.exe",
                      "vgc.exe", "XIGNCODE", "nProtect")


def _game_exe(game_dir: Path) -> Path | None:
    """The biggest .exe that isn't an installer/helper, i.e. the game."""
    skip = re.compile(r"(crash|helper|launcher|setup|install|redist|webhelper|easyanticheat|unins)", re.I)
    best, size = None, 0
    try:
        for p in game_dir.glob("*.exe"):
            if not skip.search(p.name) and p.stat().st_size > size:
                best, size = p, p.stat().st_size
    except OSError:
        return None
    return best


def game_capabilities(game_dir: Path) -> list[dict]:
    """Facts that decide whether DLSS 5 can plausibly work, read from the
    install before anything is changed. Never says "works": the strongest
    claim is "plausible", because only a log from a real launch proves it.

    Counts byte strings in the executable (D3D12 / NGX / Streamline), so a
    game that only mentions them still reads "plausible", and a packed or
    protected binary (Denuvo) can hide all of it, which is reported as
    "can't tell"."""
    out: list[dict] = []
    exe = _game_exe(game_dir)
    if exe is None:
        return [_check("Game executable", False, "warn",
                       f"no game .exe found in {game_dir}, so DLSS 5 can't be pre-checked")]
    try:
        with open(exe, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            head = mm[:4096]
            off = struct.unpack_from("<I", head, 0x3C)[0] if len(head) > 0x40 else 0
            machine = struct.unpack_from("<H", mm[off + 4:off + 6])[0] if off else 0

            def seen(needle: bytes) -> bool:
                return mm.find(needle) != -1
            d3d12 = seen(b"d3d12") or seen(b"D3D12")
            d3d11 = seen(b"d3d11") or seen(b"D3D11")
            vulkan = seen(b"vulkan-1")
            ngx = seen(b"nvngx") or seen(b"NGX")
            streamline = seen(b"sl.interposer") or seen(b"streamline")
    except (OSError, ValueError, struct.error) as e:
        return [_check("Game executable", False, "warn", f"could not read {exe.name}: {e}")]

    out.append(_check("64-bit game", machine == 0x8664, "error",
                      f"{exe.name} is {'x64' if machine == 0x8664 else hex(machine)}"))
    api = "Direct3D 12" if d3d12 else "Direct3D 11" if d3d11 else "Vulkan" if vulkan else "unknown"
    out.append(_check("Renderer", d3d12, "warn",
                      f"{api}. DLSS 5 is proven on Direct3D 12 games; other renderers need extra bridges"
                      if not d3d12 else "Direct3D 12 (strings in the executable, not yet confirmed at run time)"))
    if streamline:
        dlss = "Streamline"
    elif ngx:
        dlss = "NGX (no Streamline)"
    else:
        dlss = "none found"
    out.append(_check("Game's own DLSS", ngx or streamline, "warn",
                      f"{dlss}. The neural pass hooks the game's DLSS session; a game without one needs standalone mode"
                      if not (ngx or streamline) else f"{dlss}. The pass may be able to hook it; only the log after a real launch confirms that"))
    found = [m for m in _ANTICHEAT_MARKERS
             if (game_dir / m).exists() or any(game_dir.glob(f"**/{m}"))][:3] if game_dir.is_dir() else []
    out.append(_check("Anti-cheat", not found, "warn",
                      ("found " + ", ".join(found) + ": test offline first, modified game folders can trigger bans")
                      if found else "no EasyAntiCheat/BattlEye files found. Denuvo and similar can't be detected "
                                    "from outside, so this isn't a guarantee. Test offline first."))
    return out

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


# The runtime's own DlssNr.Enabled=true never proved the pass actually ran ,
# confirmed the hard way, 2026-09-02: a full playtest showed zero
# DlssNr_Dx12::Dispatch calls and zero CreateFeature/EvaluateFeature calls
# for any DLSS feature, meaning DLSS Super Resolution itself had never been
# turned on in the game's own graphics menu that session , the config said
# "on", nothing was actually running. These two lines
# (DlssNrFeature_Dx12.cpp, added the same day) are what the runtime logs
# once, only at the two points that matter , successful NGX feature
# creation, or a skip/failure reason , specifically so the Suite can report
# the truth instead of the config value. See FORK_NOTES.md.
_NR_ON_RE = re.compile(r"GreenBoost DLSS5 Injected: Neural Render ON \(([^)]+)\)")
_NR_OFF_RE = re.compile(r"GreenBoost DLSS5: Neural Render OFF -- (.+)")


def last_session_neural_rendering_status(game_dir: Path) -> dict | None:
    """Reads GreenBoostDLSS5.log for whichever GreenBoost-branded
    confirmation line appears LAST (line order in the log is chronological
    order , a retry-after-failure can flip OFF to ON within one session, so
    "last" is what matters, not "first" or "any"). Returns None when no log
    exists yet, which needs LogToFile actually on (the shipped ini template
    sets this permanently, 2026-09-02) and at least one real launch , never
    raises, never guesses from the config file alone."""
    log_path = game_dir / "GreenBoostDLSS5.log"
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    last_on_detail: str | None = None
    last_off_detail: str | None = None
    last_on_line = -1
    last_off_line = -1
    for i, line in enumerate(text.splitlines()):
        m = _NR_ON_RE.search(line)
        if m:
            last_on_detail = m.group(1)
            last_on_line = i
            continue
        m = _NR_OFF_RE.search(line)
        if m:
            last_off_detail = m.group(1).strip()
            last_off_line = i

    if last_on_line < 0 and last_off_line < 0:
        return {"found": False, "active": None, "detail": ""}
    if last_on_line > last_off_line:
        return {"found": True, "active": True, "detail": last_on_detail or ""}
    return {"found": True, "active": False, "detail": last_off_detail or "unknown reason"}


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

    if nr_dll.is_file():
        trusted, detail = model_trust(nr_dll)
        results.append(_check("nvngx_dlssnr.dll is a known NVIDIA build", trusted,
                              "error" if not trusted else "info", detail))

    runtime_ok, runtime_or_msg = runtime.ensure_dlss5_runtime_available()
    results.append(_check(
        "DLSS5 runtime staged", runtime_ok, "error" if not runtime_ok else "info",
        str(runtime_or_msg)))

    results.extend(game_capabilities(game_dir))

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

    # What actually happened last time you played, not what the config says
    # should happen , see last_session_neural_rendering_status()'s docstring
    # for why the distinction is real and hard-won.
    status = last_session_neural_rendering_status(game_dir)
    if status is None:
        results.append(_check(
            "Last session", False, "warn",
            "no GreenBoostDLSS5.log yet , launch the game at least once to see "
            "whether Neural Rendering actually ran"))
    elif not status["found"]:
        results.append(_check(
            "Last session", False, "warn",
            "a log exists but never mentions Neural Rendering , DLSS 5 may not "
            "have been enabled yet at that point"))
    elif status["active"]:
        results.append(_check(
            "Last session", True, "info",
            f"Neural Rendering actually ran , {status['detail']}"))
    else:
        results.append(_check(
            "Last session", False, "warn",
            f"Neural Rendering did not run , {status['detail']}"))

    return results


# ── Enable / disable ─────────────────────────────────────────────────────

# Per-game "[Section].Key": "value" overrides applied automatically at
# enable time, keyed by Steam AppID , same idea as the Proton wrapper's
# _inject_ue5_rt_disable(), just landing in GreenBoostDLSS5.ini instead of
# the game's own Engine.ini. Unlike CONFIG_KEYS (user-facing DLSS5 panel
# controls the user tunes themselves), this table is for known-broken-game
# compatibility fixes nobody should have to discover by hand.
#
# 2909400 (FINAL FANTASY VII REBIRTH): update 1.004 broke the game's own
# Unreal Engine resource-state reporting for its color target, which the
# upstream OptiScaler project (github.com/optiscaler/OptiScaler, the base
# this runtime forks) documents as causing "Xmas artifacts/dark glitches"
# , https://github.com/optiscaler/OptiScaler/wiki/FINAL-FANTASY-VII-REBIRTH
# , and fixes with Hotfix.ColorResourceBarrier=4
# (D3D12_RESOURCE_STATE_RENDER_TARGET) in place of the runtime's default
# "auto". A second, independent Linux report of the same darkened-image
# symptom on this game exists at
# https://github.com/optiscaler/OptiScaler/issues/764. This is a real
# upstream game/engine bug, not something introduced by GreenBoost's fork ,
# confirmed here by checking a real FF7 Rebirth session's own
# GreenBoostDLSS5.log, which shows Neural Rendering never even ran that
# session (no DLSS Super Resolution was active, only Frame Generation), so
# the darkness predates and is unrelated to Neural Rendering entirely.
GAME_INI_HOTFIXES: dict[str, dict[str, str]] = {
    "2909400": {"Hotfix.ColorResourceBarrier": "4"},
}


def enable_for_game(game_dir: Path, appid: str) -> dict:
    """Installs the DLSS5 runtime into `game_dir` under whichever proxy DLL
    name is free, copies in the user's own nvngx_dlssnr.dll, and turns the
    Neural Rendering pass on directly in the runtime's own ini , no in-game
    overlay step required. `appid` is used only to look up
    GAME_INI_HOTFIXES , the install itself is entirely per-directory,
    unlike the old add-on-patch approach which staged a per-appid symlink
    farm."""
    if not game_dir.is_dir():
        return {"ok": False, "msg": f"game directory not found: {game_dir}"}

    nr_dll = asset_dir() / "nvngx_dlssnr.dll"
    if not nr_dll.is_file():
        return {"ok": False, "msg": f"nvngx_dlssnr.dll not found: {nr_dll}"}
    trusted, detail = model_trust(nr_dll)
    if not trusted:
        return {"ok": False, "msg": f"{nr_dll.name}: {detail}"}

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

    for full_key, value in GAME_INI_HOTFIXES.get(appid, {}).items():
        section, key = full_key.split(".", 1)
        hotfix = runtime.set_ini_key(ini_path, section, key, value)
        if not hotfix.get("ok"):
            return {"ok": False, "msg": f"installed but could not apply {full_key} hotfix: {hotfix.get('msg')}"}

    # Neither Frame Generation ([FrameGen]) nor DLSS Super Resolution's
    # preset ([DLSS]) get forced here , deliberately. Both were tried as
    # automatic defaults on 2026-09-02, both were suspected and reverted when
    # 007 First Light showed severe image corruption (blown highlights, a
    # magenta cast, vertical comb artifacts), and both suspicions turned out
    # to be wrong: the real cause was a build bug in the DLSS 5 runtime
    # itself (spoofArch() in Streamline_Hooks.cpp reporting GPU architecture
    # 0 unconditionally for every Frame Generation capability query, not just
    # when actually needed , see libraries/dlssnr/FORK_NOTES.md for the full
    # diagnosis, including a wrong first fix that crashed the game outright).
    # Found by diff review, fixed, rebuilt on a Windows VM, confirmed clean
    # by a real playtest , no ghosting, no corruption, no crash.
    #
    # That fix is in the runtime binary now, not config, so it doesn't need
    # anything forced here to take effect. Auto-forcing Preset M and Frame
    # Generation together, in this exact combination, against the fixed
    # build specifically, hasn't itself been re-tested , the panel's own
    # controls (Dlss5Panel.tsx) restore the sensible-default seeding
    # (turning either on for the first time picks a working value) as
    # opt-in, one game at a time, rather than reintroducing them as another
    # blanket default without that test. DlssNr.Enabled=true above is what
    # this flow still turns on automatically.

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
