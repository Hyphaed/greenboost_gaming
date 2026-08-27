# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Per-game user-supplied DLL directory (external_dlls_enabled / external_dll_dir).

Lets a user point a game at a directory of DLLs they have already obtained
and placed there themselves , a debug build, a mod, a locally installed
plugin, a Streamline/DLSS bundle downloaded by hand, anything. GreenBoost
supplies the mechanism only: it never downloads, extracts, bundles, or
redistributes anything placed in that directory, and never assumes what the
files are or who is allowed to use them , a missing/empty directory, or a
disabled toggle, degrades to a normal launch with no DLL overlay.

No environment variable can inject a Unix directory into the Windows DLL
search order (confirmed against a real Proton Experimental build: WINEPATH
appears in zero Wine binaries; WINEDLLPATH exists but only covers Wine's own
builtin .so modules, not native PE DLLs). Since a game's own executable
directory is the first place Windows looks for a DLL by name, the only
mechanism that actually takes effect is placing files there. These tests pin
three functions:

- `_external_dll_plan(profile, game_dir)` , pure, decides WHAT would be
  linked and WHERE, never touches the filesystem beyond reading it.
- `_apply_external_dll_overlay(plan)` , symlinks each planned file in,
  backing up any DLL of the same name the game already ships.
- `_revert_external_dll_overlay(applied)` , removes the symlinks and
  restores the backups.

Same AST-lift technique as test_proton_helper_gate.py , the wrapper calls
main() at import time, so importing the module would try to launch a game.
All three functions depend on nothing beyond `os` and `sys`, so each lifts
cleanly on its own.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "greenboost_proton" / "gb_proton_main.py"

_FUNCS = ("_external_dll_plan", "_apply_external_dll_overlay", "_revert_external_dll_overlay")


@pytest.fixture(scope="module")
def dll_fns():
    src = WRAPPER.read_text(encoding="utf-8")
    lines = src.splitlines(True)
    found: dict = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCS:
            chunk = "".join(lines[node.lineno - 1:node.end_lineno])
            found[node.name] = chunk
    missing = [name for name in _FUNCS if name not in found]
    assert not missing, f"{missing} not found in {WRAPPER}"
    ns: dict = {"os": os, "sys": sys}
    for name in _FUNCS:
        exec(found[name], ns)                        # noqa: S102 , our own source
    return tuple(ns[name] for name in _FUNCS)


@pytest.fixture()
def plan_fn(dll_fns):
    return dll_fns[0]


@pytest.fixture()
def apply_fn(dll_fns):
    return dll_fns[1]


@pytest.fixture()
def revert_fn(dll_fns):
    return dll_fns[2]


@pytest.fixture()
def game_dir(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    return d


@pytest.fixture()
def dll_dir(tmp_path):
    d = tmp_path / "user-dlls"
    d.mkdir()
    return d


# ── _external_dll_plan: path handling ───────────────────────────────────────

def test_toggle_off_is_a_no_op(plan_fn, dll_dir, game_dir):
    (dll_dir / "sl.dlss.dll").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": False, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert plan == []


def test_empty_path_is_a_no_op(plan_fn, game_dir):
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": ""}, str(game_dir))
    assert plan == []


def test_no_field_at_all_is_a_no_op(plan_fn, game_dir):
    assert plan_fn({}, str(game_dir)) == []


def test_missing_directory_is_skipped_not_obtained(plan_fn, tmp_path, game_dir, capsys):
    missing = tmp_path / "does-not-exist"
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(missing)}, str(game_dir))
    assert plan == []
    err = capsys.readouterr().err
    assert "not found, skipping" in err
    assert "not created or fetched" in err
    assert not missing.exists()


def test_tilde_expansion(plan_fn, monkeypatch, tmp_path, game_dir):
    home = tmp_path / "home"
    (home / "my-dlls").mkdir(parents=True)
    (home / "my-dlls" / "a.dll").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(home))
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": "~/my-dlls"}, str(game_dir))
    assert len(plan) == 1
    assert plan[0][0] == os.path.join(str(home), "my-dlls", "a.dll")


def test_directory_with_no_dlls_is_a_no_op(plan_fn, dll_dir, game_dir, capsys):
    (dll_dir / "readme.txt").write_bytes(b"hello")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert plan == []
    assert "no .dll files found" in capsys.readouterr().err


def test_non_dll_files_are_ignored(plan_fn, dll_dir, game_dir):
    (dll_dir / "a.dll").write_bytes(b"x")
    (dll_dir / "notes.txt").write_bytes(b"x")
    (dll_dir / "a.dll.bak").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert [os.path.basename(s) for s, _ in plan] == ["a.dll"]


def test_case_insensitive_dll_suffix(plan_fn, dll_dir, game_dir):
    (dll_dir / "A.DLL").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert len(plan) == 1


def test_subdirectories_are_not_recursed_into(plan_fn, dll_dir, game_dir):
    (dll_dir / "a.dll").write_bytes(b"x")
    sub = dll_dir / "nested"
    sub.mkdir()
    (sub / "b.dll").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert [os.path.basename(s) for s, _ in plan] == ["a.dll"]


def test_path_with_space_works(plan_fn, tmp_path, game_dir):
    """The real motivating case: a Downloads folder with a space in its name."""
    spaced = tmp_path / "SL 2.13"
    spaced.mkdir()
    (spaced / "sl.dlss.dll").write_bytes(b"x")
    (spaced / "sl.reflex.dll").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(spaced)}, str(game_dir))
    assert len(plan) == 2


def test_no_game_dir_is_skipped(plan_fn, dll_dir):
    (dll_dir / "a.dll").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, None)
    assert plan == []


def test_user_dir_same_as_game_dir_is_refused(plan_fn, game_dir, capsys):
    (game_dir / "a.dll").write_bytes(b"x")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(game_dir)}, str(game_dir))
    assert plan == []
    assert "same as the game" in capsys.readouterr().err


def test_never_inspects_dll_contents(plan_fn, dll_dir, game_dir):
    """Generic mechanism , no vendor/component-specific detection of any kind."""
    (dll_dir / "some_arbitrary_file.dll").write_bytes(b"not a real PE file")
    plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert len(plan) == 1


def test_plan_never_writes_to_the_filesystem(plan_fn, dll_dir, game_dir):
    (dll_dir / "a.dll").write_bytes(b"x")
    plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    assert list(game_dir.iterdir()) == []


# ── _apply_external_dll_overlay / _revert_external_dll_overlay: launch behavior ──

def test_apply_creates_symlink_when_no_collision(apply_fn, dll_dir, game_dir):
    src = dll_dir / "a.dll"
    src.write_bytes(b"x")
    dst = game_dir / "a.dll"
    applied = apply_fn([(str(src), str(dst))])
    assert applied == [str(dst)]
    assert os.path.islink(str(dst))
    assert os.readlink(str(dst)) == str(src)
    assert not (game_dir / "a.dll.gb_bak").exists()


def test_apply_backs_up_colliding_file(apply_fn, dll_dir, game_dir):
    src = dll_dir / "sl.dlss.dll"
    src.write_bytes(b"user version")
    dst = game_dir / "sl.dlss.dll"
    dst.write_bytes(b"game shipped version")
    apply_fn([(str(src), str(dst))])
    backup = game_dir / "sl.dlss.dll.gb_bak"
    assert backup.read_bytes() == b"game shipped version"
    assert os.readlink(str(dst)) == str(src)


def test_apply_never_overwrites_a_stale_backup(apply_fn, dll_dir, game_dir):
    src = dll_dir / "a.dll"
    src.write_bytes(b"new user version")
    dst = game_dir / "a.dll"
    dst.write_bytes(b"a leftover file")
    backup = game_dir / "a.dll.gb_bak"
    backup.write_bytes(b"the true original from a prior session")
    apply_fn([(str(src), str(dst))])
    assert backup.read_bytes() == b"the true original from a prior session"


def test_apply_skips_one_bad_file_without_aborting(apply_fn, dll_dir, game_dir, capsys):
    good_src = dll_dir / "good.dll"
    good_src.write_bytes(b"x")
    bad_dst = game_dir / "bad.dll"
    plan = [
        (str(dll_dir / "missing.dll"), str(game_dir / "missing.dll")),
        (str(good_src), str(game_dir / "good.dll")),
    ]
    applied = apply_fn(plan)
    assert str(game_dir / "good.dll") in applied
    assert os.path.islink(str(game_dir / "good.dll"))


def test_revert_removes_symlink_and_restores_backup(apply_fn, revert_fn, dll_dir, game_dir):
    src = dll_dir / "sl.dlss.dll"
    src.write_bytes(b"user version")
    dst = game_dir / "sl.dlss.dll"
    dst.write_bytes(b"game shipped version")
    applied = apply_fn([(str(src), str(dst))])
    revert_fn(applied)
    assert not os.path.islink(str(dst))
    assert dst.read_bytes() == b"game shipped version"
    assert not (game_dir / "sl.dlss.dll.gb_bak").exists()


def test_revert_with_no_prior_collision_leaves_no_file(apply_fn, revert_fn, dll_dir, game_dir):
    src = dll_dir / "a.dll"
    src.write_bytes(b"x")
    dst = game_dir / "a.dll"
    applied = apply_fn([(str(src), str(dst))])
    revert_fn(applied)
    assert not dst.exists()
    assert not os.path.islink(str(dst))


def test_revert_does_not_delete_a_real_file_that_replaced_the_symlink(apply_fn, revert_fn, dll_dir, game_dir):
    """A game update or a Steam integrity check may drop a real file over our
    symlink mid-session , revert must never delete that file."""
    src = dll_dir / "a.dll"
    src.write_bytes(b"x")
    dst = game_dir / "a.dll"
    applied = apply_fn([(str(src), str(dst))])
    dst.unlink()
    dst.write_bytes(b"a fresh copy the game update dropped in")
    revert_fn(applied)
    assert dst.read_bytes() == b"a fresh copy the game update dropped in"


def test_revert_is_a_no_op_on_empty_list(revert_fn):
    revert_fn([])  # must not raise


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                     reason="root bypasses the write-permission check this test relies on")
def test_read_only_game_dir_degrades_to_a_warning(plan_fn, dll_dir, game_dir, capsys):
    (dll_dir / "a.dll").write_bytes(b"x")
    os.chmod(str(game_dir), 0o555)
    try:
        plan = plan_fn({"external_dlls_enabled": True, "external_dll_dir": str(dll_dir)}, str(game_dir))
    finally:
        os.chmod(str(game_dir), 0o755)
    assert plan == []
    assert "not writable" in capsys.readouterr().err
