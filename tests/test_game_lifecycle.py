# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Regression tests for gb_gaming.game_lifecycle.

These cover the three things that are easy to get subtly wrong and expensive
to debug later: the tree walk must survive an intermediate process exiting,
a zombie must not read as a running game, and Wine's shared infrastructure
must never be signalled.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gb_gaming import game_lifecycle as gl  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never touch the real ~/.local/state/greenboost-gaming."""
    monkeypatch.setattr(gl, "STATE_DIR", tmp_path / "state")
    yield


def _spawn_tree():
    """A launcher that forks two children and exits, leaving them orphaned.

    This is the shape a real game launcher has, and the reason the walk uses
    /proc/<pid>/task/<tid>/children rather than a PPID scan.
    """
    return subprocess.Popen(["bash", "-c", "sleep 60 & sleep 60 & wait"])


def test_descendants_finds_the_whole_tree():
    p = _spawn_tree()
    try:
        time.sleep(0.4)
        kids = gl.descendants(p.pid)
        assert len(kids) >= 2, f"expected both children, got {kids}"
    finally:
        gl.terminate_tree(p.pid, grace=1.0)
        p.wait(timeout=5)


def test_terminate_tree_is_two_stage_and_leaves_nothing():
    p = _spawn_tree()
    time.sleep(0.4)
    report = gl.terminate_tree(p.pid, grace=2.0)
    p.wait(timeout=5)
    assert report["orphans"] == [], report
    assert p.pid in report["terminated"]
    for pid in report["terminated"]:
        assert not gl.is_alive(pid)


def test_a_zombie_is_not_alive():
    """A finished child keeps its /proc entry until reaped. Reading that as
    'still running' would make every clean shutdown look like it orphaned
    processes."""
    p = subprocess.Popen(["true"])
    time.sleep(0.3)
    assert Path(f"/proc/{p.pid}").exists(), "expected a zombie to still have /proc"
    assert gl.is_alive(p.pid) is False
    p.wait()


def test_system_processes_are_never_signalled(monkeypatch):
    """wineserver and friends are shared with any other prefix running at the
    same time , signalling them would take down an unrelated game."""
    signalled = []
    monkeypatch.setattr(gl, "descendants", lambda root: [111, 222])
    monkeypatch.setattr(gl, "comm_of",
                        lambda pid: "wineserver" if pid == 111 else "game.exe")
    monkeypatch.setattr(gl, "_signal", lambda pid, sig: signalled.append((pid, sig)) or True)
    monkeypatch.setattr(gl, "is_alive", lambda pid: False)
    gl.terminate_tree(999, grace=0.0, include_root=False)
    assert 111 not in [pid for pid, _ in signalled], "wineserver must not be signalled"
    assert (222, signal.SIGTERM) in signalled


def test_session_round_trip_and_prune():
    gl.write_session("3274469611", prefix="/tmp/pfx")
    sessions = gl.read_sessions()
    assert len(sessions) == 1
    assert sessions[0]["appid"] == "3274469611"
    assert sessions[0]["wrapper_pid"] == os.getpid()
    assert sessions[0]["_alive"] is True
    # Nothing to prune while the owner lives.
    assert gl.prune_stale_sessions() == []
    gl.clear_session("3274469611")
    assert gl.read_sessions() == []


def test_prune_drops_a_record_whose_owner_died():
    """A session that died hard leaves a record behind , and that same crash
    leaves gaming_mode at 1. Cleaning the record is how the next launch knows."""
    gl.write_session("42")
    path = gl.session_path("42")
    rec = path.read_text().replace(f'"wrapper_pid": {os.getpid()}',
                                   '"wrapper_pid": 2147483646')  # never a live pid
    path.write_text(rec)
    stale = gl.prune_stale_sessions()
    assert len(stale) == 1 and stale[0]["appid"] == "42"
    assert gl.read_sessions() == []


def test_stop_game_reports_none_when_nothing_runs(monkeypatch):
    monkeypatch.setattr(gl, "find_steam_reaper", lambda appid: None)
    monkeypatch.setattr(gl, "find_wine_root", lambda appid=None: None)
    report = gl.stop_game("3274469611")
    assert report["method"] == "none"
    assert report["ok"] is True
    assert report["terminated"] == []


def test_set_child_subreaper_succeeds_on_linux():
    assert gl.set_child_subreaper() is True


# ── the reaper ────────────────────────────────────────────────────────
#
# `install_reaper` sets a process-wide SIGCHLD handler and wraps
# subprocess.Popen, so it can only be tested in a child interpreter , doing
# it here would leave pytest itself reaping its own workers' children.

REAPER_PRELUDE = f"""
import os, subprocess, sys, time
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from gb_gaming import game_lifecycle as gl
assert gl.set_child_subreaper()
assert gl.install_reaper()
"""


def _run_child(body: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", REAPER_PRELUDE + body],
                          capture_output=True, text=True, timeout=60)


def _zombie_probe() -> str:
    return """
kids = gl.child_pids(os.getpid())
z = []
for k in kids:
    try:
        st = open("/proc/%d/stat" % k).read()
    except OSError:
        continue
    if st[st.rfind(")") + 2:].split()[0] == "Z":
        z.append(k)
print("ZOMBIES=%d" % len(z))
"""


def test_adopted_orphans_are_reaped():
    """A subreaper that does not reap is a bug, not a half-measure.

    Confirmed live 2026-08-21: the Proton wrapper had adopted wineboot.exe,
    wine64-preloader and a python3, all sitting `<defunct>` under it for the
    whole session, because nothing ever called waitpid on an adopted orphan.
    """
    out = _run_child(
        'subprocess.Popen(["sh", "-c", "( sleep 0.3; exit 0 ) & exit 0"]).wait()\n'
        'time.sleep(1.5)\n' + _zombie_probe())
    assert out.returncode == 0, out.stderr
    assert "ZOMBIES=0" in out.stdout, out.stdout


def test_the_launch_we_wait_on_keeps_its_exit_status():
    """The reaper must never touch a process we started ourselves.

    Reaping the game out from under `subprocess.run` makes Popen.wait raise
    ChildProcessError, which CPython turns into returncode 0 , a crashed game
    would be recorded as a clean exit in the session summary.
    """
    out = _run_child(
        'print("RC=%d" % subprocess.run(["sh", "-c", "exit 7"]).returncode)\n'
        'print("RC2=%d" % subprocess.run(["sh", "-c", "sleep 0.2; exit 3"]).returncode)\n')
    assert out.returncode == 0, out.stderr
    assert "RC=7" in out.stdout and "RC2=3" in out.stdout, out.stdout


def test_protected_pids_do_not_accumulate():
    """Pid reuse must not let a stale entry protect a stranger , the set is
    pruned to whatever is still actually our child."""
    out = _run_child(
        'for _ in range(20): subprocess.run(["true"])\n'
        'print("TRACKED=%d" % len(gl._PROTECTED_PIDS))\n')
    assert out.returncode == 0, out.stderr
    tracked = int(out.stdout.split("TRACKED=")[1].split()[0])
    assert tracked <= 2, out.stdout
