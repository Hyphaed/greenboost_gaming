# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""gb_gaming.session_bus: marshalling round trips, plus one read-only live call."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gb_gaming import session_bus as sb  # noqa: E402


@pytest.mark.parametrize("sig,value", [
    ("s", "héllo"), ("u", 7), ("b", True), ("as", ["a", "bc"]),
    ("a{sv}", {"state": ("d", 1.0), "name": ("s", "x")}),
    ("(su)", ("x", 3)), ("v", ("b", False)), ("a(ssssssouso)", [("a", "b", "c", "d", "e", "f", "/o", 1, "g", "/p")]),
])
def test_marshal_round_trip(sig, value):
    w = sb._Writer()
    w.put(sig, value)
    got = sb._Reader(bytes(w.b)).get(sig)
    if sig == "a{sv}":
        value = {k: v[1] for k, v in value.items()}
    elif sig == "v":
        value = value[1]
    assert got == value


def test_alignment_matches_the_spec():
    w = sb._Writer()
    w.put("y", 1)
    w.put("u", 2)                   # must pad to a 4-byte boundary
    w.put("x", 3)                   # and 8 for 64-bit
    assert len(w.b) == 16
    assert bytes(w.b[1:4]) == b"\0\0\0"


@pytest.mark.skipif(not os.environ.get("DBUS_SESSION_BUS_ADDRESS"), reason="no session bus")
def test_live_read_only_call():
    bus = sb.connect()
    assert bus is not None and bus.name.startswith(":")
    names = bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                     "org.freedesktop.DBus", "ListNames")[0]
    assert "org.freedesktop.DBus" in names
    with pytest.raises(sb.BusError):
        bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                 "org.freedesktop.DBus", "NoSuchMethod")
    bus.close()
