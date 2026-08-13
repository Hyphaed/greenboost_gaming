#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
GreenBoost Gaming Suite , GTK4 GUI.

This is the lightweight first-class GUI for users who don't want to build
the Tauri bundle.  The panels mirror what the React/Tauri build offers:

  • Games        , scan Steam library, show installed titles + Proton/DLSS
                   versions, apply per-game GreenBoost env presets
  • DLSS         , list out-of-date DLSS/FSR/XeSS DLLs and offer updates
  • GPU Profile  , view current GPU clocks/power/fan, apply a saved profile
  • Status       , GreenBoost detection, Vulkan layer health
  • About        , disclaimer + license

Heavy logic (Steam library parse, DLSS scan, NVML telemetry) lives in
companion modules under `gb_gaming/`.  This file is just the shell.

Run:  python3 main.py
"""
from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    from gi.repository import Gtk, Gdk, Gio, Adw, GLib
except (ImportError, ValueError) as e:
    print(f"GreenBoost Gaming Suite: GTK4/libadwaita missing ({e}).",
          file=sys.stderr)
    print("Install: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1",
          file=sys.stderr)
    sys.exit(1)

# Backend modules live alongside this file.  Add the package's parent
# to sys.path so `gb_gaming.*` works whether the GUI is run from source
# (PROJECT_ROOT/ui/main.py) or from installed location
# (/usr/local/lib/greenboost-gaming/greenboost_gaming_gui.py).
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent, _HERE):
    if (_candidate / "gb_gaming" / "__init__.py").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

try:
    from gb_gaming import game_scanner, dlss_updater, gpu_profile
except ImportError:
    # Backend modules absent , panels gracefully degrade to placeholders.
    game_scanner = dlss_updater = gpu_profile = None  # type: ignore


APP_ID  = "com.ferran.greenboost-gaming-suite"
APP_VER = "0.1.0"
DISCLAIMER = (
    "GreenBoost is an independent open-source project and is not affiliated "
    "with, endorsed by, or sponsored by NVIDIA Corporation. NVIDIA, CUDA, "
    "GeForce, and RTX are trademarks of NVIDIA Corporation."
)


# ─────────────────────────────────────────────────────────────────────
# Diagnostics , what the Status panel reports
# ─────────────────────────────────────────────────────────────────────

def diag_greenboost() -> dict[str, str]:
    """Probe for an installed GreenBoost , used by the Status panel."""
    cli = shutil.which("greenboost")
    out: dict[str, str] = {}
    out["cli"] = cli or "not found"
    out["shim"] = next(
        (p for p in ("/usr/local/lib/libgreenboost_cuda.so",
                     "/usr/lib/libgreenboost_cuda.so") if Path(p).exists()),
        "not found",
    )
    kver = os.uname().release
    kmod_root = Path(f"/lib/modules/{kver}")
    if kmod_root.exists():
        hit = list(kmod_root.rglob("greenboost.ko*"))
        out["kmod"] = str(hit[0]) if hit else "not loaded"
    else:
        out["kmod"] = "no /lib/modules tree"
    return out


def diag_vulkan() -> dict[str, str]:
    out: dict[str, str] = {}
    out["loader"] = shutil.which("vulkaninfo") or "not found"
    out["layer_manifest"] = next(
        (p for p in (
            "/usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json",
            "/etc/vulkan/implicit_layer.d/VkLayer_greenboost.json",
        ) if Path(p).exists()),
        "not found",
    )
    out["layer_so"] = next(
        (p for p in ("/usr/local/lib/libVkLayer_greenboost.so",
                     "/usr/lib/libVkLayer_greenboost.so") if Path(p).exists()),
        "not found",
    )
    return out


def diag_steam() -> dict[str, str]:
    """Find Steam library roots , used by the Games panel."""
    candidates = [
        Path.home() / ".steam/steam",
        Path.home() / ".local/share/Steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",  # Flatpak
    ]
    out: dict[str, str] = {}
    for c in candidates:
        if c.exists():
            out[str(c)] = "present"
    return out or {"<none>": "no Steam library detected"}


# ─────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────

def make_kv_grid(rows: list[tuple[str, str]]) -> Gtk.Widget:
    """Two-column key/value grid with monospaced values."""
    grid = Gtk.Grid(column_spacing=18, row_spacing=6, margin_top=8,
                    margin_bottom=8, margin_start=12, margin_end=12)
    for i, (k, v) in enumerate(rows):
        klbl = Gtk.Label(label=k, xalign=0)
        klbl.add_css_class("dim-label")
        vlbl = Gtk.Label(label=v, xalign=0, selectable=True, wrap=True)
        vlbl.add_css_class("monospace")
        grid.attach(klbl, 0, i, 1, 1)
        grid.attach(vlbl, 1, i, 1, 1)
    return grid


def make_section(title: str, child: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                  margin_top=12)
    hdr = Gtk.Label(label=title, xalign=0)
    hdr.add_css_class("title-4")
    box.append(hdr)
    frame = Gtk.Frame()
    frame.set_child(child)
    box.append(frame)
    return box


def make_placeholder(text: str) -> Gtk.Widget:
    lbl = Gtk.Label(label=text, wrap=True, xalign=0)
    lbl.add_css_class("dim-label")
    lbl.set_margin_top(24); lbl.set_margin_bottom(24)
    lbl.set_margin_start(18); lbl.set_margin_end(18)
    return lbl


# ─────────────────────────────────────────────────────────────────────
# Panels
# ─────────────────────────────────────────────────────────────────────

def _game_cover_widget(game) -> Gtk.Widget:
    """Build a small Gtk.Picture of the game's cover art, with a
    placeholder fallback when Steam hasn't cached it yet."""
    cover = getattr(game, "cover_path", None)
    if cover is not None and cover.exists():
        try:
            pic = Gtk.Picture.new_for_filename(str(cover))
            pic.set_can_shrink(True)
            pic.set_keep_aspect_ratio(True)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(48, 70)        # 600×900 aspect → 48×72
            pic.add_css_class("gb-game-cover")
            return pic
        except Exception:
            pass
    # Placeholder: a green-tinted square with the first letter of the
    # game name.
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    box.add_css_class("gb-game-cover-placeholder")
    box.set_size_request(48, 70)
    label = Gtk.Label(label=(game.name[:1].upper() if game.name else "?"))
    label.add_css_class("gb-game-cover-placeholder-letter")
    label.set_halign(Gtk.Align.CENTER)
    label.set_valign(Gtk.Align.CENTER)
    box.append(label)
    label.set_hexpand(True); label.set_vexpand(True)
    return box


def _games_listbox(games: list, on_select=None) -> Gtk.Widget:
    """Render a list of Game dataclasses as a ListBox with Steam cover
    art and an optional row-select callback."""
    lb = Gtk.ListBox()
    lb.add_css_class("boxed-list")
    lb.add_css_class("gb-game-list")
    if not games:
        lb.append(Gtk.Label(label="No installed games detected.",
                            margin_top=18, margin_bottom=18))
        return lb
    for g in games[:200]:  # cap the row count for sanity
        row = Gtk.ListBoxRow()
        row.add_css_class("gb-game-row")

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=14, margin_start=6, margin_end=6,
                        margin_top=6, margin_bottom=6)
        outer.append(_game_cover_widget(g))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=g.name, xalign=0)
        title.add_css_class("gb-game-title")
        text.append(title)

        sub_bits = []
        if g.has_dlss:                       sub_bits.append("DLSS")
        if g.proton_prefix is not None:      sub_bits.append("Proton prefix")
        if g.size_on_disk:
            sub_bits.append(f"{g.size_on_disk // (1024 * 1024 * 1024)} GB")
        subtitle_text = (f"appid {g.appid} · " + " · ".join(sub_bits)
                         if sub_bits else f"appid {g.appid}")
        subtitle = Gtk.Label(label=subtitle_text, xalign=0)
        subtitle.add_css_class("gb-game-sub")
        text.append(subtitle)

        outer.append(text)
        row.set_child(outer)
        if on_select is not None:
            # Store the game on the row so the on_select can recover it.
            row._gb_game = g  # type: ignore[attr-defined]
        lb.append(row)
    if len(games) > 200:
        lb.append(Gtk.Label(label=f"… and {len(games) - 200} more",
                            margin_top=12, margin_bottom=12))
    if on_select is not None:
        def _on_row_activated(_lb, row):
            on_select(getattr(row, "_gb_game", None))
        lb.connect("row-activated", _on_row_activated)
    return lb


def panel_games() -> Gtk.Widget:
    """Lists Steam roots and installed games via gb_gaming.game_scanner.

    PR-RRR: DLSS update summary merged into this panel , matches the
    Tauri side where DLSS no longer has its own sidebar entry."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

    if game_scanner is None:
        box.append(make_section("Steam library roots",
                                make_kv_grid(list(diag_steam().items()))))
        box.append(make_section(
            "Installed games",
            make_placeholder("gb_gaming.game_scanner not importable , "
                             "is the gb_gaming/ package installed alongside "
                             "the GUI?")))
        return box

    try:
        summary = game_scanner.summary()
    except Exception as e:
        box.append(make_section("Steam library roots",
            make_placeholder(f"Steam library scan failed: {e}")))
        return box
    roots = summary["libraries"] or ["<none>"]
    box.append(make_section(
        "Steam library roots",
        make_kv_grid([(r, "present") for r in roots])))
    box.append(make_section(
        f"Installed games ({summary['game_count']})",
        _games_listbox(summary["games"])))

    # Inline DLSS quick-status , counts of detected libraries across
    # all games.  Detailed per-game update + restore UI stays on the
    # Tauri side; here we just summarise so users on the lean GTK4
    # build know whether their games carry DLSS.
    if dlss_updater is not None:
        try:
            scan_paths = [g.install_dir for g in summary["games"]
                          if g.has_dlss]
            findings = dlss_updater.scan_paths(scan_paths)
            rows = [
                ("DLSS-enabled games", str(len(scan_paths))),
                ("Recognised DLLs",    str(len(findings))),
                ("Out of date",
                 str(sum(1 for f in findings if f.needs_update))),
                ("Update source",
                 dlss_updater.get_sources().nvngx_source),
            ]
            box.append(make_section("DLSS libraries", make_kv_grid(rows)))
            box.append(make_section("DLSS update",
                make_placeholder(
                    "The Tauri UI exposes per-game Update / Restore "
                    "buttons.  On this lean GTK4 build, run the bundled "
                    "CLI from a terminal:\n\n"
                    "    python3 -c 'from gb_gaming import dlss_updater "
                    "as d, game_scanner as g; "
                    "[print(d.download_latest(name)) for name in "
                    "(\"nvngx_dlss.dll\",)]'\n\n"
                    "Or install Rust + Node and re-run install.sh to "
                    "get the full Tauri GUI."
                )))
        except Exception as e:
            box.append(make_section("DLSS libraries",
                make_placeholder(f"DLSS scan failed: {e}")))
    return box


def panel_dlss() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

    if dlss_updater is None or game_scanner is None:
        box.append(make_section(
            "Library scan",
            make_placeholder("gb_gaming backends not importable.")))
        return box

    # Scan each game's install_dir for DLSS DLLs.  Cheap pre-filter:
    # only scan games whose game_scanner marked has_dlss=True so we
    # don't rglob through libraries with thousands of unrelated files.
    try:
        games = game_scanner.list_games(scan_dlss=True)
        roots = [g.install_dir for g in games if g.has_dlss]
        findings = dlss_updater.scan_paths(roots)
    except Exception as e:
        box.append(make_section("Library scan",
            make_placeholder(f"DLSS scan failed: {e}")))
        return box

    summary_rows = [
        ("DLLs found",      str(len(findings))),
        ("Out of date",     str(sum(1 for f in findings if f.needs_update))),
        ("Known families",  ", ".join(sorted({s.family for s in dlss_updater.KNOWN.values()}))),
    ]
    box.append(make_section("Summary", make_kv_grid(summary_rows)))

    lb = Gtk.ListBox()
    lb.add_css_class("boxed-list")
    if not findings:
        lb.append(Gtk.Label(label="No DLSS / FSR / XeSS DLLs detected.",
                            margin_top=18, margin_bottom=18))
    for f in findings:
        row = Adw.ActionRow()
        row.set_title(f.spec.pretty)
        cur = dlss_updater._fmt_ver(f.current) if f.current else "unknown"
        latest = dlss_updater._fmt_ver(f.spec.latest)
        suffix = " ⚠ update available" if f.needs_update else " ✓ up to date"
        row.set_subtitle(
            f"{f.path.parent.name} · {cur} → latest {latest}{suffix}")
        lb.append(row)
    box.append(make_section("Findings", lb))
    return box


def _gpu_live_widget() -> Gtk.Widget:
    """Live snapshot of GPU stats; refreshes every 2 s via GLib timeout."""
    rows_widget = make_kv_grid([("status", "loading…")])
    container = Gtk.Frame(child=rows_widget)
    state = {"box": container, "current": rows_widget}

    def refresh():
        try:
            live = gpu_profile.read_gpu(0) if gpu_profile else None
        except Exception as e:
            # Previously unguarded: an exception here (e.g. nvidia-smi
            # permission denied, driver briefly offline) propagated out of
            # this GLib timeout callback, which silently stops it from
            # ever firing again , the panel would freeze on stale data
            # forever with nothing telling the user why. Show the error
            # and keep polling instead; a transient failure can recover.
            new = make_kv_grid([("GPU read failed", str(e))])
            state["box"].set_child(new)
            state["current"] = new
            return True
        if not live:
            new = make_kv_grid([("nvidia-smi", "not available")])
        else:
            new = make_kv_grid([
                ("name",            live.name),
                ("temperature",     f"{live.temperature_c} °C"),
                ("power",           f"{live.power_draw_w:.1f} / {live.power_limit_w:.0f} W"),
                ("core clock",      f"{live.sm_clock_mhz} MHz"),
                ("memory clock",    f"{live.mem_clock_mhz} MHz"),
                ("fan",             f"{live.fan_speed_pct} %"),
                ("utilization",     f"GPU {live.util_gpu_pct} % · MEM {live.util_mem_pct} %"),
            ])
        state["box"].set_child(new)
        state["current"] = new
        return True   # keep ticking

    GLib.timeout_add_seconds(2, refresh)
    refresh()
    return container


def panel_profile() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

    if gpu_profile is None:
        box.append(make_section("Profile",
                                make_placeholder("gb_gaming.gpu_profile "
                                                 "not importable.")))
        return box

    box.append(make_section("Live GPU state", _gpu_live_widget()))

    try:
        profiles = gpu_profile.list_profiles()
        p_rows = [(name, str(gpu_profile.PROFILE_DIR / f"{name}.json"))
                  for name in profiles] or [("<none>", "no profiles saved yet")]
        box.append(make_section(
            f"Saved profiles ({len(profiles)})", make_kv_grid(p_rows)))
    except Exception as e:
        box.append(make_section("Saved profiles",
            make_placeholder(f"Profile list failed: {e}")))

    notes = make_placeholder(
        "Apply / save controls are not yet wired into the GTK4 shell , for "
        "now use `python3 -m gb_gaming.gpu_profile_cli` (planned).\n\n"
        "Apply requires either root or a running X session with "
        "`nvidia-settings` on PATH.  Read-only telemetry above always works."
    )
    box.append(make_section("Editor", notes))
    return box


def panel_status() -> Gtk.Widget:
    gb = diag_greenboost()
    vk = diag_vulkan()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(make_section(
        "GreenBoost", make_kv_grid([(k, v) for k, v in gb.items()])))
    box.append(make_section(
        "Vulkan layer", make_kv_grid([(k, v) for k, v in vk.items()])))
    ok = (gb["shim"] != "not found" or gb["cli"] != "not found") \
         and vk["layer_so"] != "not found"
    verdict = Gtk.Label(
        label=("✓ Ready , Vulkan games can use the GreenBoost pool."
               if ok else
               "⚠ Not ready , install GreenBoost or re-run installer."),
        xalign=0,
    )
    verdict.add_css_class("title-4")
    verdict.set_margin_top(18); verdict.set_margin_start(12)
    box.append(verdict)
    return box


def panel_about() -> Gtk.Widget:
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                   margin_top=18, margin_bottom=18,
                   margin_start=24, margin_end=24)
    title = Gtk.Label(label="GreenBoost Gaming Suite", xalign=0)
    title.add_css_class("title-1")
    sub   = Gtk.Label(label=f"Version {APP_VER} , by Ferran Duarri", xalign=0)
    sub.add_css_class("dim-label")
    disclaim = Gtk.Label(label=DISCLAIMER, wrap=True, xalign=0)
    # DLSS DLL source , be precise: NVIDIA-authored binaries, community
    # mirror. See DLSS_UPDATER.md in the project root for the full story.
    dlss_source = Gtk.Label(
        label=("DLSS DLLs are NVIDIA-authored binaries, downloaded on demand "
               "from the community-maintained DLSS-Updater mirror at "
               "github.com/Recol/DLSS-Updater-DLLs. The Gaming Suite does "
               "not bundle them and does not phone home; the only network "
               "traffic from the DLSS panel happens when you click Update."),
        wrap=True, xalign=0,
    )
    dlss_source.add_css_class("dim-label")
    licence = Gtk.Label(
        label="License: GPL v2 , see /usr/share/doc/greenboost-gaming/LICENSE",
        xalign=0, selectable=True,
    )
    licence.add_css_class("monospace")
    for w in (title, sub, disclaim, dlss_source, licence):
        body.append(w)
    return body


# ─────────────────────────────────────────────────────────────────────
# Application shell
# ─────────────────────────────────────────────────────────────────────

# NVIDIA-app-look-alike CSS.  Mirrors the design tokens used by the
# Tauri/React UI's index.css so both shells feel like the same product.
# Heavy use of explicit selectors (.gb-sidebar, .gb-nav-item, .gb-card)
# means we can override libadwaita's defaults where they fight us.
_APP_CSS = """
/* ── Root window ──────────────────────────────────────────────────── */
window, .background {
    background-color: #1e2124;
    color: #dde1e8;
    font-family: -apple-system, "SF Pro Text", "Inter",
                 "Segoe UI", system-ui, sans-serif;
}

/* ── Native window header (Adw.HeaderBar with min/max/close) ──────── */
.gb-window-header {
    background-color: #1a1c1e;
    border-bottom: 1px solid #2d3038;
    min-height: 38px;
    padding: 0 6px;
    box-shadow: none;
}
.gb-window-header headerbar {
    background-color: #1a1c1e;
}
.gb-brand {
    color: #dde1e8;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.4px;
}

/* ── Sidebar ──────────────────────────────────────────────────────── */
.gb-sidebar {
    background-color: #1a1c1e;
    border-right: 1px solid #2d3038;
    padding: 0;
    min-width: 200px;
}
.gb-sidebar-logo {
    padding: 18px 16px 22px;
    color: #76b900;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 1px;
}
.gb-sidebar-logo-sub {
    color: #596070;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.6px;
    margin-top: 2px;
}
.gb-nav-item {
    background: transparent;
    color: #8a9ab0;
    border: none;
    border-radius: 0;
    padding: 11px 18px;
    font-size: 13px;
    font-weight: 500;
    box-shadow: none;
    transition: background 120ms ease;
    margin: 0;
}
.gb-nav-item:hover {
    background-color: #2a2d35;
    color: #dde1e8;
}
.gb-nav-item.active {
    background-color: rgba(118, 185, 0, 0.10);
    color: #dde1e8;
    border-left: 3px solid #76b900;
    padding-left: 15px;
}
.gb-nav-item label {
    text-align: left;
}
.gb-nav-icon {
    color: inherit;
    margin-right: 12px;
}
.gb-sidebar-footer {
    padding: 14px 18px;
    color: #596070;
    font-size: 10px;
}

/* ── Main content area ────────────────────────────────────────────── */
.gb-content {
    background-color: #1e2124;
    padding: 24px 32px;
}
.gb-page-header {
    background-color: #1e2124;
    border-bottom: 1px solid #2d3038;
    padding: 0 32px;
    min-height: 56px;
}
.gb-page-title {
    color: #dde1e8;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.2px;
}

/* ── Cards ────────────────────────────────────────────────────────── */
.gb-section-title {
    color: #8a9ab0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin: 0 0 10px 2px;
}
.gb-card {
    background-color: #252830;
    border: 1px solid #2d3038;
    border-radius: 8px;
    padding: 16px 20px;
}
.gb-card-row {
    padding: 10px 0;
    border-bottom: 1px solid #2d3038;
}
.gb-card-row:last-child { border-bottom: none; }

.gb-key {
    color: #8a9ab0;
    font-size: 12px;
    font-weight: 500;
}
.gb-value {
    color: #dde1e8;
    font-size: 13px;
    font-weight: 500;
}
.gb-value.ok    { color: #76b900; }
.gb-value.warn  { color: #e8a000; }
.gb-value.dim   { color: #596070; }

/* ── Buttons ──────────────────────────────────────────────────────── */
.gb-primary-btn {
    background-color: #76b900;
    color: #0d0f12;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 13px;
    box-shadow: none;
}
.gb-primary-btn:hover { background-color: #85d000; }
.gb-primary-btn:disabled { opacity: 0.5; }
.gb-secondary-btn {
    background-color: #2a2d35;
    color: #dde1e8;
    border: 1px solid #2d3038;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 12px;
}
.gb-secondary-btn:hover { background-color: #333840; }

/* ── PR-RRR: game list rows with Steam cover art ──────────────────── */
.gb-game-list, .gb-game-list listbox {
    background-color: transparent;
    border: none;
}
.gb-game-row {
    background-color: #252830;
    border-bottom: 1px solid #2d3038;
    padding: 0;
    transition: background 120ms ease;
}
.gb-game-row:hover    { background-color: #2a2d35; }
.gb-game-row:selected { background-color: rgba(118, 185, 0, 0.10); }
.gb-game-cover {
    border-radius: 4px;
    background-color: #1a1c1e;
}
.gb-game-cover-placeholder {
    background-color: rgba(118, 185, 0, 0.18);
    border-radius: 4px;
}
.gb-game-cover-placeholder-letter {
    color: #76b900;
    font-weight: 700;
    font-size: 22px;
}
.gb-game-title {
    color: #dde1e8;
    font-size: 14px;
    font-weight: 600;
}
.gb-game-sub {
    color: #8a9ab0;
    font-size: 11px;
}

/* Override libadwaita defaults that clash with the look. */
.dim-label, label.dim-label { opacity: 0.65; }
scrolledwindow {
    background-color: #1e2124;
    border: none;
}
"""


def _nav_button(label_text: str, icon_name: str,
                on_click) -> Gtk.Button:
    """Sidebar nav button , icon + label, left-aligned, with CSS class
    `gb-nav-item` for theming."""
    btn = Gtk.Button()
    btn.add_css_class("gb-nav-item")
    btn.set_has_frame(False)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.add_css_class("gb-nav-icon")
    label = Gtk.Label(label=label_text, xalign=0)
    label.set_hexpand(True)
    box.append(icon)
    box.append(label)
    btn.set_child(box)
    btn.connect("clicked", lambda _b: on_click())
    return btn


class GreenBoostGamingApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        # ── Force libadwaita dark colour scheme, regardless of system. ──
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        win = Adw.ApplicationWindow(application=self)
        win.set_title("GreenBoost Gaming Suite")
        win.set_default_size(1180, 760)

        # ── Apply the global CSS first so widgets inherit it on creation. ──
        provider = Gtk.CssProvider()
        provider.load_from_data(_APP_CSS.encode("utf-8"), -1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # ── Sidebar (left) ──────────────────────────────────────────────
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.add_css_class("gb-sidebar")
        sidebar.set_size_request(200, -1)

        # Logo block
        logo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        logo_box.add_css_class("gb-sidebar-logo")
        logo = Gtk.Label(label="GREENBOOST", xalign=0)
        logo.add_css_class("gb-sidebar-logo")
        logo_sub = Gtk.Label(label="GAMING SUITE", xalign=0)
        logo_sub.add_css_class("gb-sidebar-logo-sub")
        logo_box.append(logo)
        logo_box.append(logo_sub)
        sidebar.append(logo_box)

        # Nav definitions , five sections per current spec.
        # (DLSS lives inside Games rather than its own panel; that
        # decision is reflected in the Tauri UI, the GTK4 version
        # keeps DLSS as a dedicated tab for now because reworking the
        # Games panel to embed DLSS needs the inline source picker
        # which doesn't have a GTK4 implementation yet.)
        sections = [
            ("Status",  "emblem-default-symbolic",      panel_status),
            ("Games",   "applications-games-symbolic",  panel_games),
            ("Profile", "preferences-system-symbolic",  panel_profile),
            ("About",   "help-about-symbolic",          panel_about),
        ]

        # Pages live in a ViewStack we drive programmatically.
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(160)
        stack.set_hexpand(True)
        stack.set_vexpand(True)

        nav_buttons: list[Gtk.Button] = []
        page_title_label = Gtk.Label(label="Status", xalign=0)
        page_title_label.add_css_class("gb-page-title")

        def select(idx: int) -> None:
            for j, b in enumerate(nav_buttons):
                if j == idx: b.add_css_class("active")
                else:        b.remove_css_class("active")
            name, _, _ = sections[idx]
            stack.set_visible_child_name(name.lower())
            page_title_label.set_label(name)

        for i, (name, icon, builder) in enumerate(sections):
            scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
            scrolled.set_child(builder())
            stack.add_named(scrolled, name.lower())

            btn = _nav_button(name, icon, lambda _i=i: select(_i))
            nav_buttons.append(btn)
            sidebar.append(btn)

        # Sidebar bottom: version + dot
        sidebar.append(Gtk.Box(vexpand=True))  # spacer
        footer = Gtk.Label(label=f"v{APP_VER}", xalign=0)
        footer.add_css_class("gb-sidebar-footer")
        sidebar.append(footer)

        # ── Content area (right) ────────────────────────────────────────
        content_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Top page header with section title.
        page_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        page_header.add_css_class("gb-page-header")
        page_header.set_valign(Gtk.Align.CENTER)
        page_header.append(page_title_label)
        # Right side: minimal app indicator (NO NVIDIA reference).
        right_pad = Gtk.Box(hexpand=True)
        page_header.append(right_pad)
        page_header.set_margin_start(0)
        page_header.set_margin_end(0)

        content_root.append(page_header)
        content_root.append(stack)

        # ── Window-controls header bar.  Adw.ApplicationWindow needs
        # an Adw.HeaderBar somewhere in its content tree to render the
        # window's min / max / close buttons; without it the window
        # appears chromeless (which is the bug the user hit).  We keep
        # the header bar minimal , just a brand label on the left and
        # the system controls on the right , so the per-section
        # `page_header` below it (which carries the section title)
        # doesn't fight it visually. ─────────────────────────────────
        header = Adw.HeaderBar()
        header.add_css_class("gb-window-header")
        brand = Gtk.Label(label="GreenBoost Gaming Suite")
        brand.add_css_class("gb-brand")
        header.set_title_widget(brand)
        # Add an About button to the left so the menu route still exists.
        menu = Gio.Menu()
        menu.append("About", "app.about")
        header.pack_end(
            Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        # ── Top-level vertical layout: header on top, then split below ──
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.append(header)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.append(sidebar)
        body.append(content_root)
        body.set_vexpand(True)
        root.append(body)
        win.set_content(root)

        select(0)  # default to Status

        about_act = Gio.SimpleAction.new("about", None)
        about_act.connect("activate", self._on_about, win)
        self.add_action(about_act)

        win.present()

    def _on_about(self, _action, _param, parent):
        about = Adw.AboutWindow(
            transient_for=parent,
            application_name="GreenBoost Gaming Suite",
            application_icon="greenboost-gaming",
            developer_name="Ferran Duarri",
            version=APP_VER,
            comments=textwrap.fill(DISCLAIMER, width=72),
            license_type=Gtk.License.GPL_2_0,
            website="https://gitlab.com/IsolatedOctopi/greenboost",
        )
        about.present()


def main() -> int:
    return GreenBoostGamingApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
