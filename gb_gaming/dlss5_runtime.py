# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Installs GreenBoostDLSS5 , GreenBoost's own NVIDIA-only, menu-free DLSS
Neural Rendering runtime , into a game directory.

Replaces the earlier RenoDX "Control" add-on approach entirely (see
docs/dlss5-external-folders.md for that history). That approach patched a
leaked, Control-specific binary's process-name check and could never go
further than that: Control's Neural Rendering pass finds its depth/motion
"guides" by matching Control's own shader signatures, which no other game's
shaders will ever match. Confirmed live on 007 First Light, 2026-09-01: the
add-on hooked NGX correctly (CreateFeature/EvaluateFeature firing every
frame) and got stuck at "RR: WAITING FOR GUIDES" indefinitely.

This runtime's Neural Rendering pass works differently and is why this
actually has a chance: it runs generically over the upscaler's OWN final
output buffer, never touching game-specific shaders at all. Confirmed live
on 007 First Light, 2026-09-02: the pass dispatches every frame
(`DlssNr_Dx12::Dispatch DLSS-NR running at 2560x1440 …`) and its per-frame
parameter readback echoes back the exact values it was given.

2026-09-02: switched from fetching a release archive off GitHub at install
time to GreenBoost's own pre-built, NVIDIA-only runtime
(`libraries/dlssnr/runtime/`, build source kept outside this repo , see
`libraries/dlssnr/FORK_NOTES.md` for provenance and license terms). Built
runtime is ~7 MB, versus the earlier vendor-agnostic build this replaced,
which pulled in ~200 MB of alternate-vendor upscaler/frame-gen DLLs this
project's NVIDIA-only hardware target never used.

What GreenBoost does and doesn't do here: it never fetches, bundles, or
redistributes nvngx_dlssnr.dll , that is NVIDIA's, and the user supplies
their own copy in gb_gaming.dlss5.asset_dir(). The GreenBoostDLSS5 runtime
itself IS bundled (MIT-licensed, and it's this project's own build) , see
`libraries/dlssnr/runtime/`.

This needs no ReShade install and no Streamline DLL swap: the runtime hooks
DXGI/D3D12/NGX itself via its own proxy DLL, and a native-DLSS game's own
Streamline runtime is left untouched.

Sidecar convention: this module tracks every file it adds or overwrites in
one JSON manifest (`.gb_dlss5_manifest.json` in the game directory) ,
simpler to reason about across the ~6 files a real install touches, and the
only thing uninstall_dlss5_runtime_from_game() trusts. `_legacy_runtime_migration()`
below also recognises the manifest filename an install made before
2026-09-02 used, so an old install stays detectable and removable.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# This module lives at gb_gaming/dlss5_runtime.py in both the dev tree
# (repo_root/gb_gaming/) and the installed tree
# ($LIBDIR/greenboost-gaming/gb_gaming/, see install.sh's APP_LIB_DIR) , in
# both cases install.sh stages libraries/dlssnr/runtime/ as a sibling of
# gb_gaming/, so one parent.parent-relative path resolves it in either
# location without hardcoding $LIBDIR (which is configurable at install
# time and unknown to this process).
_RUNTIME_DIR = (
    Path(os.environ["GB_DLSS5_RUNTIME_DIR"]) if os.environ.get("GB_DLSS5_RUNTIME_DIR")
    else Path(__file__).resolve().parent.parent / "libraries" / "dlssnr" / "runtime"
)

_MANIFEST_NAME = ".gb_dlss5_manifest.json"
_LEGACY_MANIFEST_NAME = ".gb_optiscaler_manifest.json"  # installs made before 2026-09-02

_BACKUP_SUFFIX = ".gb_dlss5_original"
_LEGACY_BACKUP_SUFFIX = ".gb_optiscaler_original"

# Order matters: tried in sequence, skipping any name the game already ships
# natively. dxgi.dll/d3d12.dll are last , they're the ones a game is most
# likely to already need for something else (an Agility SDK redirect, in
# d3d12.dll's case).
PROXY_CANDIDATES = (
    "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll",
    "dxgi.dll", "d3d12.dll",
)


def ensure_dlss5_runtime_available() -> tuple[bool, Path | str]:
    """Locates GreenBoost's own built DLSS5 runtime (no network involved).
    Returns (True, runtime_dir) or (False, error_message); never raises."""
    if not _RUNTIME_DIR.is_dir():
        return False, f"DLSS5 runtime not found: {_RUNTIME_DIR}"
    if not (_RUNTIME_DIR / "GreenBoostDLSS5.dll").is_file():
        return False, f"GreenBoostDLSS5.dll missing from runtime dir: {_RUNTIME_DIR}"
    return True, _RUNTIME_DIR


# Top-level entries from the built runtime (libraries/dlssnr/runtime/) that
# are actually needed at runtime.
_RUNTIME_ENTRIES = ("GreenBoostDLSS5.ini", "nvngx.dll_dlssnr.dll", "GreenBoostDLSS5", "Licenses")


def pick_proxy_dll(game_dir: Path) -> str | None:
    """First candidate from PROXY_CANDIDATES not already shipped by the game.
    None when every candidate collides , the caller should surface that
    rather than guess."""
    for name in PROXY_CANDIDATES:
        if not (game_dir / name).is_file():
            return name
    return None


def _legacy_runtime_migration(game_dir: Path) -> dict | None:
    """An install made before 2026-09-02 wrote its manifest under
    `_LEGACY_MANIFEST_NAME` and recorded the runtime's own ini under the
    literal name `OptiScaler.ini` (that build's own `_RUNTIME_ENTRIES`). Such
    an install (~200 MB, including alternate-vendor upscaler DLLs this
    project never needed) must be fully removed before the current runtime
    goes in , otherwise the dead payload stays on disk and its ini keeps
    shadowing `GreenBoostDLSS5.ini`. Returns the removal result dict, or
    None if there was nothing legacy to remove."""
    legacy_path = game_dir / _LEGACY_MANIFEST_NAME
    if not legacy_path.is_file():
        return None
    try:
        manifest = json.loads(legacy_path.read_text())
    except (OSError, ValueError):
        return None
    if "OptiScaler.ini" not in manifest.get("added", []) + manifest.get("overwritten", []):
        return None
    return _uninstall_from_manifest(game_dir, legacy_path, manifest, _LEGACY_BACKUP_SUFFIX)


def has_pending_legacy_install(game_dir: Path) -> bool:
    """True when a pre-2026-09-02 install (`OptiScaler.ini`, the ~200 MB
    vendor-agnostic runtime) is still on disk for this game and hasn't been
    replaced by the current GreenBoostDLSS5 runtime yet. Migration only runs
    inside install_dlss5_runtime_into_game() , i.e. the next time DLSS 5 is
    (re-)enabled for this game , so a game left alone since before that date
    is still running the old build: none of GreenBoostDLSS5.ini's settings
    (including anything the Suite's panel writes) apply to it, because that
    file doesn't exist yet , OptiScaler.ini, a different file, is still what
    the old runtime reads. Read-only check, same shape as
    _legacy_runtime_migration() but without touching anything."""
    return ((game_dir / _LEGACY_MANIFEST_NAME).is_file()
            and not (game_dir / "GreenBoostDLSS5.ini").is_file())


def install_dlss5_runtime_into_game(runtime_dir: Path, game_dir: Path, proxy_dll: str,
                                     nvngx_dlssnr_src: Path) -> dict:
    """Copies the DLSS5 runtime into `game_dir`, renaming GreenBoostDLSS5.dll
    to `proxy_dll`, plus a real copy of the user-supplied nvngx_dlssnr.dll
    (not a symlink , this module owns its own install, independent of the
    external-DLL-folder feature). Existing files at any destination path are
    snapshotted before being overwritten. Every path touched is recorded in
    `_MANIFEST_NAME` so uninstall only ever removes or restores what this
    call can prove it did.

    Migrates a legacy (pre-2026-09-02) install first if one is found , see
    _legacy_runtime_migration()."""
    migration = _legacy_runtime_migration(game_dir)
    if migration is not None and not migration.get("ok", False):
        return {"ok": False, "msg": f"could not remove the previous DLSS5 install first: {migration.get('msg')}"}

    if not (runtime_dir / "GreenBoostDLSS5.dll").is_file():
        return {"ok": False, "msg": f"GreenBoostDLSS5.dll not found in runtime dir: {runtime_dir}"}
    if not game_dir.is_dir():
        return {"ok": False, "msg": f"game directory not found: {game_dir}"}
    if not nvngx_dlssnr_src.is_file():
        return {"ok": False, "msg": f"nvngx_dlssnr.dll not found: {nvngx_dlssnr_src}"}

    added: list[str] = []
    overwritten: list[str] = []

    def _place(src: Path, rel_dest: str):
        dst = game_dir / rel_dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            backup = dst.with_name(dst.name + _BACKUP_SUFFIX)
            if not backup.exists():
                shutil.move(str(dst), str(backup))
                overwritten.append(rel_dest)
            else:
                dst.unlink()
        else:
            added.append(rel_dest)
        shutil.copy2(src, dst)

    _place(runtime_dir / "GreenBoostDLSS5.dll", proxy_dll)
    for entry in _RUNTIME_ENTRIES:
        src = runtime_dir / entry
        if not src.exists():
            continue
        if src.is_dir():
            for f in src.rglob("*"):
                if f.is_file():
                    _place(f, str(Path(entry) / f.relative_to(src)))
        else:
            _place(src, entry)
    _place(nvngx_dlssnr_src, "nvngx_dlssnr.dll")

    manifest = {"proxy_dll": proxy_dll, "added": added, "overwritten": overwritten}
    (game_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

    return {
        "ok": True,
        "msg": f"DLSS5 runtime installed as {proxy_dll} , {len(added) + len(overwritten)} file(s) placed",
        "proxy_dll": proxy_dll,
        "files": added + overwritten,
    }


def _uninstall_from_manifest(game_dir: Path, manifest_path: Path, manifest: dict,
                              backup_suffix: str) -> dict:
    removed = 0
    for rel in manifest.get("added", []):
        p = game_dir / rel
        if p.is_file():
            p.unlink()
            removed += 1
    restored = 0
    for rel in manifest.get("overwritten", []):
        p = game_dir / rel
        backup = p.with_name(p.name + backup_suffix)
        if backup.is_file():
            if p.exists():
                p.unlink()
            shutil.move(str(backup), str(p))
            restored += 1
    manifest_path.unlink()
    return {"ok": True, "msg": f"removed {removed} file(s), restored {restored} original(s)"}


def uninstall_dlss5_runtime_from_game(game_dir: Path) -> dict:
    """Reads `_MANIFEST_NAME` (or, failing that, a legacy manifest from
    before 2026-09-02) and reverses exactly what it recorded: deletes every
    added file, restores every overwritten one from its backup snapshot.
    Silently succeeds (nothing to do) if no manifest exists , this is never
    the caller's error."""
    for name, suffix in ((_MANIFEST_NAME, _BACKUP_SUFFIX),
                         (_LEGACY_MANIFEST_NAME, _LEGACY_BACKUP_SUFFIX)):
        manifest_path = game_dir / name
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as e:
            return {"ok": False, "msg": f"could not read {manifest_path}: {e}"}
        return _uninstall_from_manifest(game_dir, manifest_path, manifest, suffix)
    return {"ok": True, "msg": "nothing installed"}


def _set_ini_key(ini_path: Path, section: str, key: str, value: str) -> dict:
    """Idempotent line-oriented edit of the runtime's ini format: sets
    `key=value` under `[section]`, touching nothing else in the file , no
    reformatting, no other section rewritten, comments and blank lines
    preserved verbatim. Creates the section (and the key within it) if
    either is missing, rather than requiring it to pre-exist."""
    if not ini_path.is_file():
        return {"ok": False, "msg": f"{ini_path.name} not found: {ini_path}"}

    raw = ini_path.read_bytes().decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)

    header = f"[{section}]"
    out: list[str] = []
    in_section = False
    section_found = False
    key_set = False

    for line in lines:
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            in_section = t.lower() == header.lower()
            if in_section:
                section_found = True
            out.append(line)
            continue
        if in_section and not key_set:
            eq = t.split("=", 1)
            if len(eq) == 2 and eq[0].strip().lower() == key.lower():
                out.append(f"{key}={value}")
                key_set = True
                continue
        out.append(line)

    if section_found and not key_set:
        # Section exists but had no matching key , append it right after
        # the header rather than guessing where else it might belong.
        idx = next(i for i, l in enumerate(out) if l.strip().lower() == header.lower())
        out.insert(idx + 1, f"{key}={value}")
    elif not section_found:
        out.append(header)
        out.append(f"{key}={value}")

    new_text = newline.join(out)
    if raw.endswith(newline) and not new_text.endswith(newline):
        new_text += newline

    tmp = ini_path.with_name(ini_path.name + ".tmp")
    tmp.write_bytes(new_text.encode("utf-8"))
    tmp.replace(ini_path)
    return {"ok": True, "msg": f"set [{section}] {key}={value} in {ini_path}"}


def set_ini_key(ini_path: Path, section: str, key: str, value: str) -> dict:
    """Public entry point for _set_ini_key(), for callers outside this
    module (gb_gaming.dlss5.write_config()) that need to set an arbitrary
    key rather than one of this module's own named setters."""
    return _set_ini_key(ini_path, section, key, value)


def read_ini_section(ini_path: Path, section: str) -> dict[str, str]:
    """Returns every `key=value` pair under `[section]`, verbatim (including
    the literal string "auto" , the runtime's own "use the DLL's built-in
    default" sentinel, which is not the same as any numeric default and must
    not be silently coerced into one). Empty dict if the file or section is
    missing, never raises."""
    if not ini_path.is_file():
        return {}
    raw = ini_path.read_bytes().decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in raw else "\n"
    header = f"[{section}]".lower()
    out: dict[str, str] = {}
    in_section = False
    for line in raw.split(newline):
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            in_section = t.lower() == header
            continue
        if not in_section or not t or t.startswith(";"):
            continue
        eq = t.split("=", 1)
        if len(eq) == 2:
            out[eq[0].strip()] = eq[1].strip()
    return out


def set_neural_rendering_enabled(ini_path: Path, enabled: bool) -> dict:
    """Sets `Enabled=` under `[DlssNr]` to `true`/`false`. Ships with
    `Enabled=auto` by default (effectively off unless every internal
    condition happens to line up); this is what lets the Suite's toggle be
    the only thing a user ever has to touch , no opening the runtime's own
    in-game overlay required."""
    return _set_ini_key(ini_path, "DlssNr", "Enabled", "true" if enabled else "false")


def disable_startup_splash(ini_path: Path) -> dict:
    """Sets `DisableSplash=true` under `[Menu]`. Ships with `DisableSplash=auto`
    (effectively `false`, i.e. the splash shows) , GreenBoost installs this
    silently as part of the Suite's own DLSS 5 feature, so the runtime's own
    branding never appears on screen."""
    return _set_ini_key(ini_path, "Menu", "DisableSplash", "true")
