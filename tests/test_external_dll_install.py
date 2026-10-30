# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Permanent "copy into game" / restore for the external DLL folder feature
(gb_gaming.dlss_updater.install_external_folder_into_game /
restore_external_folder_from_game / inspect_external_folder).

Companion to tests/test_external_dll_paths.py, which covers the launch-time
symlink overlay , this file covers the sibling mechanism that copies real
files instead, so they survive Steam's "Verify integrity of game files".
Unlike the wrapper module, gb_gaming.dlss_updater is a normal importable
package (no main()-at-import-time gate), so these tests import it directly
rather than lifting functions out of source via AST.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gb_gaming import dlss_updater as d  # noqa: E402


@pytest.fixture()
def game_dir(tmp_path):
    g = tmp_path / "game"
    g.mkdir()
    return g


@pytest.fixture()
def src_dir(tmp_path):
    s = tmp_path / "SL 2.13"
    s.mkdir()
    return s


# ── install_external_folder_into_game: copy-in ──────────────────────────

def test_replaces_existing_dll_and_snapshots_original(src_dir, game_dir):
    (src_dir / "sl.dlss.dll").write_bytes(b"new version")
    (game_dir / "sl.dlss.dll").write_bytes(b"shipped version")

    results = d.install_external_folder_into_game(src_dir, game_dir)
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["added"] is False
    assert (game_dir / "sl.dlss.dll").read_bytes() == b"new version"
    assert (game_dir / "sl.dlss.gdlss_original").read_bytes() == b"shipped version"


def test_second_install_does_not_overwrite_existing_original(src_dir, game_dir):
    (src_dir / "sl.dlss.dll").write_bytes(b"v2")
    (game_dir / "sl.dlss.dll").write_bytes(b"shipped")
    d.install_external_folder_into_game(src_dir, game_dir)
    assert (game_dir / "sl.dlss.gdlss_original").read_bytes() == b"shipped"

    (src_dir / "sl.dlss.dll").write_bytes(b"v3")
    d.install_external_folder_into_game(src_dir, game_dir)
    # The durability guarantee: the ORIGINAL shipped bytes must still be
    # what a "restore to shipped" would produce, not v2.
    assert (game_dir / "sl.dlss.gdlss_original").read_bytes() == b"shipped"
    assert (game_dir / "sl.dlss.dll").read_bytes() == b"v3"


def test_adds_dll_the_game_does_not_ship_with_marker(src_dir, game_dir):
    (src_dir / "nvngx_dlssnr.dll").write_bytes(b"neural rendering")
    results = d.install_external_folder_into_game(src_dir, game_dir)
    assert results[0]["ok"] is True
    assert results[0]["added"] is True
    assert (game_dir / "nvngx_dlssnr.dll").read_bytes() == b"neural rendering"
    assert (game_dir / "nvngx_dlssnr.dll.gb_added").exists()
    # No shipped snapshot should ever exist for a file that was never shipped.
    assert not (game_dir / "nvngx_dlssnr.gdlss_original").exists()


def test_non_dll_files_are_ignored(src_dir, game_dir):
    (src_dir / "readme.txt").write_bytes(b"hi")
    results = d.install_external_folder_into_game(src_dir, game_dir)
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "no .dll files found" in results[0]["msg"]


def test_subdirectories_are_not_recursed_into(src_dir, game_dir):
    (src_dir / "a.dll").write_bytes(b"x")
    nested = src_dir / "nested"
    nested.mkdir()
    (nested / "b.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(src_dir, game_dir)
    assert [r["name"] for r in results] == ["a.dll"]


def test_path_with_space_works_end_to_end(tmp_path, game_dir):
    spaced = tmp_path / "SL 2.13"
    spaced.mkdir()
    (spaced / "sl.dlss.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(spaced, game_dir)
    assert results[0]["ok"] is True
    assert (game_dir / "sl.dlss.dll").exists()


def test_refuses_to_write_through_existing_symlink(src_dir, game_dir, tmp_path):
    real = tmp_path / "elsewhere.dll"
    real.write_bytes(b"the user's own file, from the launch overlay")
    (game_dir / "sl.dlss.dll").symlink_to(real)
    (src_dir / "sl.dlss.dll").write_bytes(b"copy-in attempt")

    results = d.install_external_folder_into_game(src_dir, game_dir)
    assert results[0]["ok"] is False
    assert "symlink" in results[0]["msg"]
    assert real.read_bytes() == b"the user's own file, from the launch overlay"


def test_target_outside_game_root_is_rejected(src_dir, game_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (src_dir / "sl.dlss.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(
        src_dir, game_dir, targets={"sl.dlss.dll": [str(outside)]})
    assert results[0]["ok"] is False
    assert "outside the game directory" in results[0]["msg"]
    assert not (outside / "sl.dlss.dll").exists()


def test_targets_map_directs_copy_to_plugin_subdirectory(src_dir, game_dir):
    plugin_dir = game_dir / "Engine" / "Plugins" / "Win64"
    plugin_dir.mkdir(parents=True)
    (src_dir / "sl.dlss.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(
        src_dir, game_dir, targets={"sl.dlss.dll": [str(plugin_dir)]})
    assert results[0]["ok"] is True
    assert (plugin_dir / "sl.dlss.dll").exists()
    assert not (game_dir / "sl.dlss.dll").exists()


def test_disabled_name_is_not_copied(src_dir, game_dir):
    (src_dir / "sl.dlss.dll").write_bytes(b"x")
    (src_dir / "nvngx_dlssnr.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(
        src_dir, game_dir, disabled=["nvngx_dlssnr.dll"])
    names = {r["name"] for r in results if r["ok"]}
    assert names == {"sl.dlss.dll"}
    assert not (game_dir / "nvngx_dlssnr.dll").exists()


def test_unmapped_streamline_name_falls_back_to_interposer_directory(src_dir, game_dir):
    """FF7 Rebirth regression this fixes: nvngx_dlssnr.dll had no target of
    its own, so it copied into the install root , game_dir here , instead
    of next to sl.interposer.dll where Streamline's plugin loader actually
    looks. When the interposer's directory is known, an unmapped
    Streamline-family name (sl.*/nvngx_*) goes there instead."""
    plugin_dir = game_dir / "Engine" / "Plugins" / "StreamlineSubset" / "Win64"
    plugin_dir.mkdir(parents=True)
    (src_dir / "nvngx_dlssnr.dll").write_bytes(b"x")
    results = d.install_external_folder_into_game(
        src_dir, game_dir, targets={"sl.interposer.dll": [str(plugin_dir)]})
    assert results[0]["ok"] is True
    assert (plugin_dir / "nvngx_dlssnr.dll").exists()
    assert not (game_dir / "nvngx_dlssnr.dll").exists()


# ── restore_external_folder_from_game ───────────────────────────────────

def test_restore_removes_added_dll_and_its_marker(src_dir, game_dir):
    (src_dir / "nvngx_dlssnr.dll").write_bytes(b"x")
    d.install_external_folder_into_game(src_dir, game_dir)
    assert (game_dir / "nvngx_dlssnr.dll").exists()

    d.restore_external_folder_from_game(game_dir)
    assert not (game_dir / "nvngx_dlssnr.dll").exists()
    assert not (game_dir / "nvngx_dlssnr.dll.gb_added").exists()


def test_restore_puts_back_shipped_dll(src_dir, game_dir):
    (src_dir / "sl.dlss.dll").write_bytes(b"new")
    (game_dir / "sl.dlss.dll").write_bytes(b"shipped")
    d.install_external_folder_into_game(src_dir, game_dir)
    assert (game_dir / "sl.dlss.dll").read_bytes() == b"new"

    d.restore_external_folder_from_game(game_dir)
    assert (game_dir / "sl.dlss.dll").read_bytes() == b"shipped"
    assert (game_dir / "sl.dlss.dll").exists()


def test_restore_never_deletes_a_dll_with_no_marker(game_dir):
    """The single most important guarantee: GreenBoost never deletes a DLL
    it cannot prove it added."""
    untouched = game_dir / "some_other_game_file.dll"
    untouched.write_bytes(b"never touched by this feature")
    d.restore_external_folder_from_game(game_dir)
    assert untouched.exists()
    assert untouched.read_bytes() == b"never touched by this feature"


def test_marker_with_no_sibling_dll_is_a_silent_no_op(game_dir):
    marker = game_dir / "ghost.dll.gb_added"
    marker.write_bytes(b"")
    results = d.restore_external_folder_from_game(game_dir)
    assert not marker.exists()
    assert all(r["ok"] for r in results)


def test_restore_is_idempotent(src_dir, game_dir):
    (src_dir / "sl.dlss.dll").write_bytes(b"new")
    (game_dir / "sl.dlss.dll").write_bytes(b"shipped")
    (src_dir / "nvngx_dlssnr.dll").write_bytes(b"added")
    d.install_external_folder_into_game(src_dir, game_dir)

    d.restore_external_folder_from_game(game_dir)
    state_after_first = sorted(p.name for p in game_dir.iterdir())

    d.restore_external_folder_from_game(game_dir)
    state_after_second = sorted(p.name for p in game_dir.iterdir())
    assert state_after_first == state_after_second


def test_restore_scans_target_directories_too(src_dir, game_dir):
    plugin_dir = game_dir / "Engine" / "Win64"
    plugin_dir.mkdir(parents=True)
    (src_dir / "sl.dlss.dll").write_bytes(b"x")
    targets = {"sl.dlss.dll": [str(plugin_dir)]}
    d.install_external_folder_into_game(src_dir, game_dir, targets=targets)
    assert (plugin_dir / "sl.dlss.dll").exists()

    d.restore_external_folder_from_game(game_dir, targets=targets)
    assert not (plugin_dir / "sl.dlss.dll").exists()


# ── inspect_external_folder: read-only preview ──────────────────────────

def test_inspect_reports_unknown_version_for_non_pe_file(src_dir):
    (src_dir / "not_a_real.dll").write_bytes(b"this is not a PE file")
    rows = d.inspect_external_folder(src_dir)
    assert rows[0]["version"] == "unknown"


def test_inspect_known_type_populated_for_recognised_name(src_dir):
    (src_dir / "nvngx_dlss.dll").write_bytes(b"x")
    rows = d.inspect_external_folder(src_dir)
    assert rows[0]["known_type"] == "DLSS Super Resolution"


def test_inspect_known_type_empty_for_unrecognised_name(src_dir):
    (src_dir / "totally_unknown_thing.dll").write_bytes(b"x")
    rows = d.inspect_external_folder(src_dir)
    assert rows[0]["known_type"] == ""


def test_inspect_never_writes_to_the_folder(src_dir):
    (src_dir / "nvngx_dlss.dll").write_bytes(b"x")
    before = sorted(p.name for p in src_dir.iterdir())
    d.inspect_external_folder(src_dir)
    after = sorted(p.name for p in src_dir.iterdir())
    assert before == after


def test_inspect_matches_the_same_dll_filter_as_the_launch_overlay(src_dir):
    """A drift here means the preview list and the launch overlay disagree
    about what would actually be linked , same case-insensitive ".dll"
    suffix, same non-recursive scope, as _external_dll_plan()."""
    (src_dir / "a.dll").write_bytes(b"x")
    (src_dir / "B.DLL").write_bytes(b"x")
    (src_dir / "notes.txt").write_bytes(b"x")
    nested = src_dir / "nested"
    nested.mkdir()
    (nested / "c.dll").write_bytes(b"x")

    rows = d.inspect_external_folder(src_dir)
    names = sorted(r["name"] for r in rows)
    assert names == ["B.DLL", "a.dll"]


def test_inspect_description_and_product_empty_when_absent(src_dir):
    """Verified against a real ReShade add-on 2026-08-28: it carries a
    VS_VERSIONINFO block with only OriginalFilename/FileVersion, no
    FileDescription/ProductName , must not raise or fabricate a value."""
    (src_dir / "no_string_info.dll").write_bytes(b"not a real PE file")
    rows = d.inspect_external_folder(src_dir)
    assert rows[0]["description"] == ""
    assert rows[0]["product"] == ""
