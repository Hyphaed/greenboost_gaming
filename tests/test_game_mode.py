# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""gb_gaming.game_mode: exact restore, crash recovery, opt-out. No real systemctl/gsettings."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gb_gaming import game_mode as gm  # noqa: E402

EXT = "ubuntu-appindicators@ubuntu.com"
PROFILE = {"game_mode": {"disable_extensions": [EXT, "Vitals@CoreCoding.com"],
                         "stop_user_units": ["hz75-guard.service", "absent.service"],
                         "keep_screen_awake": True}}


class FakeDesktop:
    """Stands in for gnome-extensions, systemctl --user and gsettings."""

    def __init__(self):
        self.enabled = {EXT}
        self.active = {"hz75-guard.service"}
        self.idle = "300"
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["gnome-extensions", "list"]:
            return 0, "\n".join(sorted(self.enabled)) + "\n"
        if argv[:2] == ["gnome-extensions", "disable"]:
            self.enabled.discard(argv[2]); return 0, ""
        if argv[:2] == ["gnome-extensions", "enable"]:
            self.enabled.add(argv[2]); return 0, ""
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return (0 if argv[3] in self.active else 3), ""
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active.discard(argv[3]); return 0, ""
        if argv[:3] == ["systemctl", "--user", "start"]:
            self.active.add(argv[3]); return 0, ""
        if argv[:2] == ["gsettings", "get"]:
            return 0, "uint32 %s\n" % self.idle
        if argv[:2] == ["gsettings", "set"]:
            self.idle = argv[4]; return 0, ""
        return 1, ""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "STATE_DIR", tmp_path / "state")
    monkeypatch.delenv("GREENBOOST_GAME_MODE", raising=False)


def test_acquire_release_restores_exactly():
    d = FakeDesktop()
    cfg = gm.parse_config(PROFILE)
    done = gm.acquire(cfg, run=d)
    assert sorted(done) == ["extension:" + EXT, "idle:300", "unit:hz75-guard.service"]
    assert d.enabled == set() and d.active == set() and d.idle == "0"
    assert gm.state_path().exists()
    assert gm.release(run=d) is True
    assert d.enabled == {EXT} and d.active == {"hz75-guard.service"} and d.idle == "300"
    assert not gm.state_path().exists()


def test_only_changed_items_are_touched():
    d = FakeDesktop()                        # Vitals not enabled, absent.service not active
    gm.acquire(gm.parse_config(PROFILE), run=d)
    gm.release(run=d)
    assert ["gnome-extensions", "enable", "Vitals@CoreCoding.com"] not in d.calls
    assert ["systemctl", "--user", "start", "absent.service"] not in d.calls


def test_screen_already_never_blanking_is_left_alone():
    d = FakeDesktop(); d.idle = "0"
    done = gm.acquire(gm.parse_config({"game_mode": {"keep_screen_awake": True}}), run=d)
    assert done == []
    gm.release(run=d)
    assert d.idle == "0"


def test_crash_recovery_restores_on_next_start():
    d = FakeDesktop()
    gm.acquire(gm.parse_config(PROFILE), owner_pid=2 ** 22 + 12345, run=d)  # owner never alive
    assert d.idle == "0"
    restored = gm.sweep_stale(run=d)
    assert len(restored) == 3
    assert d.enabled == {EXT} and d.active == {"hz75-guard.service"} and d.idle == "300"
    assert not gm.state_path().exists()


def test_second_game_keeps_quiet_desktop_until_last_exit(monkeypatch):
    d = FakeDesktop()
    cfg = gm.parse_config(PROFILE)
    parent = os.getppid()                      # a live pid that is not us
    gm.acquire(cfg, owner_pid=os.getpid(), run=d)
    gm.acquire(cfg, owner_pid=parent, run=d)
    assert gm.release(owner_pid=os.getpid(), run=d) is False
    assert d.idle == "0"
    monkeypatch.setattr(gm, "is_alive", lambda p: False)
    assert gm.release(owner_pid=parent, run=d) is True
    assert d.idle == "300" and d.enabled == {EXT}


def test_failed_restore_is_kept_for_retry():
    d = FakeDesktop()
    gm.acquire(gm.parse_config(PROFILE), run=d)
    real = d.__call__
    fail = lambda argv: (1, "") if argv[:2] == ["gnome-extensions", "enable"] else real(argv)
    assert gm.release(run=fail) is False
    assert gm.state_path().exists()
    assert gm.release(run=d) is True and d.enabled == {EXT}


def test_opt_out_and_empty_profiles(monkeypatch):
    assert gm.parse_config({}) is None
    assert gm.parse_config({"game_mode": {"enabled": False, "keep_screen_awake": True}}) is None
    assert gm.parse_config({"game_mode": {}}) is None
    monkeypatch.setenv("GREENBOOST_GAME_MODE", "0")
    assert gm.parse_config(PROFILE) is None
    d = FakeDesktop()
    assert gm.acquire(None, run=d) == [] and d.calls == []
