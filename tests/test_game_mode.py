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


def test_on_by_default_and_opt_outs(monkeypatch):
    # No profile at all: still pauses extensions and keeps the screen awake.
    cfg = gm.parse_config({})
    assert cfg["pause_extensions"] and cfg["keep_screen_awake"]
    assert gm.parse_config({"game_mode": {"enabled": False, "keep_screen_awake": True}}) is None
    assert gm.parse_config({"game_mode": {"pause_extensions": False, "keep_screen_awake": False}}) is None
    only = gm.parse_config({"game_mode": {"pause_extensions": False}})
    assert not only["pause_extensions"] and only["keep_screen_awake"]
    monkeypatch.setenv("GREENBOOST_GAME_MODE_EXTENSIONS", "0")
    assert not gm.parse_config({})["pause_extensions"]
    monkeypatch.setenv("GREENBOOST_GAME_MODE", "0")
    assert gm.parse_config(PROFILE) is None
    d = FakeDesktop()
    assert gm.acquire(None, run=d) == [] and d.calls == []


class FakeBus:
    """Stands in for session_bus.SessionBus: GNOME Shell, systemd, session manager."""

    def __init__(self, ext_on=True, inhibit_ok=True):
        self.ext_on = ext_on
        self.inhibit_ok = inhibit_ok
        self.extensions = {EXT: 1}
        self.inhibits = 0
        self.closed = False
        self.units = {"hz75-guard.service": "active"}

    def get_property(self, dest, path, iface, prop):
        assert prop == "UserExtensionsEnabled"
        return self.ext_on

    def set_property(self, dest, path, iface, prop, sig, value):
        assert (prop, sig) == ("UserExtensionsEnabled", "b")
        self.ext_on = value

    def call(self, dest, path, iface, member, sig="", args=()):
        if member == "Inhibit":
            if not self.inhibit_ok:
                raise RuntimeError("no session manager")
            self.inhibits += 1
            return [42]
        if member == "GetExtensionInfo":
            return [{"state": self.extensions.get(args[0], 0)}]
        if member == "DisableExtension":
            self.extensions[args[0]] = 2; return [True]
        if member == "EnableExtension":
            self.extensions[args[0]] = 1; return [True]
        if member == "ListUnitsByNames":
            n = args[0][0]
            return [[(n, "", "", self.units.get(n, "inactive"), "", "", "/", 0, "", "/")]]
        if member == "StopUnit":
            self.units[args[0]] = "inactive"; return ["/job"]
        if member == "StartUnit":
            self.units[args[0]] = "active"; return ["/job"]
        raise AssertionError(member)

    def close(self):
        self.closed = True


def test_bus_switches_extensions_off_and_restores_on_exit():
    b, d = FakeBus(), FakeDesktop()
    done = gm.acquire(gm.parse_config({}), run=d, bus=b)
    assert "extensions:off" in done and "awake:inhibit" in done
    assert b.ext_on is False and b.inhibits == 1
    assert d.calls == []                         # no host tools needed
    assert gm.release(run=d) is True
    assert b.ext_on is True and b.closed          # closing the bus drops the inhibit


def test_extensions_user_already_turned_off_are_left_alone():
    b, d = FakeBus(ext_on=False), FakeDesktop()
    done = gm.acquire(gm.parse_config({}), run=d, bus=b)
    assert "extensions:off" not in done
    assert any("already off" in n for n in gm.LAST_NOTES)
    gm.release(run=d)
    assert b.ext_on is False                      # not switched on behind the user's back


def test_bus_crash_recovery_restores_switch():
    b, d = FakeBus(), FakeDesktop()
    gm.acquire(gm.parse_config({}), owner_pid=2 ** 22 + 777, run=d, bus=b)
    assert b.ext_on is False
    b2 = FakeBus(ext_on=False)                   # next game start, new connection
    restored = gm.sweep_stale(run=d, bus=b2)
    assert [u["kind"] for u in restored] == ["ext_all"]
    assert b2.ext_on is True


def test_per_extension_and_unit_lists_over_the_bus():
    b, d = FakeBus(ext_on=False), FakeDesktop()
    cfg = gm.parse_config({"game_mode": {"pause_extensions": False, "keep_screen_awake": False,
                                         "disable_extensions": [EXT],
                                         "stop_user_units": ["hz75-guard.service"]}})
    done = gm.acquire(cfg, run=d, bus=b)
    assert sorted(done) == ["extension:" + EXT, "unit:hz75-guard.service"]
    assert b.extensions[EXT] == 2 and b.units["hz75-guard.service"] == "inactive"
    gm.release(run=d)
    assert b.extensions[EXT] == 1 and b.units["hz75-guard.service"] == "active"


def test_inhibit_failure_falls_back_to_idle_delay():
    b, d = FakeBus(inhibit_ok=False), FakeDesktop()
    done = gm.acquire(gm.parse_config({"game_mode": {"pause_extensions": False}}), run=d, bus=b)
    assert done == ["idle:300"] and d.idle == "0"
    gm.release(run=d)
    assert d.idle == "300"


def test_no_bus_and_no_tools_says_why():
    d = lambda argv: (127, "")
    gm.acquire(gm.parse_config({}), run=d, bus=None)
    assert any("no session bus" in n for n in gm.LAST_NOTES)
