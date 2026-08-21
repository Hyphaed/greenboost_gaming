# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Regression tests for gb_gaming.power_baseline.

The failure this module exists to prevent is invisible: a game session dies
hard, and the box stays pinned to performance with gaming_mode at 1 while
nothing reports it. So these tests check the crash path, not the happy one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gb_gaming import power_baseline as pb  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_GAMING_STATE_DIR", str(tmp_path / "state"))
    yield


def test_capture_before_write_wins(tmp_path):
    """A value read AFTER we changed it is not a baseline. First capture wins,
    because acquire() can touch the same knob twice."""
    f = tmp_path / "governor"
    f.write_text("powersave")
    b = pb.PowerBaseline("1")
    assert b.capture_file(str(f)) == "powersave"
    f.write_text("performance")
    b.capture_file(str(f))
    assert b.files[str(f)] == "powersave"


def test_unreadable_file_records_nothing(tmp_path):
    """Restoring a guess is worse than not restoring."""
    b = pb.PowerBaseline("1")
    assert b.capture_file(str(tmp_path / "nope")) is None
    assert b.files == {}


def test_restore_script_is_dependency_free(tmp_path):
    """It has to work when the thing that wrote it is gone , so plain sh,
    no python, no imports of this package."""
    f = tmp_path / "knob"
    f.write_text("0")
    b = pb.PowerBaseline("1")
    b.capture_file(str(f))
    script = b.persist()
    text = Path(script).read_text()
    assert text.startswith("#!/bin/sh")
    assert "python" not in text
    f.write_text("1")
    subprocess.run(["sh", str(script)], check=True)
    assert f.read_text() == "0"


def test_restore_script_quotes_hostile_values(tmp_path):
    """A value with a space or a quote must not become shell syntax."""
    f = tmp_path / "knob"
    f.write_text("4 4 1 7")
    b = pb.PowerBaseline("1")
    b.capture_file(str(f))
    script = b.persist()
    f.write_text("9 9 9 9")
    subprocess.run(["sh", str(script)], check=True)
    assert f.read_text() == "4 4 1 7"


def test_watchdog_restores_after_a_hard_kill(tmp_path):
    """The whole point: the owner dies without cleanup and the box still gets
    put back."""
    knob = tmp_path / "gaming_mode"
    knob.write_text("0")
    victim = subprocess.Popen(["sleep", "60"])
    try:
        b = pb.PowerBaseline("victim")
        b.record_file(str(knob), "0")
        b.persist()
        assert b.arm(watch_pid=victim.pid) is True
        knob.write_text("1")                     # the session's own change
        victim.kill()
        victim.wait()
        deadline = time.time() + 30
        while time.time() < deadline and knob.read_text() != "0":
            time.sleep(0.5)
        assert knob.read_text() == "0", "watchdog did not restore after a kill"
        assert not b.script_path().exists(), "watchdog must clean up after itself"
    finally:
        if victim.poll() is None:
            victim.kill()


def test_disarm_removes_the_baseline_on_a_clean_exit(tmp_path):
    knob = tmp_path / "knob"
    knob.write_text("0")
    b = pb.PowerBaseline("clean")
    b.record_file(str(knob), "0")
    b.persist()
    assert b.json_path().exists() and b.script_path().exists()
    b.disarm()
    assert not b.json_path().exists()
    assert not b.script_path().exists()


def test_stale_baseline_is_the_one_whose_owner_is_gone(tmp_path):
    b = pb.PowerBaseline("live")
    b.record_file(str(tmp_path / "x"), "0")
    b.persist()
    assert pb.stale_baselines() == []            # we are still alive

    dead = pb.PowerBaseline("dead")
    dead.pid = 2147483646                        # never a live pid
    dead.record_file(str(tmp_path / "y"), "0")
    dead.persist()
    stale = pb.stale_baselines()
    assert [r["appid"] for r in stale] == ["dead"]


def test_restore_stale_puts_state_back_and_clears_the_record(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "_df_emit", lambda ev: None)
    knob = tmp_path / "knob"
    knob.write_text("0")
    dead = pb.PowerBaseline("dead")
    dead.pid = 2147483646
    dead.record_file(str(knob), "0")
    dead.persist()
    knob.write_text("1")                         # crashed session's leftover
    done = pb.restore_stale()
    assert len(done) == 1 and done[0]["restored"] is True
    assert knob.read_text() == "0"
    assert pb.stale_baselines() == []


def test_a_restore_needing_root_is_reported_not_claimed(tmp_path, monkeypatch):
    """Half a restore must not be reported as a whole one , the user needs to
    know there is one command left to run."""
    monkeypatch.setattr(pb, "_df_emit", lambda ev: None)
    dead = pb.PowerBaseline("dead")
    dead.pid = 2147483646
    dead.record_file("/proc/sys/kernel/definitely_not_writable", "0")
    dead.persist()
    done = pb.restore_stale()
    assert len(done) == 1
    assert done[0]["restored"] is False
    # The record survives so the next sweep (or the user) can finish it.
    assert Path(done[0]["_script"]).exists()
