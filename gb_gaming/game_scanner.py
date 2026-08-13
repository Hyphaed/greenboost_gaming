# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.game_scanner , enumerate installed Steam games.

The scanner parses Valve's KeyValues format (used in libraryfolders.vdf and
each app's appmanifest_*.acf).  It's tolerant of malformed input , Steam
itself sometimes leaves partially-written manifests when force-quit during
an install.

Public API:
    discover_libraries() -> list[Path]
    list_games(libraries=None) -> list[Game]
    Game(appid, name, install_dir, library, last_played, has_dlss)

No external dependencies , stdlib only.  Heavy disk scanning is bounded:
we cap manifest count per library to avoid pathological cases.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_MANIFESTS_PER_LIBRARY = 4096


# ──────────────────────────────────────────────────────────────────────
# KeyValues parser , tolerant of comments, BOM, trailing junk
# ──────────────────────────────────────────────────────────────────────

# Tokeniser regex: matches "quoted string", { , }, or an unquoted bareword.
_TOK_RE = re.compile(
    r"""
    "((?:[^"\\]|\\.)*)"     |   # 1: quoted string (handles \" \\)
    (\{)                    |   # 2: open brace
    (\})                    |   # 3: close brace
    /\*.*?\*/               |   # comment (block)
    //[^\n]*                |   # comment (line)
    ([A-Za-z_][\w./-]*)         # 4: bareword
    """,
    re.VERBOSE | re.DOTALL,
)


def _parse_kv(text: str) -> dict:
    """Parse a KeyValues blob into a nested dict.  Last value wins on
    duplicate keys (matches Steam's behaviour)."""
    # Strip UTF-8 BOM if present.
    if text.startswith("﻿"):
        text = text[1:]

    tokens: list[tuple[str, str]] = []
    for m in _TOK_RE.finditer(text):
        if m.group(1) is not None:
            tokens.append(("str", m.group(1)))
        elif m.group(2):
            tokens.append(("{", "{"))
        elif m.group(3):
            tokens.append(("}", "}"))
        elif m.group(4) is not None:
            tokens.append(("str", m.group(4)))

    def parse_block(start: int) -> tuple[dict, int]:
        out: dict = {}
        i = start
        while i < len(tokens):
            t, v = tokens[i]
            if t == "}":
                return out, i + 1
            if t != "str":
                i += 1
                continue
            key = v
            if i + 1 >= len(tokens):
                break
            t2, v2 = tokens[i + 1]
            if t2 == "{":
                sub, i = parse_block(i + 2)
                out[key.lower()] = sub
            elif t2 == "str":
                out[key.lower()] = v2
                i += 2
            else:
                i += 1
        return out, i

    parsed, _ = parse_block(0)
    return parsed


# ──────────────────────────────────────────────────────────────────────
# Library discovery
# ──────────────────────────────────────────────────────────────────────

def _steam_roots() -> list[Path]:
    """Candidate Steam install locations on Linux."""
    home = Path.home()
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",  # Flatpak
        home / ".steam/root",
    ]


def discover_libraries() -> list[Path]:
    """Return every Steam library root that contains a steamapps/ tree.

    Reads ~/.steam/steam/steamapps/libraryfolders.vdf when present;
    falls back to scanning the candidate roots themselves.  De-duplicated
    by canonical resolved path."""
    found: dict[str, Path] = {}
    for root in _steam_roots():
        if not root.exists():
            continue
        libfile = root / "steamapps" / "libraryfolders.vdf"
        if libfile.exists():
            try:
                blob = _parse_kv(libfile.read_text(errors="replace"))
            except Exception:
                blob = {}
            # libraryfolders.vdf shape:
            #   "libraryfolders" {
            #       "0" { "path" "/home/.../.steam/steam" "label" "" ... }
            #       "1" { "path" "/mnt/games" ... }
            #   }
            lf = blob.get("libraryfolders") or blob
            if isinstance(lf, dict):
                for v in lf.values():
                    if isinstance(v, dict) and "path" in v:
                        p = Path(v["path"]) / "steamapps"
                        if p.exists():
                            found[str(p.resolve())] = p.resolve()
        # The root itself is always a library if it has steamapps/.
        own = root / "steamapps"
        if own.exists():
            found[str(own.resolve())] = own.resolve()
    return list(found.values())


# ──────────────────────────────────────────────────────────────────────
# Game enumeration
# ──────────────────────────────────────────────────────────────────────

# Steam ships Proton builds, runtimes, and redistributables as "apps"
# with their own appmanifest_*.acf , they show up in libraryfolders.vdf
# alongside real games but the user never wants to see them in a games
# list.  We filter them by:
#   • appid against a curated set of known infrastructure ids, AND
#   • name prefix / contains check as a forward-compat safety net
#     (so new Proton X.Y releases get filtered even before we update
#     the appid list).
_STEAM_INFRA_APPIDS: frozenset[int] = frozenset({
    228980,    # Steamworks Common Redistributables
    1070560,   # Steam Linux Runtime
    1391110,   # Steam Linux Runtime - soldier
    1493710,   # Proton Experimental
    1628350,   # Steam Linux Runtime - sniper
    1826330,   # Proton 7.0
    2180100,   # Proton Hotfix
    2348590,   # Proton 9.0 (Beta)
    2805730,   # Proton EasyAntiCheat Runtime
    3658110,   # Proton 10.0
    4030330,   # Proton 9.0
    4183110,   # Steam Linux Runtime 4.0
    4628710,   # Proton 11.0
    # Older Proton: 858280 (3.7), 930400 (3.16), 961940 (4.2),
    # 1054830 (4.11), 1113280 (5.0), 1245040 (5.13), 1420170 (6.3),
    # 1492870 (7.0 beta), 1580130 (8.0 beta).
    858280, 930400, 961940, 1054830, 1113280, 1245040, 1420170,
    1492870, 1580130,
})

_STEAM_INFRA_NAME_PATTERNS: tuple[str, ...] = (
    "proton",                  # any "Proton X.Y" variant
    "steam linux runtime",     # sniper, soldier, scout, etc.
    "steamworks common",
    "easy anti-cheat",
)

def _is_steam_infra(appid: int, name: str) -> bool:
    """True if this manifest is a Steam tool / runtime, not a real game."""
    if appid in _STEAM_INFRA_APPIDS:
        return True
    n = name.lower()
    return any(pat in n for pat in _STEAM_INFRA_NAME_PATTERNS)


# Cover-art locations Steam writes when it caches library thumbnails.
# `<appid>_library_600x900.jpg` is the vertical store-page art most
# users associate with a game; `_header.jpg` is the horizontal banner.
# We try vertical first → header → grid fall-backs.  Returns None when
# Steam has never opened that game's library entry (then nothing is
# cached and the GUI shows a placeholder).
def _cover_path(library: Path, appid: int) -> Path | None:
    # The library cache lives one level up from `steamapps/` , Steam
    # writes it under either `appcache/librarycache` (legacy) or
    # `appcache/librarycache/<appid>/` (newer hashed layout).
    candidates = [
        # Legacy flat layout
        library.parent / "appcache" / "librarycache" / f"{appid}_library_600x900.jpg",
        library.parent / "appcache" / "librarycache" / f"{appid}_header.jpg",
        library.parent / "appcache" / "librarycache" / f"{appid}_library_hero.jpg",
        # Newer hashed layout , pick the first match from the
        # per-appid directory if it exists.
        library.parent / "appcache" / "librarycache" / str(appid) / "library_600x900.jpg",
        library.parent / "appcache" / "librarycache" / str(appid) / "header.jpg",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    # Last resort: scan the per-appid directory for any .jpg.
    hashed = library.parent / "appcache" / "librarycache" / str(appid)
    if hashed.is_dir():
        for f in sorted(hashed.glob("*.jpg")):
            if f.stat().st_size > 0:
                return f
    return None


@dataclass
class Game:
    appid: int
    name: str
    install_dir: Path
    library: Path
    last_played: int = 0           # unix ts (0 = unknown)
    size_on_disk: int = 0          # bytes (from manifest, may be stale)
    has_dlss: bool = False         # nvngx_dlss.dll found in install_dir
    proton_prefix: Path | None = None
    cover_path: Path | None = None  # vertical store art if Steam cached it
    extra: dict = field(default_factory=dict)


def _has_dlss_dll(install_dir: Path) -> bool:
    """Quick check for the presence of any DLSS DLL anywhere in the tree.

    Bounded , bails out on first hit or after scanning ~8 000 files."""
    if not install_dir.exists():
        return False
    needle = "nvngx_dlss"
    seen = 0
    try:
        for p in install_dir.rglob("*"):
            seen += 1
            if seen > 8000:
                break
            if p.is_file() and needle in p.name.lower():
                return True
    except OSError:
        pass
    return False


def _proton_prefix(library: Path, appid: int) -> Path | None:
    """Return ~/.../steamapps/compatdata/<appid>/pfx if it exists."""
    cand = library / "compatdata" / str(appid) / "pfx"
    return cand if cand.exists() else None


def list_games(libraries: list[Path] | None = None,
               *, scan_dlss: bool = True) -> list[Game]:
    """Walk each library's steamapps/ for appmanifest_*.acf files.

    Returns games sorted by name.  `scan_dlss=False` skips the per-game
    rglob for DLSS, which is helpful on large libraries where the UI
    only needs the title list."""
    libraries = libraries or discover_libraries()
    out: list[Game] = []

    for lib in libraries:
        manifests = sorted(lib.glob("appmanifest_*.acf"))
        for manifest in manifests[:MAX_MANIFESTS_PER_LIBRARY]:
            try:
                blob = _parse_kv(manifest.read_text(errors="replace"))
            except OSError:
                continue
            app = blob.get("appstate") or {}
            if not app:
                continue
            try:
                appid = int(app.get("appid", "0"))
            except ValueError:
                continue
            if appid <= 0:
                continue
            name = app.get("name", f"appid {appid}")
            # Filter Steam infrastructure entries (Proton builds,
            # runtimes, redistributables) , they're not real games.
            if _is_steam_infra(appid, name):
                continue
            installdir = app.get("installdir", "")
            install_path = lib / "common" / installdir if installdir else lib

            try:
                last_played = int(app.get("lastplayed", "0"))
            except ValueError:
                last_played = 0
            try:
                size_on_disk = int(app.get("sizeondisk", "0"))
            except ValueError:
                size_on_disk = 0

            game = Game(
                appid=appid,
                name=name,
                install_dir=install_path,
                library=lib,
                last_played=last_played,
                size_on_disk=size_on_disk,
                proton_prefix=_proton_prefix(lib, appid),
                cover_path=_cover_path(lib, appid),
            )
            if scan_dlss and install_path.exists():
                game.has_dlss = _has_dlss_dll(install_path)
            out.append(game)

    out.sort(key=lambda g: g.name.lower())
    return out


# ──────────────────────────────────────────────────────────────────────
# Convenience for the GUI
# ──────────────────────────────────────────────────────────────────────

def summary() -> dict:
    """One-shot snapshot for the Status / Games panels."""
    libs = discover_libraries()
    games = list_games(libs, scan_dlss=False)
    return {
        "libraries":     [str(p) for p in libs],
        "library_count": len(libs),
        "game_count":    len(games),
        "games":         games,
        "scanned_at":    int(time.time()),
    }
