# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.dlss_updater , find and update NVIDIA upscaler DLLs.

NVIDIA-only by policy.  We do not update AMD FidelityFX (FSR) or Intel
XeSS DLLs.

Sources, in priority order:

  • Streamline DLLs (`sl.dlss.dll`, `sl.dlss_g.dll`) are fetched from
    NVIDIA's OFFICIAL Streamline GitHub repository
    (`github.com/NVIDIAGameWorks/Streamline`) via its public Releases
    API.  No auth required, no community mirror involved.

  • nvngx_*.dll (DLSS SR / FG / RR) come from the **bundled** mirror
    by default , the `dlls/` directory shipped inside the Gaming
    Suite.  The packager populates it once from NVIDIA's official
    DLSS SDK (developer.nvidia.com) and ships the bundle in the
    install package.

  • If the bundled mirror is empty or missing a DLL, the updater can
    fall back to a community mirror , but only after the user
    explicitly opts in via Preferences.  Supported community mirrors:
      - 'recol'        , github.com/Recol/DLSS-Updater-DLLs (default)
      - 'custom'       , a user-provided base URL

The active source for nvngx_*.dll is configured at:
  /etc/greenboost-gaming/sources.conf   (system-wide; takes precedence)
  ~/.config/greenboost-gaming/sources.conf  (per-user override)

Both files share the same key=value INI-ish format:
  nvngx_source = bundled        # one of: bundled, recol, custom
  nvngx_custom_url = https://my-mirror.example/dlss/   # if source=custom

Public API:
    KNOWN: dict[str, DllSpec]
    get_sources() -> SourceConfig
    set_nvngx_source(name, custom_url=None) -> None
    list_available_dlss_tags(limit=20) -> dict
    set_pinned_tags(nvngx_tag, streamline_tag) -> None
    scan_paths(roots) -> list[DllFinding]
    download_latest(dll_name, dest_dir) -> tuple[bool, Path|str]
    update(finding, replacement, *, dry_run=False) -> tuple[bool, str]
    refresh_manifest() -> dict[str, str]
    summary(roots) -> dict
    inspect_external_folder(folder) -> list[dict]
    install_external_folder_into_game(src_dir, game_dir, targets=None, disabled=None) -> list[dict]
    restore_external_folder_from_game(game_dir, targets=None) -> list[dict]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

# ──────────────────────────────────────────────────────────────────────
# Source configuration
# ──────────────────────────────────────────────────────────────────────

# Official NVIDIA Streamline repo , used for sl.dlss.dll, sl.dlss_g.dll.
NVIDIA_STREAMLINE_REPO   = "NVIDIAGameWorks/Streamline"
NVIDIA_STREAMLINE_API    = f"https://api.github.com/repos/{NVIDIA_STREAMLINE_REPO}"
# PR-GGGG: official DLSS SDK repo , same family of public releases, but
# the nvngx_*.dll files live in the working tree (not as release assets),
# so we fetch via raw.githubusercontent.com pinned to a tag.
NVIDIA_DLSS_REPO    = "NVIDIA/DLSS"
NVIDIA_DLSS_API     = f"https://api.github.com/repos/{NVIDIA_DLSS_REPO}"
NVIDIA_DLSS_RAW     = "https://raw.githubusercontent.com"

# Bundled mirror , relative to the package, optional and user-built.
# Not shipped in the repository (see DLSS_UPDATER.md).  Read-only at runtime.
BUNDLE_DIR = Path(__file__).resolve().parent.parent / "dlls"

# PR-GGGG: official NVIDIA DLSS SDK on disk.  When present, _read_bundled_dll
# prefers its DLLs over the in-tree mirror , keeps us shipping the latest
# vendor-blessed binaries without re-vendoring on every release.
# Override path via env NVIDIA_DLSS_SDK_DIR; canonical layout matches the
# upstream repo (lib/Windows_x86_64/rel/, lib/Linux_x86_64/rel/).
_DEFAULT_NV_SDK = Path.home() / "Dev/greenboost_all/DLSS"
NVIDIA_DLSS_SDK_DIR = Path(
    os.environ.get("NVIDIA_DLSS_SDK_DIR", str(_DEFAULT_NV_SDK))
).expanduser()
NVIDIA_DLSS_SDK_WIN_DLL_DIR = NVIDIA_DLSS_SDK_DIR / "lib" / "Windows_x86_64" / "rel"

# Runtime cache , populated lazily on each Update click.  See
# libraries/README.md for the layout and lifecycle.  We resolve it to
# an absolute path now so subsequent chdir() calls in the host process
# can't move us to a different cache.
LIBRARIES_DIR = (Path(__file__).resolve().parent.parent / "libraries").resolve()

# Community mirror options for nvngx_*.dll.
COMMUNITY_MIRRORS: dict[str, str] = {
    # Recol's repo stores DLLs under main/dlls/, not at repo root.
    # The /dlls suffix is part of the base URL so the flat-layout
    # fetcher (`<base>/<dll>`) resolves correctly.
    "recol": "https://raw.githubusercontent.com/Recol/DLSS-Updater-DLLs/main/dlls",
    # 'custom' is resolved at runtime from sources.conf's nvngx_custom_url.
}

DEFAULT_SOURCE = "bundled"
SOURCES_PATHS  = [
    Path("/etc/greenboost-gaming/sources.conf"),
    Path.home() / ".config/greenboost-gaming/sources.conf",
]
HTTP_TIMEOUT_S = 20
USER_AGENT     = "greenboost-gaming/0.1"


@dataclass
class SourceConfig:
    nvngx_source:      str  = DEFAULT_SOURCE   # bundled | recol | custom
    nvngx_custom_url:  str  = ""               # only used when source=custom
    nvngx_gh_tag:      str  = ""               # pin specific NVIDIA/DLSS tag (empty=latest)
    streamline_gh_tag: str  = ""               # pin specific Streamline tag (empty=latest)
    # Cached at read time so the panel can show provenance.
    config_files_read: list[Path] = field(default_factory=list)

    def nvngx_base_url(self) -> str | None:
        """Resolve the source name to an actual base URL or None if
        the source is `bundled` (no URL , files come from disk)."""
        if self.nvngx_source == "bundled":
            return None
        if self.nvngx_source == "custom":
            return self.nvngx_custom_url.rstrip("/")
        return COMMUNITY_MIRRORS.get(self.nvngx_source, "").rstrip("/")


def _parse_conf(path: Path) -> dict[str, str]:
    """Minimal INI-style parser , `key = value`, `#` comments, no sections."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


def get_sources() -> SourceConfig:
    """Read both the system-wide and per-user config files; per-user wins."""
    cfg = SourceConfig()
    for p in SOURCES_PATHS:
        if not p.exists():
            continue
        kv = _parse_conf(p)
        cfg.config_files_read.append(p)
        if "nvngx_source" in kv:
            cfg.nvngx_source = kv["nvngx_source"]
        if "nvngx_custom_url" in kv:
            cfg.nvngx_custom_url = kv["nvngx_custom_url"]
        if "nvngx_gh_tag" in kv:
            cfg.nvngx_gh_tag = kv["nvngx_gh_tag"]
        if "streamline_gh_tag" in kv:
            cfg.streamline_gh_tag = kv["streamline_gh_tag"]
    # Validate.
    if cfg.nvngx_source not in ("bundled", "nvidia-github", "custom", *COMMUNITY_MIRRORS):
        cfg.nvngx_source = DEFAULT_SOURCE
    if cfg.nvngx_source == "custom" and not cfg.nvngx_custom_url:
        cfg.nvngx_source = DEFAULT_SOURCE
    return cfg


def set_nvngx_source(source: str, custom_url: str = "") -> None:
    """Persist the user's choice to ~/.config/greenboost-gaming/sources.conf.
    Preserves existing keys (e.g. nvngx_gh_tag, streamline_gh_tag)."""
    if source not in ("bundled", "custom", *COMMUNITY_MIRRORS):
        raise ValueError(f"unknown source: {source}")
    target = Path.home() / ".config/greenboost-gaming/sources.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _parse_conf(target) if target.exists() else {}
    existing["nvngx_source"] = source
    if source == "custom":
        existing["nvngx_custom_url"] = custom_url
    else:
        existing.pop("nvngx_custom_url", None)
    lines = ["# greenboost-gaming DLL source configuration"]
    for k, v in existing.items():
        lines.append(f"{k} = {v}")
    target.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# Known DLLs
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DllSpec:
    canonical: str
    family:    str
    latest:    tuple[int, int, int, int]
    pretty:    str
    via:       str   # 'bundled-or-mirror' | 'nvidia-streamline-github'
    # Optional DLLs are ones NVIDIA hasn't published (DLSS 5 Neural Rendering).
    # Failing to fetch one is reported as a skip and never fails the sync.
    optional:  bool = False


KNOWN: dict[str, DllSpec] = {
    # nvngx_*.dll , sourced from the official DLSS SDK at NVIDIA_DLSS_SDK_DIR
    # when reachable, else from the in-tree dlls/ mirror.  Latest tracks the
    # SDK release on disk.
    # Floor raised 2026-08-28 from 310.6.0 to 310.8.0 , measured with
    # _read_pe_fileversion() against a real Streamline 2.13 bundle (the
    # DLSS 5 mod wave; see docs/dlss5-external-folders.md), not guessed.
    "nvngx_dlss.dll":  DllSpec("nvngx_dlss.dll",  "dlss-sr",
        (310, 8, 0, 0), "DLSS Super Resolution",  "bundled-or-mirror"),
    "nvngx_dlssg.dll": DllSpec("nvngx_dlssg.dll", "dlss-fg",
        (310, 8, 0, 0), "DLSS Frame Generation",  "bundled-or-mirror"),
    "nvngx_dlssd.dll": DllSpec("nvngx_dlssd.dll", "dlss-rr",
        (310, 8, 0, 0), "DLSS Ray Reconstruction","bundled-or-mirror"),
    # DLSS 5 Neural Rendering , added 2026-08-28. NVIDIA has not published
    # an official release containing this file yet (it surfaced via a
    # leaked NBA 2K27 early-access build, see the DLSS-5-mod coverage this
    # entry was added to support), so "bundled-or-mirror" will honestly
    # fail here (empty bundle, Recol mirror 404) until NVIDIA ships one ,
    # same failure mode as any other not-yet-cached DLL, never a false
    # claim of availability. Get it via the external-DLL-folder mechanism
    # (a folder you already populated yourself) instead of Update/Sync.
    "nvngx_dlssnr.dll": DllSpec("nvngx_dlssnr.dll", "dlss-nr",
        (310, 8, 0, 0), "DLSS Neural Rendering",  "bundled-or-mirror", optional=True),
    # Shipped by "FINAL FANTASY VII REBIRTH" alongside the DLSS/Streamline
    # set above. NVIDIA ships it inside the Streamline SDK zip
    # (bin/x64/nvngx_deepdvc.dll, verified for 2.14.1 and 2.14.16), so it
    # is fetched the same way as the sl.*.dll files.
    "nvngx_deepdvc.dll": DllSpec("nvngx_deepdvc.dll", "deepdvc",
        (310, 3, 0, 0), "Deep DVC",                "nvidia-streamline-github"),

    # Streamline component DLLs , always from NVIDIA's official GitHub.
    # PR-CCCC: expanded to cover sl.common (loader) and sl.reflex
    # (Reflex SDK), which the Rust scanner already finds in games.
    # The list mirrors what the Recol mirror exposes under /dlls/.
    # `latest` corrected 2026-08-07: this was (310, 2, 1, 0), copied from
    # the unrelated nvngx_* SDK version above it. Real Streamline PE
    # FileVersions live in the 2.x namespace (confirmed against the cached
    # DLLs: 2.12.0.0) , the 310.x number could never be satisfied, so every
    # Streamline row showed a permanent, un-clearable "update available"
    # badge. `scan_paths()` below also widens this against whatever is
    # newest in the cache, so this constant only needs to be a floor.
    # Floor raised again 2026-08-28 from 2.12.0.0 to 2.13.0.0 , measured
    # against a real Streamline 2.13 bundle, not guessed.
    "sl.dlss.dll":      DllSpec("sl.dlss.dll",      "streamline",
        (2, 13, 0, 0), "Streamline DLSS SR",     "nvidia-streamline-github"),
    "sl.dlss_g.dll":    DllSpec("sl.dlss_g.dll",    "streamline",
        (2, 13, 0, 0), "Streamline DLSS FG",     "nvidia-streamline-github"),
    "sl.dlss_d.dll":    DllSpec("sl.dlss_d.dll",    "streamline",
        (2, 13, 0, 0), "Streamline DLSS RR",     "nvidia-streamline-github"),
    # Streamline DLSS Neural Rendering , see nvngx_dlssnr.dll above for why
    # this has no working official fetch yet; same honest-failure behaviour.
    "sl.dlss_nr.dll":   DllSpec("sl.dlss_nr.dll",   "streamline",
        (2, 13, 0, 0), "Streamline DLSS Neural Rendering", "nvidia-streamline-github",
        optional=True),
    "sl.common.dll":    DllSpec("sl.common.dll",    "streamline",
        (2, 13, 0, 0), "Streamline Loader",      "nvidia-streamline-github"),
    "sl.reflex.dll":    DllSpec("sl.reflex.dll",    "streamline",
        (2, 13, 0, 0), "Streamline Reflex",      "nvidia-streamline-github"),
    "sl.interposer.dll":DllSpec("sl.interposer.dll","streamline",
        (2, 13, 0, 0), "Streamline Interposer",  "nvidia-streamline-github"),
    "sl.nis.dll":       DllSpec("sl.nis.dll",       "streamline",
        (2, 13, 0, 0), "Streamline NIS",         "nvidia-streamline-github"),
    "sl.pcl.dll":       DllSpec("sl.pcl.dll",       "streamline",
        (2, 13, 0, 0), "Streamline PCL",         "nvidia-streamline-github"),
    # The next three are shipped by "FINAL FANTASY VII REBIRTH" but were
    # never tracked here , added 2026-08-28, floor measured from that
    # install (2.8.0.0), not guessed.
    "sl.deepdvc.dll":   DllSpec("sl.deepdvc.dll",   "streamline",
        (2, 8, 0, 0),  "Streamline Deep DVC",   "nvidia-streamline-github"),
    "sl.directsr.dll":  DllSpec("sl.directsr.dll",  "streamline",
        (2, 8, 0, 0),  "Streamline DirectSR",   "nvidia-streamline-github"),
    "sl.nvperf.dll":    DllSpec("sl.nvperf.dll",    "streamline",
        (2, 8, 0, 0),  "Streamline NvPerf",     "nvidia-streamline-github"),
}


# ──────────────────────────────────────────────────────────────────────
# PE FileVersion extraction , bare struct unpacking, no `pefile` dep
# ──────────────────────────────────────────────────────────────────────

def _read_pe_fileversion(path: Path) -> tuple[int, int, int, int] | None:
    """Reads VS_FIXEDFILEINFO.dwFileVersion{MS,LS} from a PE version resource.

    Fixed 2026-08-07: this used to take the FIRST occurrence of the
    0xFEEF04BD signature bytes anywhere in the file and trust it blindly.
    That 4-byte pattern also turns up by coincidence in unrelated binary
    data well before the real version resource , confirmed live on
    nvngx_dlss.dll, where the first hit decoded to the nonsense
    "46863.0.46863.4696" while the real VS_FIXEDFILEINFO (310.6.0.0) sat
    ~58 MB further into the file. Every occurrence is now checked and only
    one with dwStrucVersion == 0x00010000 (the fixed version-resource
    format tag, always present in a genuine VS_FIXEDFILEINFO) is accepted.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    sig = b"\xbd\x04\xef\xfe"
    start = 0
    while True:
        idx = data.find(sig, start)
        if idx < 0:
            return None
        if idx + 16 <= len(data):
            try:
                struc_version, = struct.unpack_from("<I", data, idx + 4)
            except struct.error:
                struc_version = None
            if struc_version == 0x00010000:
                ms, ls = struct.unpack_from("<II", data, idx + 8)
                return ((ms >> 16) & 0xFFFF,  ms & 0xFFFF,
                        (ls >> 16) & 0xFFFF,  ls & 0xFFFF)
        start = idx + 1


def _read_pe_string_info(path: Path) -> dict[str, str]:
    """Best-effort read of a PE VS_VERSIONINFO StringFileInfo block ,
    FileDescription and ProductName, when present. Never load-bearing for
    any placement or swap decision, purely informational.

    Not every PE carries these two keys , verified 2026-08-28 against a
    real ReShade add-on (renodx-control-rr.addon64): it has a
    VS_VERSIONINFO block with only OriginalFilename/FileVersion, no
    FileDescription/ProductName, so this returns {} for it, not a guess.
    Verified working against a real NVIDIA-signed DLL from the same day
    (sl.interposer.dll from a genuine Streamline 2.13 bundle): returns
    FileDescription "SL.INTERPOSER MODULE - PRODUCTION", ProductName
    "NVIDIA STREAMLINE PRODUCTION". A raw byte scan rather than a full
    resource-table walk , good enough for a preview list, not a place to
    trust blindly.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for key in ("FileDescription", "ProductName"):
        needle = key.encode("utf-16-le") + b"\x00\x00"
        idx = data.find(needle)
        if idx < 0:
            continue
        pos = idx + len(needle)
        while pos + 1 < len(data) and data[pos] == 0 and data[pos + 1] == 0:
            pos += 2
        end = pos
        while end + 1 < len(data):
            if data[end] == 0 and data[end + 1] == 0:
                break
            end += 2
        try:
            value = data[pos:end].decode("utf-16-le").strip()
        except UnicodeDecodeError:
            value = ""
        if value:
            out[key] = value
    return out


def _fmt_ver(v: tuple[int, int, int, int]) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}.{v[3]}"


def _parse_ver(s: str) -> tuple[int, int, int, int] | None:
    try:
        parts = tuple(int(x) for x in s.split(".")[:4])
    except ValueError:
        return None
    while len(parts) < 4:
        parts = parts + (0,)
    return parts  # type: ignore[return-value]


def _sha256(data: bytes) -> str:
    h = hashlib.sha256(); h.update(data); return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────
# Scan + finding model
# ──────────────────────────────────────────────────────────────────────

@dataclass
class DllFinding:
    path:         Path
    spec:         DllSpec
    current:      tuple[int, int, int, int] | None
    needs_update: bool
    game_root:    Path
    # Version the GAME actually shipped with, read from the `.gdlss_original`
    # sidecar `update_game_dlss` (Rust, src-tauri/src/dlss.rs) writes once,
    # the first time it ever updates this DLL, and never overwrites again.
    # None means either never updated through GreenBoost, or updated before
    # this snapshot mechanism existed (2026-08-07).
    original:     tuple[int, int, int, int] | None = None
    # The KNOWN table's `latest` is a hardcoded floor that goes stale the
    # moment a newer DLL is actually fetched into the cache (see scan_paths
    # below, which takes max(spec.latest, newest cached version)). None
    # means "use spec.latest as-is" , kept optional so DllFinding can still
    # be constructed the old way anywhere else in the module.
    effective_latest: tuple[int, int, int, int] | None = None
    # Every backup sibling on disk (`.gdlss_bak` + timestamped `.bak.<ts>`),
    # newest first , see `_restore_points()`. Lets the dropdown offer a
    # specific backup generation, not just "Shipped" vs "Cached".
    restore_points: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        upgraded = bool(
            self.original is not None
            and self.current is not None
            and self.current != self.original
        )
        latest = self.effective_latest or self.spec.latest
        return {
            "path":         str(self.path),
            "family":       self.spec.family,
            "name":         self.spec.canonical,
            "pretty":       self.spec.pretty,
            "current":      _fmt_ver(self.current) if self.current else "unknown",
            "latest":       _fmt_ver(latest),
            "via":          self.spec.via,
            "needs_update": self.needs_update,
            "game_root":    str(self.game_root),
            "shipped":      _fmt_ver(self.original) if self.original else None,
            "upgraded":     upgraded,
            "can_restore_shipped": self.original is not None,
            "restore_points": self.restore_points,
        }


def _restore_points(dll_path: Path) -> list[dict]:
    """Every backup sibling of `dll_path`, newest first: the timestamped
    `<name>.bak.<unix_ts>` files the Python install path writes (see
    `install_cached_into_game` below) plus the single-slot `<name>.gdlss_bak`
    Rust's `update_game_dlss` writes. Surfaced so the dropdown can offer a
    specific backup generation directly, instead of only the `.gdlss_original`
    "Shipped" option gated behind having run a scan first , confirmed live
    2026-08-08: "The First Berserker: Khazan" alone had 22 of these on disk,
    accumulated over four update generations, none of them ever selectable.
    """
    name = dll_path.name
    parent = dll_path.parent
    points: list[dict] = []

    single = dll_path.with_suffix(".gdlss_bak")
    if single.exists():
        ver = _read_pe_fileversion(single)
        try:
            mtime = int(single.stat().st_mtime)
        except OSError:
            mtime = 0
        points.append({
            "path": str(single),
            "label": _fmt_ver(ver) if ver else "unknown",
            "mtime": mtime,
        })

    needle = f"{name}.bak."
    try:
        for entry in parent.iterdir():
            if not entry.name.startswith(needle):
                continue
            ts_part = entry.name[len(needle):]
            if not ts_part.isdigit():
                continue
            ver = _read_pe_fileversion(entry)
            points.append({
                "path": str(entry),
                "label": _fmt_ver(ver) if ver else "unknown",
                "mtime": int(ts_part),
            })
    except OSError:
        pass

    points.sort(key=lambda d: d["mtime"], reverse=True)
    return points


def _prune_old_backups(target: Path, keep: int = 3) -> None:
    """Keep only the newest `keep` timestamped `<name>.bak.<ts>` backups for
    `target`, deleting the rest. Called after every write that adds a new
    one. Confirmed live 2026-08-08: with no cap, four Streamline bundle
    installs alone left 22 backup files behind for a single game. Skips the
    single-slot `.gdlss_bak` (not timestamped, already self-overwriting) and
    the durable `.gdlss_original` snapshot (never pruned, by design).
    Best-effort , a stale extra backup left behind on error is harmless, so
    failures here are swallowed rather than surfaced.
    """
    needle = f"{target.name}.bak."
    try:
        candidates = [
            (int(entry.name[len(needle):]), entry)
            for entry in target.parent.iterdir()
            if entry.name.startswith(needle) and entry.name[len(needle):].isdigit()
        ]
    except OSError:
        return
    candidates.sort(key=lambda t: t[0], reverse=True)
    for _, stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _in_backup_dir(p: Path, root: Path) -> bool:
    """True when p sits under a backup folder (e.g. _DLSS5_Backup) below root."""
    try:
        parts = p.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return any("backup" in d.lower() for d in parts)


def scan_paths(roots: Iterable[Path]) -> list[DllFinding]:
    out: list[DllFinding] = []
    canon = {k.lower(): v for k, v in KNOWN.items()}
    # The hardcoded KNOWN.latest is only a floor , as soon as a newer DLL is
    # fetched into the cache (Sync / per-DLL update), that's the real
    # "latest" the user can actually install. Without this, needs_update
    # can go permanently stuck the moment KNOWN falls behind an NVIDIA
    # release, exactly as happened with the Streamline 310.x constant
    # above. Computed once per scan, not per file.
    cache_latest: dict[str, tuple[int, int, int, int]] = {}
    for entry in list_cached_dlls():
        v = _parse_ver(entry["version"])
        if v is None:
            continue
        name = entry["name"]
        if v > cache_latest.get(name, (0, 0, 0, 0)):
            cache_latest[name] = v
    for root in roots:
        if not root.exists():
            continue
        seen = 0
        try:
            for p in root.rglob("*"):
                seen += 1
                if seen > 65_535:
                    break
                if not p.is_file() or _in_backup_dir(p, root):
                    continue
                spec = canon.get(p.name.lower())
                if not spec:
                    continue
                cur = _read_pe_fileversion(p)
                effective_latest = max(spec.latest, cache_latest.get(spec.canonical, spec.latest))
                needs = (cur is not None and cur < effective_latest)
                original_path = p.with_suffix(".gdlss_original")
                original = _read_pe_fileversion(original_path) if original_path.exists() else None
                out.append(DllFinding(p, spec, cur, needs, root, original, effective_latest,
                                       _restore_points(p)))
        except OSError:
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# Bundled mirror reader
# ──────────────────────────────────────────────────────────────────────

def _read_bundle_manifest() -> dict[str, dict]:
    """Read dlls/manifest.json shipped with the Gaming Suite."""
    mf = BUNDLE_DIR / "manifest.json"
    if not mf.exists():
        return {}
    try:
        return (json.loads(mf.read_text(errors="replace")) or {}).get("dlls", {}) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_bundled_dll(dll_name: str) -> tuple[bool, bytes | str]:
    """Read a bundled DLL.  Verifies SHA-256 against manifest if present.

    Source priority:
      1. The official NVIDIA DLSS SDK at NVIDIA_DLSS_SDK_DIR , used when the
         file exists there and the spec is one of the nvngx_*.dll family.
         Skips the manifest hash check (the SDK is its own provenance).
      2. github.com/NVIDIA/DLSS at the latest tag (or NVIDIA_DLSS_GH_TAG)
         , fetched via raw.githubusercontent.com.  Skipped when offline.
      3. The in-tree dlls/ mirror at BUNDLE_DIR , verified against the
         shipped manifest.json hash.

    Returns (True, bytes) on success or (False, error_message) on any
    inconsistency.  We refuse to install a bundled DLL whose hash doesn't
    match , that's how we catch a tampered install."""
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"

    # PR-GGGG: prefer the official NVIDIA DLSS SDK when reachable.  Only
    # the three nvngx_*.dll files live there; Streamline (sl.*.dll) is
    # handled separately by _fetch_streamline_release.
    sdk_path = NVIDIA_DLSS_SDK_WIN_DLL_DIR / spec.canonical
    if sdk_path.is_file():
        try:
            _SOURCE_USED[spec.canonical] = "NVIDIA DLSS SDK on disk"
            return True, sdk_path.read_bytes()
        except OSError:
            # SDK present but unreadable , fall through.
            pass

    # PR-GGGG: GitHub fallback for nvngx_*.dll.  Network-dependent; on a
    # transient failure we keep going to the in-tree mirror below.
    if dll_name.lower() in {"nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll"}:
        if os.environ.get("GREENBOOST_DLSS_DISABLE_GITHUB", "0") != "1":
            ok, payload = _fetch_nvidia_dlss_github(dll_name)
            if ok:
                return True, payload

    # NVIDIA's NGX update server (ngx.download.nvidia.com) was evaluated and
    # is deliberately not a source: its production namespace stops at DLSS
    # 310.6.0 (310.9 exists only in the "dev-mode" staging namespace), it has
    # no dlssnr or deepdvc feature, and the bucket ignores prefix queries, so
    # finding a file means paging ~80,000 keys. Using it would downgrade.

    src = BUNDLE_DIR / spec.canonical
    if not src.exists():
        return False, (f"bundled DLL missing: {src} "
                       f"(also tried SDK at {sdk_path} and "
                       f"github.com/{NVIDIA_DLSS_REPO})")
    data = src.read_bytes()
    _SOURCE_USED[spec.canonical] = "bundled (dlls/)"
    manifest = _read_bundle_manifest()
    expected = manifest.get(spec.canonical) or manifest.get(spec.canonical.lower())
    if expected and expected.get("sha256"):
        got = _sha256(data)
        if got != expected["sha256"].lower():
            return False, (f"bundled DLL hash mismatch , "
                           f"expected {expected['sha256']} got {got}")
    return True, data


# ──────────────────────────────────────────────────────────────────────
# Network fetchers
# ──────────────────────────────────────────────────────────────────────

def _http_get(url: str, *, timeout: float = HTTP_TIMEOUT_S) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, OSError):
        return None


IMAGE_FILE_MACHINE_AMD64 = 0x8664

# Which fetcher served each DLL during this process, surfaced in the cache
# manifest so the UI can show where a file really came from.
_SOURCE_USED: dict[str, str] = {}


def _pe_machine(data: bytes) -> int | None:
    """COFF Machine field of a PE image, or None when it isn't a PE."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    off = struct.unpack_from("<I", data, 0x3C)[0]
    if off + 6 > len(data) or data[off:off + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", data, off + 4)[0]


def _is_x64_pe(data: bytes) -> bool:
    return _pe_machine(data) == IMAGE_FILE_MACHINE_AMD64


# One Streamline SDK zip is ~270 MB and holds every sl.*.dll, so it is
# downloaded once per process and all its bin/x64 DLLs are kept in memory.
_STREAMLINE_ZIP_CACHE: dict[str, dict[str, bytes]] = {}


def _streamline_x64_bundle(tag: str, assets: list[dict]) -> dict[str, bytes]:
    """{lowercase dll name: bytes} for every x64 DLL under bin/x64/ in the
    release's x64 SDK zip.  The release also ships *-aarch64.zip and
    *-arm64ec.zip; those are ARM binaries a Windows x64 game can't load, so
    they are never considered (the old code took whichever zip came first,
    which was the aarch64 one)."""
    if tag in _STREAMLINE_ZIP_CACHE:
        return _STREAMLINE_ZIP_CACHE[tag]
    import io
    import zipfile
    out: dict[str, bytes] = {}
    for asset in assets:
        name = (asset.get("name") or "").lower()
        url = asset.get("browser_download_url") or ""
        if not url or not name.endswith(".zip") or "streamline" not in name:
            continue
        if any(a in name for a in ("aarch64", "arm64")):
            continue
        blob = _http_get(url, timeout=600)
        if not blob:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for member in z.namelist():
                    m = member.replace("\\", "/").lower()
                    # bin/x64/ holds the Production builds; _artifacts/ holds
                    # Develop builds with the same file names.
                    if m.startswith("bin/x64/") and m.endswith(".dll"):
                        data = z.read(member)
                        if _is_x64_pe(data):
                            out[os.path.basename(m)] = data
        except zipfile.BadZipFile:
            continue
        if out:
            break
    _STREAMLINE_ZIP_CACHE[tag] = out
    return out


_STREAMLINE_REL_CACHE: dict[str, dict | None] = {}


def _latest_tag_without_api() -> str | None:
    """Latest Streamline tag from github.com's own redirect. The REST API
    allows 60 unauthenticated requests an hour per IP; a sync used to spend
    one per DLL and hit that limit, failing every Streamline file."""
    import re
    try:
        req = urllib.request.Request(
            f"https://github.com/{NVIDIA_STREAMLINE_REPO}/releases/latest",
            method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            m = re.search(r"/releases/tag/([^/?#]+)$", r.geturl())
            return m.group(1) if m else None
    except (urllib.error.URLError, OSError):
        return None


def _streamline_release(pinned: str) -> dict | None:
    """The release to read DLLs from: {tag_name, assets}. Looked up once per
    process (not once per DLL). Tries the REST API, then falls back to
    github.com's redirect plus the standard x64 zip name, which needs no API
    quota. None when both fail."""
    key = pinned or "latest"
    if key in _STREAMLINE_REL_CACHE:
        return _STREAMLINE_REL_CACHE[key]
    endpoint = (f"{NVIDIA_STREAMLINE_API}/releases/tags/{pinned}" if pinned
                else f"{NVIDIA_STREAMLINE_API}/releases/latest")
    rel = None
    raw = _http_get(endpoint)
    if raw:
        try:
            rel = json.loads(raw)
        except json.JSONDecodeError:
            rel = None
    if not (isinstance(rel, dict) and rel.get("assets")):
        tag = pinned or _latest_tag_without_api()
        rel = None
        if tag:
            name = f"streamline-sdk-{tag}.zip"
            rel = {"tag_name": tag, "assets": [{
                "name": name,
                "browser_download_url":
                    f"https://github.com/{NVIDIA_STREAMLINE_REPO}/releases/download/{tag}/{name}"}]}
    _STREAMLINE_REL_CACHE[key] = rel
    return rel


def _fetch_streamline_release(dll_name: str,
                               cfg: SourceConfig | None = None) -> tuple[bool, bytes | str]:
    """Fetch a Streamline (or Streamline-bundled nvngx_*) DLL from NVIDIA's
    official GitHub release, x64 build only.

    GET /releases/latest (or /releases/tags/<tag> when pinned), then read
    the DLL from bin/x64/ inside the x64 SDK zip.  Public repo, no auth; the
    unauthenticated GitHub rate limit is 60 requests/hour per IP."""
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"

    pinned = (cfg.streamline_gh_tag if cfg else "") or \
             os.environ.get("NVIDIA_STREAMLINE_GH_TAG", "").strip()
    rel = _streamline_release(pinned)
    if rel is None:
        return False, ("could not reach NVIDIAGameWorks/Streamline on GitHub "
                       "(offline, or the API limit of 60 requests an hour is used up)")

    tag = rel.get("tag_name", "?")
    bundle = _streamline_x64_bundle(tag, rel.get("assets") or [])
    data = bundle.get(spec.canonical.lower())
    if data is not None:
        _SOURCE_USED[spec.canonical] = f"NVIDIA Streamline SDK {tag}"
        return True, data
    return False, (f"{spec.canonical} not found in the x64 NVIDIA Streamline "
                   f"release '{tag}'")


# PR-GGGG: official NVIDIA/DLSS repo , used as a network source for the
# nvngx_*.dll files when the local SDK isn't present.  Tag pinning via
# env `NVIDIA_DLSS_GH_TAG`; default is "latest" via the tags API.
_DLSS_GH_TAG_CACHE: dict[str, str] = {}

def _resolve_nvidia_dlss_tag(pinned: str = "") -> str | None:
    """Return the tag we should fetch.  Honours explicit `pinned` tag first,
    then `NVIDIA_DLSS_GH_TAG` env var; otherwise queries the most recent tag
    via the GitHub API.  Cached for the lifetime of the process."""
    override = pinned or os.environ.get("NVIDIA_DLSS_GH_TAG", "").strip()
    if override:
        return override
    if "latest" in _DLSS_GH_TAG_CACHE:
        return _DLSS_GH_TAG_CACHE["latest"]
    # /tags is paginated; first entry is the newest by default.
    js = _http_get(f"{NVIDIA_DLSS_API}/tags?per_page=1")
    if not js:
        return None
    try:
        arr = json.loads(js)
        if isinstance(arr, list) and arr:
            tag = arr[0].get("name")
            if tag:
                _DLSS_GH_TAG_CACHE["latest"] = tag
                return tag
    except json.JSONDecodeError:
        pass
    return None


def _fetch_nvidia_dlss_github(dll_name: str,
                               cfg: SourceConfig | None = None) -> tuple[bool, bytes | str]:
    """Fetch nvngx_*.dll from github.com/NVIDIA/DLSS at the latest tag.

    The repo checks the Windows DLLs into `lib/Windows_x86_64/rel/`
    (they're not on the Releases page), so we resolve the latest tag
    and pull the raw file pinned to that ref , guarantees reproducibility
    even if the repo's default branch is force-pushed."""
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"
    tag = _resolve_nvidia_dlss_tag(cfg.nvngx_gh_tag if cfg else "")
    if not tag:
        return False, ("could not resolve latest NVIDIA/DLSS tag "
                       "(API rate-limited or offline?)")
    url = (f"{NVIDIA_DLSS_RAW}/{NVIDIA_DLSS_REPO}/{tag}"
           f"/lib/Windows_x86_64/rel/{spec.canonical}")
    blob = _http_get(url, timeout=60)
    if blob is None or len(blob) < 1024:
        return False, f"download from {url} failed"
    # Sanity-check: must start with "MZ" , Windows PE.
    if not _is_x64_pe(blob):
        return False, f"download from {url} is not an x64 Windows PE"
    _SOURCE_USED[spec.canonical] = f"NVIDIA DLSS GitHub {tag}"
    return True, blob


def _fetch_community_mirror(dll_name: str, base_url: str) -> tuple[bool, bytes | str]:
    """Download a DLL from a community mirror.

    Recol/DLSS-Updater-DLLs keeps a `manifest.json` at the REPO ROOT, keyed
    by DLL name at the top level; each entry carries a `url` (a release
    asset) or the file sits under `dlls/`.  `base_url` ends in `/dlls`, so the
    manifest is one level up.  Mirrors without that manifest fall back to the
    flat `<base>/<dll>` pattern.  Only x64 PE files are accepted."""
    spec = KNOWN.get(dll_name.lower())
    if not spec or not base_url:
        return False, "no mirror configured"

    base = base_url.rstrip("/")
    root = base[:-len("/dlls")] if base.endswith("/dlls") else base
    for mf_url in (f"{root}/manifest.json", f"{base}/manifest.json"):
        blob = _http_get(mf_url)
        if blob is None:
            continue
        try:
            mf = json.loads(blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        table = mf if isinstance(mf, dict) else {}
        table = {**(table.get("dlls") or {}), **{k: v for k, v in table.items() if k != "dlls"}}
        entry = table.get(spec.canonical) or table.get(spec.canonical.lower())
        if isinstance(entry, dict):
            url = entry.get("url") or entry.get("download_url")
            if url:
                dll_blob = _http_get(url, timeout=60)
                if dll_blob and len(dll_blob) >= 1024 and _is_x64_pe(dll_blob):
                    _SOURCE_USED[spec.canonical] = "community mirror (Recol)"
                    return True, dll_blob
        break

    dll_blob = _http_get(f"{base}/{spec.canonical}")
    if dll_blob is None:
        return False, "not on the community mirror"
    if len(dll_blob) < 1024:
        return False, f"download too small ({len(dll_blob)} bytes) , probably 404"
    if not _is_x64_pe(dll_blob):
        return False, "mirror file is not an x64 PE"
    _SOURCE_USED[spec.canonical] = "community mirror (Recol)"
    return True, dll_blob



# NVIDIA doesn't publish nvngx_dlssnr.dll, so there is no trusted download.
# Only builds whose full SHA-256 is listed here are ever cached or installed.
# A valid Authenticode signature is not enough: a widely copied patched build
# carries NVIDIA's signature over different content, and the Recol mirror's
# copy has the same size as the real 310.8.0 but a different hash.
TRUSTED_SHA256: dict[str, dict[str, str]] = {
    "nvngx_dlssnr.dll": {
        "e16bcf15e16e13f527491cdf7845b2fe6521a738d8f7c9c721866a8496e1fc8e":
            "NVIDIA-signed 310.8.0 (RTX 50)",
    },
}


def _trust_error(canonical: str, data: bytes) -> str | None:
    """None when `data` may be used; otherwise why it was refused."""
    allowed = TRUSTED_SHA256.get(canonical.lower())
    if allowed is None:
        return None
    got = _sha256(data)
    if got in allowed:
        return None
    return (f"unverified build (sha256 {got[:12]}…), not a known NVIDIA "
            f"{canonical}; refusing to use it")


def _read_local_dlss5(dll_name: str) -> tuple[bool, bytes | str]:
    """A copy the user already placed in the DLSS5 asset folder (the one the
    "Select DLSS5 folder" button sets).  Last resort for the Neural Rendering
    DLLs, which NVIDIA hasn't published."""
    try:
        from gb_gaming import dlss5
        folder = dlss5.asset_dir()
    except Exception:
        folder = Path.home() / ".local/share/greenboost/dlss5"
    p = Path(folder) / dll_name
    try:
        data = p.read_bytes()
    except OSError:
        return False, f"not published by NVIDIA yet (and no copy in {folder})"
    if not _is_x64_pe(data):
        return False, f"{p} is not an x64 PE"
    _SOURCE_USED[KNOWN[dll_name.lower()].canonical] = "your DLSS5 folder"
    return True, data


# ──────────────────────────────────────────────────────────────────────
# Public fetch , dispatches to the right source per DLL
# ──────────────────────────────────────────────────────────────────────

def _runtime_manifest_path() -> Path:
    return LIBRARIES_DIR / "manifest.json"


def _read_runtime_manifest() -> dict:
    p = _runtime_manifest_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(errors="replace")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _versions_dir(canonical_name: str) -> Path:
    """Where every fetched version of one DLL is kept, one file per version,
    never overwritten , the multi-version cache `list_cached_dlls()` reads.
    Separate from the flat `LIBRARIES_DIR/<canonical_name>` "current" copy,
    which stays as-is for existing single-file consumers (the Streamline
    bundle installer in particular reads that flat path directly)."""
    return LIBRARIES_DIR / "versions" / canonical_name


def _write_runtime_manifest_entry(dll_name: str, sha256: str,
                                   version: str, source: str,
                                   make_current: bool = True) -> None:
    """Append-or-update one entry in libraries/manifest.json.

    Keyed by (dll name, version) , every version ever fetched gets its own
    entry, never overwritten, so the multi-version cache in
    libraries/versions/<dll>/ has metadata (source, fetch time) to go with
    each file on disk. The now-legacy flat "current version" field is kept
    too, for any caller that only wants "what's the latest".

    The file is rewritten atomically each call: read → mutate → write
    to a temp sibling → os.replace.  Safe to call from concurrent
    Update clicks because the worst case is a benign overwrite of a
    later-written manifest by a slightly-stale one (the DLL bytes are
    already on disk regardless)."""
    LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    cur = _read_runtime_manifest()
    cur.setdefault("dlls", {})
    entry = cur["dlls"].setdefault(dll_name, {})
    if make_current:
        entry["version"]    = version   # legacy "current" pointer
        entry["sha256"]     = sha256
        entry["fetched_at"] = int(time.time())
        entry["source"]     = source
    entry.setdefault("versions", {})
    entry["versions"][version] = {
        "sha256":     sha256,
        "fetched_at": int(time.time()),
        "source":     source,
    }
    tmp = LIBRARIES_DIR / "manifest.json.tmp"
    tmp.write_text(json.dumps(cur, indent=2))
    os.replace(str(tmp), str(_runtime_manifest_path()))


def download_latest(dll_name: str,
                    dest_dir: Path | None = None) -> tuple[bool, Path | str]:
    """Materialise the latest known version of `dll_name`.

    By default writes into the Suite's runtime cache at
    `libraries/<dll>`; pass `dest_dir` to override (e.g. when staging a
    file before installing into a game's directory).

    Dispatches:
      • Streamline DLLs → NVIDIA official GitHub Releases API
      • nvngx_*.dll     → configured source (bundled / recol / custom)

    Side effect: on success the runtime manifest at
    `libraries/manifest.json` is updated with the new entry so the UI
    can surface the chain of custody (which source supplied this DLL
    and when).
    """
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"

    cfg = get_sources()
    source_label = ""   # populated as we resolve which source served us.
    _SOURCE_USED.pop(spec.canonical, None)

    if spec.via == "nvidia-streamline-github":
        ok, payload = _fetch_streamline_release(dll_name, cfg)
        source_label = "github://NVIDIAGameWorks/Streamline"
    else:
        if cfg.nvngx_source == "nvidia-github":
            ok, payload = _fetch_nvidia_dlss_github(dll_name, cfg)
            tag = _DLSS_GH_TAG_CACHE.get("latest", "?")
            source_label = f"github://{NVIDIA_DLSS_REPO}@{tag}"
        elif cfg.nvngx_source == "bundled":
            # Chain: DLSS SDK on disk -> NVIDIA/DLSS GitHub -> NVIDIA NGX
            # servers -> in-tree dlls/ -> Recol mirror.  The runtime manifest
            # records which one actually served the file.
            ok, payload = _read_bundled_dll(dll_name)
            source_label = "bundled (dlls/)"
            if not ok:
                recol_base = COMMUNITY_MIRRORS.get("recol", "")
                if recol_base and spec.via == "bundled-or-mirror":
                    ok, payload = _fetch_community_mirror(dll_name, recol_base)
        else:
            base = cfg.nvngx_base_url()
            if not base:
                return False, f"source '{cfg.nvngx_source}' has no base URL"
            ok, payload = _fetch_community_mirror(dll_name, base)
            source_label = f"{cfg.nvngx_source}: {base}"

    if ok:
        bad = _trust_error(spec.canonical, payload)  # type: ignore[arg-type]
        if bad:
            ok, payload = False, bad
        elif not _is_x64_pe(payload):  # type: ignore[arg-type]
            ok, payload = False, "fetched file is not an x64 PE"
    if not ok and spec.optional:
        # Last resort for the DLSS 5 files NVIDIA hasn't published: a copy
        # the user already placed in the DLSS5 folder (hash-checked too).
        ok2, payload2 = _read_local_dlss5(spec.canonical)
        if ok2 and not _trust_error(spec.canonical, payload2):  # type: ignore[arg-type]
            ok, payload = True, payload2
        elif ok2:
            payload = _trust_error(spec.canonical, payload2)  # type: ignore[arg-type]
            _SOURCE_USED.pop(spec.canonical, None)
    if ok:
        source_label = _SOURCE_USED.get(spec.canonical, source_label)

    if not ok:
        return False, payload  # type: ignore[return-value]

    target_dir = (dest_dir or LIBRARIES_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / spec.canonical
    new_ver = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as tf:
            tf.write(payload)  # type: ignore[arg-type]
        new_ver = _read_pe_fileversion(Path(tf.name))
        os.unlink(tf.name)
    except OSError:
        pass
    old_ver = _read_pe_fileversion(dest) if dest.is_file() else None
    keep_current = bool(new_ver and old_ver and new_ver < old_ver)
    if not keep_current:
        try:
            dest.write_bytes(payload)  # type: ignore[arg-type]
        except OSError as e:
            return False, f"write failed: {e}"

    ver = new_ver if keep_current else _read_pe_fileversion(dest)
    if ver is None:
        return False, "fetched bytes are not a recognisable PE"

    # Only update the runtime manifest when we wrote into LIBRARIES_DIR
    # itself.  Staging copies into game directories should not pollute it.
    if target_dir.resolve() == LIBRARIES_DIR:
        ver_str = _fmt_ver(ver)
        _write_runtime_manifest_entry(
            spec.canonical, _sha256(payload),  # type: ignore[arg-type]
            ver_str, source_label, make_current=not keep_current)
        # Multi-version cache: keep every distinct version ever fetched,
        # keyed by its own version string, so it stays selectable later
        # even after a newer one overwrites the flat "current" file above.
        # A re-fetch of a version already on disk is a cheap no-op write,
        # not an error , happens whenever Sync re-runs and nothing changed.
        vdir = _versions_dir(spec.canonical)
        vdir.mkdir(parents=True, exist_ok=True)
        try:
            (vdir / f"{ver_str}.dll").write_bytes(payload)  # type: ignore[arg-type]
        except OSError:
            pass  # the flat "current" copy above is still valid either way

    return True, dest


# ──────────────────────────────────────────────────────────────────────
# Manifest refresh , bundled has its own; Streamline is implicit
# ──────────────────────────────────────────────────────────────────────

def _rekey_versions_cache() -> None:
    """One-time-per-call idempotent migration for caches populated by the
    old buggy `_read_pe_fileversion` (fixed 2026-08-07): mis-keyed entries
    under `libraries/versions/<dll>/<bogus-version>.dll` are renamed to the
    real version now that the reader is correct, and the matching
    `manifest.json` entries (both the per-version map and the legacy
    "current" pointer) are corrected to match. No re-download, no bytes
    touched , only names/labels. Safe to call on every `list_cached_dlls`
    / `download_all_known` invocation: once a DLL's filename already
    matches its real version, this is a no-op for it.
    """
    versions_root = LIBRARIES_DIR / "versions"
    if not versions_root.is_dir():
        return
    manifest = _read_runtime_manifest()
    dlls = manifest.setdefault("dlls", {})
    changed = False

    for dll_dir in versions_root.iterdir():
        if not dll_dir.is_dir():
            continue
        name = dll_dir.name
        entry = dlls.setdefault(name, {})
        vmeta = entry.setdefault("versions", {})
        for f in list(dll_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".dll":
                continue
            real = _read_pe_fileversion(f)
            if real is None:
                continue
            real_str = _fmt_ver(real)
            old_str = f.stem
            if real_str == old_str:
                continue
            new_path = dll_dir / f"{real_str}.dll"
            if new_path.exists():
                # Correct name already cached (e.g. a later re-fetch landed
                # it properly) , drop the mis-keyed duplicate.
                try:
                    f.unlink()
                except OSError:
                    continue
            else:
                try:
                    f.rename(new_path)
                except OSError:
                    continue
            old_meta = vmeta.pop(old_str, None)
            vmeta[real_str] = old_meta if old_meta is not None else {
                "sha256": "", "fetched_at": 0, "source": "",
            }
            if entry.get("version") == old_str:
                entry["version"] = real_str
                entry["sha256"] = vmeta[real_str].get("sha256", entry.get("sha256", ""))
            changed = True

    if changed:
        tmp = LIBRARIES_DIR / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2))
        os.replace(str(tmp), str(_runtime_manifest_path()))


def list_cached_dlls() -> list[dict]:
    """List every cached version of every DLL , one entry per (name,
    version) pair: {name, version, sha256, source, fetched_at, size_bytes,
    path}. Multiple entries can share the same `name` now that fetches are
    kept in libraries/versions/<dll>/<version>.dll instead of being
    overwritten; sorted name, then highest-version-first, so a caller that
    only wants "the current one" can just take the first match per name.
    Drives the per-game version picker , the user can select and install
    any previously-fetched version, not only the latest.

    Falls back to the flat libraries/<dll> "current" file (one entry,
    version "unknown" if not in the manifest) for anything fetched before
    the versions/ cache existed and never re-fetched since.
    """
    _rekey_versions_cache()
    out: list[dict] = []
    mf = _read_runtime_manifest().get("dlls", {})
    seen_names: set[str] = set()

    versions_root = LIBRARIES_DIR / "versions"
    if versions_root.is_dir():
        for dll_dir in versions_root.iterdir():
            if not dll_dir.is_dir():
                continue
            name = dll_dir.name
            entry_meta = mf.get(name, {})
            per_version_meta: dict = entry_meta.get("versions", {})
            for f in dll_dir.iterdir():
                if not f.is_file() or f.suffix.lower() != ".dll":
                    continue
                version = f.stem
                vmeta = per_version_meta.get(version, {})
                out.append({
                    "name":       name,
                    "version":    version,
                    "sha256":     vmeta.get("sha256", ""),
                    "source":     vmeta.get("source", ""),
                    "fetched_at": vmeta.get("fetched_at", 0),
                    "size_bytes": f.stat().st_size,
                    "path":       str(f),
                })
                seen_names.add(name)

    # Legacy fallback: a flat file with no versions/ entry at all yet
    # (fetched before this cache existed, never re-fetched since).
    if LIBRARIES_DIR.is_dir():
        for entry in LIBRARIES_DIR.iterdir():
            if not entry.is_file() or entry.suffix.lower() != ".dll":
                continue
            if entry.name in seen_names:
                continue
            meta = mf.get(entry.name, {})
            out.append({
                "name":       entry.name,
                "version":    meta.get("version", "unknown"),
                "sha256":     meta.get("sha256", ""),
                "source":     meta.get("source", ""),
                "fetched_at": meta.get("fetched_at", 0),
                "size_bytes": entry.stat().st_size,
                "path":       str(entry),
            })

    # Highest version first, not newest fetch first: a fallback source that
    # serves an older build must never become the "current" one.
    out.sort(key=lambda x: (x["name"].lower(),
                            tuple(-n for n in (_parse_ver(x["version"]) or (0, 0, 0, 0))),
                            -x["fetched_at"]))
    return out


def install_streamline_bundle_into_game(
        game_dir: Path) -> dict[str, tuple[bool, str]]:
    """Install all cached Streamline DLLs into a game directory atomically.

    Only replaces DLLs that are already present in the game dir , never
    introduces new files.  Aborts with an error per entry if the cache
    versions are inconsistent (cache must be re-synced first).

    Returns {canonical_name: (ok, msg)} for every Streamline DLL found in
    the game dir.
    """
    if not game_dir.exists():
        return {"sl.*": (False, f"game directory missing: {game_dir}")}

    # Build the Streamline bundle from the cache.
    bundle: dict[str, Path] = {}
    for name, spec in KNOWN.items():
        if spec.family != "streamline":
            continue
        src = LIBRARIES_DIR / spec.canonical
        if src.exists():
            bundle[spec.canonical.lower()] = src

    if not bundle:
        return {"sl.*": (False, "no Streamline DLLs in cache , run Sync first")}

    # Sanity-check cache coherence: Streamline plugin DLLs (all sl.* except
    # sl.common.dll, which uses driver-version encoding) must share the same
    # major so we don't install a mixed bundle.
    versions: dict[str, tuple[int, int, int, int]] = {}
    for canon_lower, src in bundle.items():
        if canon_lower == "sl.common.dll":
            continue   # uses driver-version major (e.g. 46863.x), not plugin version
        v = _read_pe_fileversion(src)
        if v:
            versions[canon_lower] = v

    major_set = {v[0] for v in versions.values()}
    if len(major_set) > 1:
        return {"sl.*": (False,
                         "cached Streamline bundle is version-inconsistent , "
                         "run Sync DLSS library again")}

    # For each Streamline DLL present in the game dir, replace from cache.
    ts = int(time.time())
    out: dict[str, tuple[bool, str]] = {}
    for canon_lower, src in bundle.items():
        # Search the game tree (depth ≤ 5) for this specific DLL.
        target: Path | None = None
        for p in game_dir.rglob(src.name):
            if (p.is_file() and p.name.lower() == canon_lower
                    and not _in_backup_dir(p, game_dir)):
                target = p
                break
        if target is None:
            continue  # game doesn't ship this Streamline plugin , skip silently
        bak = target.with_suffix(target.suffix + f".bak.{ts}")
        try:
            shutil.copy2(target, bak)
            shutil.copy2(src, target)
            out[src.name] = (True, f"replaced {target.name} (backup at {bak.name})")
            _prune_old_backups(target)
        except OSError as e:
            out[src.name] = (False, f"file IO failed: {e}")

    if not out:
        return {"sl.*": (False,
                         "no Streamline DLLs found in game directory")}
    return out


def restore_single_dll_to_original(dll_name: str, game_dir: Path) -> tuple[bool, str]:
    """Restore ONE DLL to the version the game shipped with , the
    dropdown's "Shipped: vX" choice, scoped to just this file (unlike
    src-tauri/src/dlss.rs's restore_game_dlss_to_original, which restores
    every DLL in the game at once). Works for any family: the
    .gdlss_original snapshot itself doesn't care whether it was originally
    taken by update_game_dlss (Rust) or install_cached_into_game (here) ,
    both write the exact same sidecar name.
    """
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"
    target = None
    for p in game_dir.rglob(spec.canonical):
        if p.is_file() and not _in_backup_dir(p, game_dir):
            target = p; break
    if target is None:
        return False, f"{spec.canonical} not present in {game_dir.name}"
    original = target.with_suffix(".gdlss_original")
    if not original.exists():
        return False, f"no shipped-version snapshot on record for {spec.canonical}"
    try:
        shutil.copy2(original, target)
    except OSError as e:
        return False, f"file IO failed: {e}"
    return True, f"{spec.canonical} restored to shipped version"


def install_cached_into_game(dll_name: str, game_dir: Path,
                              version: str | None = None,
                              ) -> tuple[bool, str]:
    """Copy a DLL from libraries/ into a game's install directory.

    `version`, when given, installs that EXACT cached version from
    libraries/versions/<dll>/<version>.dll , the per-game version-picker
    dropdown's "Apply" action, letting the user pick something other than
    whatever the last Sync happened to leave as "current". None (default)
    keeps the original behaviour: install the flat libraries/<dll>
    "current" file.

    For Streamline-family DLLs the whole bundle is installed atomically via
    install_streamline_bundle_into_game() to prevent ABI mismatches between
    sl.interposer / sl.pcl and the plugin DLLs , version selection isn't
    supported for this family, since installing one plugin at a version
    that doesn't match the rest of the bundle is exactly the inconsistency
    that function's own major-version check exists to prevent.
    """
    spec = KNOWN.get(dll_name.lower())
    if not spec:
        return False, f"unknown DLL: {dll_name}"

    if spec.family == "streamline":
        results = install_streamline_bundle_into_game(game_dir)
        if "sl.*" in results:
            return results["sl.*"]
        ok_count = sum(1 for ok, _ in results.values() if ok)
        err_count = len(results) - ok_count
        if err_count == 0:
            return True, f"replaced {ok_count} Streamline DLL(s) atomically"
        msgs = [f"{n}: {m}" for n, (ok, m) in results.items() if not ok]
        return False, f"{err_count} error(s): " + "; ".join(msgs)

    if version:
        src = _versions_dir(spec.canonical) / f"{version}.dll"
        if not src.exists():
            return False, f"version {version} not in cache , run Sync, or pick a version that's actually cached"
    else:
        src = LIBRARIES_DIR / spec.canonical
        if not src.exists():
            return False, f"not in cache: {spec.canonical} , run Sync first"
    if not game_dir.exists():
        return False, f"game directory missing: {game_dir}"
    # Find the in-game DLL (any depth ≤ 5).
    target = None
    for p in game_dir.rglob(spec.canonical):
        if p.is_file() and not _in_backup_dir(p, game_dir):
            target = p; break
    if target is None:
        return False, f"{spec.canonical} not present in {game_dir.name}"
    # Snapshot the shipped version once, before the very first change ever
    # made to this file through ANY path (Sync+Update or this manual Apply)
    # , matches the .gdlss_original mechanism src-tauri/src/dlss.rs's
    # update_game_dlss already uses, so "Restore to shipped version" works
    # correctly regardless of which of the two ways a DLL first got changed.
    original = target.with_suffix(".gdlss_original")
    if not original.exists():
        try:
            shutil.copy2(target, original)
        except OSError as e:
            # Was non-fatal here while src-tauri/src/dlss.rs's Rust path
            # (update_game_dlss) already aborts on the same failure , the
            # inconsistency meant a user could lose their only way back to
            # the shipped DLL through this path specifically, with the
            # install proceeding as if nothing was wrong. Matches the Rust
            # behavior now: no snapshot, no install.
            return False, f"failed to snapshot shipped version ({e}) , install skipped"
    # Backup + atomic replace. Confirmed live 2026-08-07: this previously
    # did shutil.copy2(src, target) directly , a truncate-then-write-in-
    # place copy, not atomic. A mid-copy failure (disk full, permission
    # revoked partway) left `target` corrupted with the backup already
    # written, no automatic rollback. Mirror the same temp-sibling →
    # os.replace pattern update() already uses below and _write_manifest
    # uses for manifest.json: on any failure the original `target` is
    # completely untouched , os.replace is a single rename syscall.
    bak = target.with_suffix(target.suffix + f".bak.{int(time.time())}")
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        shutil.copy2(target, bak)
        shutil.copy2(src, tmp)
        os.replace(str(tmp), str(target))
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"file IO failed: {e}"
    _prune_old_backups(target)
    return True, f"replaced {target.name} (backup at {bak.name})"


# ──────────────────────────────────────────────────────────────────────
# External DLL folder , the user-supplied-directory mechanism (see
# greenboost_proton/gb_proton_main.py's _external_dll_plan() docstring for
# the launch-time symlink overlay this complements). Everything below
# operates on an arbitrary directory the user already populated , never
# downloads, never fetches, never assumes what the files are.
# ──────────────────────────────────────────────────────────────────────

def inspect_external_folder(folder: Path) -> list[dict]:
    """Read-only preview of one external DLL folder: name, PE version, and
    the friendly type if GreenBoost recognises the filename. Never writes
    anything, never recurses , same ".dll", non-recursive scope as
    _external_dll_plan() in gb_proton_main.py, so this preview matches
    exactly what the launch overlay would link.
    """
    out: list[dict] = []
    if not folder.is_dir():
        return out
    for name in sorted(os.listdir(folder)):
        lname = name.lower()
        if not lname.endswith(".dll"):
            continue
        p = folder / name
        if not p.is_file():
            continue
        v = _read_pe_fileversion(p)
        spec = KNOWN.get(lname)
        strings = _read_pe_string_info(p)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({
            "name":       name,
            "version":    _fmt_ver(v) if v else "unknown",
            "known_type": spec.pretty if spec else "",
            "size_bytes": size,
            "description": strings.get("FileDescription", ""),
            "product":     strings.get("ProductName", ""),
        })
    return out


def _is_streamline_family(name: str) -> bool:
    """Kept in sync by hand with the identical helper in
    greenboost_proton/gb_proton_main.py's _external_dll_plan() , same
    reasoning as greenboost_ioctl.h staying a manual copy of the parent
    repo's header: two processes (this one shells out from Rust, that one
    runs inside the Proton launch) need the exact same answer and neither
    imports the other."""
    n = name.lower()
    return n.startswith("sl.") or n.startswith("nvngx_")


def _external_folder_target_dirs(dll_name: str, game_dir: Path,
                                  targets: dict[str, list[str]] | None,
                                  ) -> list[Path]:
    """Same fallback rule as _external_dll_plan(): a name with a listed
    target directory (or directories) goes there. A Streamline-family name
    with no listed target falls back to sl.interposer.dll's own directory
    when that's known , see _external_dll_plan()'s docstring for why
    `game_dir` alone is the wrong guess for an Unreal title's launcher-stub
    layout (confirmed against FINAL FANTASY VII REBIRTH, 2026-08-28).

    Anything else falls back to `game_dir`, so copy-in and the launch
    overlay always agree on where a given name lands."""
    names = targets.get(dll_name.lower()) if targets else None
    if names:
        return [Path(n) for n in names]
    interposer_dirs = targets.get("sl.interposer.dll") if targets else None
    if interposer_dirs and _is_streamline_family(dll_name):
        return [Path(n) for n in interposer_dirs]
    return [game_dir]


# Sidecar naming for a shipped file's pre-swap snapshot. Shared with Rust's
# update_game_dlss (src-tauri/src/dlss.rs), which also writes
# "<stem>.gdlss_original" for a ".dll" it swaps.
def _original_sidecar(target: Path) -> Path:
    return target.with_suffix(".gdlss_original")


def install_external_folder_into_game(
        src_dir: Path, game_dir: Path,
        targets: dict[str, list[str]] | None = None,
        disabled: list[str] | None = None) -> list[dict]:
    """Permanently COPY every *.dll directly in `src_dir` into the
    directories that already load a DLL of that name (from `targets`, the
    same map _external_dll_plan() consumes , see game_overrides.rs
    `external_dll_targets`), falling back to `game_dir` for any name with no
    listed target , see _external_folder_target_dirs. Unlike the launch-time
    symlink overlay, this survives Steam's "Verify integrity of game files",
    because the files it puts there are real.

    GreenBoost supplies the mechanism only , it never downloads, extracts,
    or redistributes anything in `src_dir`.

    A file the game already ships under that name is snapshotted via
    `_original_sidecar()` (once, ever , shared with install_cached_into_
    game's restore path) and backed up to a timestamped `.bak`, then
    replaced. A file the game does NOT already ship has no shipped version
    to restore to: a zero-byte `<name>.gb_added` marker is written *before*
    the copy (so a crash between the two leaves an orphan marker, never an
    orphan file), and restore_external_folder_from_game() only ever deletes
    a file that marker proves this function added.

    `disabled`: lowercased filenames to skip even though present in
    `src_dir , the per-DLL enable/disable control in the UI. Lets a file
    stay in the shared folder without being copied into this game.

    Returns a list of {"name", "ok", "msg", "added"} , one row per
    (dll, target directory) pair attempted; `added` distinguishes the two
    cases above for the UI's "N replaced, M added" summary.
    """
    out: list[dict] = []
    if not src_dir.is_dir():
        return [{"name": "*", "ok": False, "msg": f"folder not found: {src_dir}"}]
    if not game_dir.exists():
        return [{"name": "*", "ok": False, "msg": f"game directory missing: {game_dir}"}]
    try:
        game_root = game_dir.resolve()
    except OSError:
        game_root = game_dir

    try:
        names = os.listdir(src_dir)
    except OSError as e:
        return [{"name": "*", "ok": False, "msg": f"could not list {src_dir}: {e}"}]

    disabled_set = {n.lower() for n in (disabled or [])}
    for name in sorted(names):
        lname = name.lower()
        if not lname.endswith(".dll"):
            continue
        if lname in disabled_set:
            continue
        src = src_dir / name
        if not src.is_file():
            continue
        dst_dirs = _external_folder_target_dirs(name, game_dir, targets)
        for dst_dir in dst_dirs:
            try:
                dst_dir_resolved = dst_dir.resolve()
            except OSError:
                out.append({"name": name, "ok": False,
                            "msg": f"target directory unreadable: {dst_dir}"})
                continue
            try:
                dst_dir_resolved.relative_to(game_root)
            except ValueError:
                out.append({"name": name, "ok": False,
                            "msg": f"target {dst_dir} is outside the game directory, refused"})
                continue
            target = dst_dir_resolved / name
            if target.is_symlink():
                out.append({"name": name, "ok": False,
                            "msg": "target is a symlink from the launch overlay , "
                                   "disable the overlay toggle first, skipped"})
                continue
            if target.exists():
                # Case 1: the game already ships this name.
                original = _original_sidecar(target)
                if not original.exists():
                    try:
                        shutil.copy2(target, original)
                    except OSError as e:
                        out.append({"name": name, "ok": False,
                                    "msg": f"failed to snapshot shipped version ({e}) , install skipped"})
                        continue
                bak = target.with_suffix(target.suffix + f".bak.{int(time.time())}")
                tmp = target.with_suffix(target.suffix + ".tmp")
                try:
                    shutil.copy2(target, bak)
                    shutil.copy2(src, tmp)
                    os.replace(str(tmp), str(target))
                except OSError as e:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    out.append({"name": name, "ok": False, "msg": f"file IO failed: {e}"})
                    continue
                _prune_old_backups(target)
                out.append({"name": name, "ok": True, "added": False,
                            "msg": f"replaced {target} (backup at {bak.name})"})
            else:
                # Case 2: the game does not ship this name , mark first.
                marker = target.with_suffix(target.suffix + ".gb_added")
                try:
                    marker.touch(exist_ok=True)
                    shutil.copy2(src, target)
                except OSError as e:
                    try:
                        marker.unlink(missing_ok=True)
                    except OSError:
                        pass
                    out.append({"name": name, "ok": False, "msg": f"file IO failed: {e}"})
                    continue
                out.append({"name": name, "ok": True, "added": True,
                            "msg": f"added {target}"})
    if not out:
        out.append({"name": "*", "ok": False,
                    "msg": f"no .dll files found in {src_dir}"})
    return out


def restore_external_folder_from_game(
        game_dir: Path,
        targets: dict[str, list[str]] | None = None) -> list[dict]:
    """Undo install_external_folder_into_game() across `game_dir` and every
    directory named in `targets`. A file with a `.gb_added` marker is
    deleted along with its marker , GreenBoost never deletes a file it
    cannot prove it added. A file with a `.gdlss_original` sidecar is copied
    back from it (shared sidecar with the DLSS Picker's "Shipped" restore,
    same trust model as restore_single_dll_to_original). A marker with no
    sibling file is a silent no-op, not an error.
    """
    dirs: list[Path] = [game_dir]
    if targets:
        seen = {str(game_dir)}
        for names in targets.values():
            for n in names:
                if n not in seen:
                    seen.add(n)
                    dirs.append(Path(n))

    out: list[dict] = []
    removed = 0
    restored = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for marker in sorted(d.glob("*.gb_added")):
            target = marker.with_suffix("")  # strip ".gb_added"
            try:
                if target.exists() and not target.is_symlink():
                    target.unlink()
                    removed += 1
                marker.unlink()
            except OSError as e:
                out.append({"name": target.name, "ok": False, "msg": f"could not remove: {e}"})
        for original in sorted(d.glob("*.gdlss_original")):
            target = original.with_suffix(".dll")
            try:
                shutil.copy2(original, target)
                restored += 1
            except OSError as e:
                out.append({"name": target.name, "ok": False, "msg": f"restore failed: {e}"})
    out.append({"name": "*", "ok": True,
                "msg": f"{restored} file(s) restored to shipped version, "
                       f"{removed} added file(s) removed"})
    return out


def _purge_non_x64_cache() -> list[str]:
    """Delete cached DLLs that aren't x64 PE files and drop their manifest
    entries.  Earlier syncs cached Streamline 2.14.x from the aarch64 zip
    (machine 0xaa64), which a Windows x64 game cannot load.  Returns the
    removed paths relative to the cache."""
    removed: list[str] = []
    bad_by_name: dict[str, set[str]] = {}

    def bad(path: Path) -> bool:
        try:
            m = _pe_machine(path.read_bytes())
        except OSError:
            return False
        return m is not None and m != IMAGE_FILE_MACHINE_AMD64

    for f in LIBRARIES_DIR.glob("*.dll"):
        if bad(f):
            f.unlink(missing_ok=True)
            removed.append(f.name)
            bad_by_name.setdefault(f.name, set())
    versions_root = LIBRARIES_DIR / "versions"
    if versions_root.is_dir():
        for d in versions_root.iterdir():
            for f in d.glob("*.dll") if d.is_dir() else []:
                if bad(f):
                    f.unlink(missing_ok=True)
                    removed.append(f"{d.name}/{f.name}")
                    bad_by_name.setdefault(d.name, set()).add(f.stem)
    if bad_by_name:
        mf = _read_runtime_manifest()
        for name, vers in bad_by_name.items():
            entry = (mf.get("dlls") or {}).get(name)
            if not entry:
                continue
            for v in vers:
                (entry.get("versions") or {}).pop(v, None)
            left = sorted(entry.get("versions") or {}, key=lambda v: _parse_ver(v) or (0,))
            if left:
                entry["version"] = left[-1]
            elif name in mf.get("dlls", {}):
                mf["dlls"].pop(name)
        tmp = LIBRARIES_DIR / "manifest.json.tmp"
        tmp.write_text(json.dumps(mf, indent=2))
        os.replace(str(tmp), str(_runtime_manifest_path()))
    return removed


def download_all_known(progress=None) -> dict[str, tuple[bool, str]]:
    """Pull every DLL in KNOWN into the runtime cache at LIBRARIES_DIR.

    Mirrors the DLSS-Updater workflow: one network call per DLL,
    Streamline variants from NVIDIA's official GitHub Releases API,
    nvngx_*.dll from the configured source (bundled / community).

    Smart fallback: when the user is on the default `bundled` source
    but the bundle ships empty (the common case for fresh installs),
    we fall through to the Recol community mirror for *bulk sync only*.
    The per-DLL `download_latest()` path keeps its strict no-silent-
    fallback policy , that one's used by per-game updates where the
    user has explicitly chosen a source.  Sync is bulk-and-best-effort.

    Returns {dll_name: (ok, message)}.  Optional `progress` callback
    receives a free-text status line per DLL , used by the streaming
    Tauri wrapper to push live updates through Channel<String>.
    """
    out: dict[str, tuple[bool, str]] = {}
    LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    _rekey_versions_cache()
    cfg = get_sources()

    if progress:
        progress(f"Caching {len(KNOWN)} DLLs into {LIBRARIES_DIR} …")

    for gone in _purge_non_x64_cache():
        if progress:
            progress(f"[purge]    {gone} was not an x64 build, removed from the cache")

    for dll_name in KNOWN:
        if progress:
            progress(f"[fetch]    {dll_name}")
        ok, payload = download_latest(dll_name)
        if ok:
            out[dll_name] = (True, f"OK , {Path(payload).name}")  # type: ignore[arg-type]
            if progress:
                src = _SOURCE_USED.get(KNOWN[dll_name].canonical, "")
                progress(f"[ok]       {dll_name}  →  {Path(payload).name}"  # type: ignore[arg-type]
                         + (f"  ({src})" if src else ""))
        elif KNOWN[dll_name].optional:
            # NVIDIA hasn't published these; not having them is not an error.
            out[dll_name] = (True, f"skipped , {payload}")
            if progress:
                progress(f"[skip]     {dll_name}: {payload}")
        else:
            out[dll_name] = (False, str(payload))
            if progress:
                progress(f"[error]    {dll_name}: {payload}")
    if progress:
        ok_n = sum(1 for v in out.values() if v[0])
        progress(f"Done , {ok_n}/{len(out)} DLLs cached.")
    return out


def refresh_manifest() -> dict[str, str]:
    """Refresh in-memory KNOWN versions.  For nvngx_*: read bundled
    manifest.json (or, if source=community, peek at the mirror's
    manifest if available).  For Streamline: query NVIDIA GitHub for
    the latest release tag.

    Returns {dll_name: version_string} for entries actually refreshed."""
    refreshed: dict[str, str] = {}

    # 1) Bundled manifest , local, fast.
    for fname, entry in _read_bundle_manifest().items():
        spec = KNOWN.get(fname.lower())
        if not spec:
            continue
        ver = _parse_ver(entry.get("version", "")) if isinstance(entry, dict) else None
        if ver:
            KNOWN[fname.lower()] = replace(spec, latest=ver)
            refreshed[fname] = _fmt_ver(ver)

    # 2) NVIDIA Streamline , read tag_name as a version string when possible.
    rel_json = _http_get(f"{NVIDIA_STREAMLINE_API}/releases/latest")
    if rel_json:
        try:
            rel = json.loads(rel_json)
            tag = (rel.get("tag_name") or "").lstrip("vV")
            ver = _parse_ver(tag)
            if ver:
                for fname, spec in list(KNOWN.items()):
                    # sl.* only: nvngx_deepdvc.dll rides in the same zip but
                    # lives in the 310.x namespace, not the 2.x one.
                    if spec.via == "nvidia-streamline-github" and fname.startswith("sl."):
                        KNOWN[fname] = replace(spec, latest=ver)
                        refreshed[fname] = _fmt_ver(ver)
        except json.JSONDecodeError:
            pass

    return refreshed


# ──────────────────────────────────────────────────────────────────────
# Apply update , staged, with backup
# ──────────────────────────────────────────────────────────────────────

def update(finding: DllFinding, replacement: Path,
           *, dry_run: bool = False) -> tuple[bool, str]:
    if not replacement.exists():
        return False, f"replacement file not found: {replacement}"
    new_ver = _read_pe_fileversion(replacement)
    if new_ver is None:
        return False, "replacement is not a recognisable PE / DLL"
    if finding.current is not None and new_ver < finding.current:
        return False, (f"replacement {_fmt_ver(new_ver)} is OLDER than "
                       f"current {_fmt_ver(finding.current)} , refusing")
    if dry_run:
        return True, (
            f"would replace {finding.path.name}: "
            f"{_fmt_ver(finding.current) if finding.current else '?'} "
            f"→ {_fmt_ver(new_ver)}")
    backup = finding.path.with_suffix(
        finding.path.suffix + f".bak.{int(time.time())}")
    try:
        shutil.copy2(finding.path, backup)
        os.replace(str(replacement), str(finding.path))
    except OSError as e:
        return False, f"file IO failed: {e}"
    _prune_old_backups(finding.path)
    return True, f"replaced , backup at {backup.name}"


# ──────────────────────────────────────────────────────────────────────
# Version picker helpers , fetch available tags, persist pin choices
# ──────────────────────────────────────────────────────────────────────

def list_available_dlss_tags(limit: int = 20) -> dict:
    """Fetch available release tags/names from both NVIDIA repos.

    Returns:
        {
            "nvngx":             [{"tag": str, "name": str, "date": str}],
            "streamline":        [{"tag": str, "name": str, "date": str}],
            "nvngx_pinned":      str,   # currently pinned (empty = latest)
            "streamline_pinned": str,   # currently pinned (empty = latest)
        }
    Lists are newest-first (GitHub API default).  An empty list for a
    repo means the request was rate-limited or the network is unavailable.
    """
    cfg = get_sources()

    nvngx_tags: list[dict] = []
    js = _http_get(f"{NVIDIA_DLSS_API}/tags?per_page={limit}")
    if js:
        try:
            for t in json.loads(js) or []:
                tag = t.get("name", "")
                if tag:
                    nvngx_tags.append({"tag": tag, "name": tag, "date": ""})
        except json.JSONDecodeError:
            pass

    streamline_tags: list[dict] = []
    js = _http_get(f"{NVIDIA_STREAMLINE_API}/releases?per_page={limit}")
    if js:
        try:
            for r in json.loads(js) or []:
                tag  = r.get("tag_name", "")
                name = r.get("name", "") or tag
                date = (r.get("published_at") or "")[:10]
                if tag:
                    streamline_tags.append({"tag": tag, "name": name, "date": date})
        except json.JSONDecodeError:
            pass

    return {
        "nvngx":             nvngx_tags,
        "streamline":        streamline_tags,
        "nvngx_pinned":      cfg.nvngx_gh_tag,
        "streamline_pinned": cfg.streamline_gh_tag,
    }


def set_pinned_tags(nvngx_tag: str = "", streamline_tag: str = "") -> None:
    """Persist DLSS/Streamline release pins to sources.conf.

    Preserves existing keys (nvngx_source, nvngx_custom_url).
    Pass empty string for either tag to revert to 'latest'."""
    target = Path.home() / ".config/greenboost-gaming/sources.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _parse_conf(target) if target.exists() else {}
    existing["nvngx_gh_tag"]      = nvngx_tag
    existing["streamline_gh_tag"] = streamline_tag
    lines = ["# greenboost-gaming DLL source configuration"]
    for k, v in existing.items():
        lines.append(f"{k} = {v}")
    target.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# One-shot for the GUI
# ──────────────────────────────────────────────────────────────────────

def summary(roots: Iterable[Path]) -> dict:
    findings = scan_paths(roots)
    cfg = get_sources()
    return {
        "scanned":      len(findings),
        "out_of_date":  sum(1 for f in findings if f.needs_update),
        "findings":     [f.to_dict() for f in findings],
        "scanned_at":   int(time.time()),
        "source": {
            "nvngx":      cfg.nvngx_source,
            "nvngx_url":  cfg.nvngx_base_url() or "(local dlls/ bundle)",
            "streamline": "NVIDIAGameWorks/Streamline (official)",
        },
    }
