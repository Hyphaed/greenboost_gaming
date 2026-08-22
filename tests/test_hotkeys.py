#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Chord-matching invariants for gb_gaming.hotkey_daemon.

These are the rules a hotkey system is judged on, and every one of them
is a bug that only shows up in someone's hands mid-game:

  - a chord fires once per press, not once per event
  - autorepeat is not a new press
  - modifier-last does not fire (F10 then Alt is not Alt+F10)
  - two keyboards cannot combine into one chord
  - a gamepad-scoped binding ignores an identical keyboard chord

The modifier-last case was a real defect caught by this file before the
daemon ever ran against a device.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gb_gaming import hotkeys as h                      # noqa: E402
from gb_gaming import hotkey_daemon as d                # noqa: E402

KB, KB2, PAD = "/dev/input/kb", "/dev/input/kb2", "/dev/input/pad"


class ChordTests(unittest.TestCase):
    def setUp(self):
        self.fired = []
        self._real_dispatch = d.dispatch
        d.dispatch = lambda action, args: self.fired.append(action)
        cfg = h.HotkeyConfig(True, [
            h.Binding("save_replay", ["KEY_LEFTALT", "KEY_F10"], "keyboard"),
            h.Binding("toggle_recording", ["BTN_TR", "BTN_SOUTH"], "gamepad"),
        ])
        self.m = d.Matcher(cfg)
        self.ALT = h.code_of("KEY_LEFTALT")
        self.F10 = h.code_of("KEY_F10")
        self.TR = h.code_of("BTN_TR")
        self.SOUTH = h.code_of("BTN_SOUTH")

    def tearDown(self):
        d.dispatch = self._real_dispatch

    def press(self, dev, code, kind="keyboard"):
        self.m.feed(dev, kind, code, h.VAL_PRESS)

    def release(self, dev, *codes, kind="keyboard"):
        for c in codes:
            self.m.feed(dev, kind, c, h.VAL_RELEASE)

    def drain(self):
        out, self.fired = list(self.fired), []
        return out

    def test_modifier_then_key_fires_once(self):
        self.press(KB, self.ALT)
        self.press(KB, self.F10)
        self.assertEqual(self.drain(), ["save_replay"])

    def test_autorepeat_does_not_refire(self):
        self.press(KB, self.ALT)
        self.press(KB, self.F10)
        self.drain()
        self.m.feed(KB, "keyboard", self.F10, h.VAL_REPEAT)
        self.m.feed(KB, "keyboard", self.F10, h.VAL_REPEAT)
        self.assertEqual(self.drain(), [])

    def test_rearms_after_release(self):
        self.press(KB, self.ALT)
        self.press(KB, self.F10)
        self.drain()
        self.release(KB, self.F10)
        self.press(KB, self.F10)
        self.assertEqual(self.drain(), ["save_replay"])

    def test_modifier_last_does_not_fire(self):
        """F10 then Alt must not trigger Alt+F10."""
        self.press(KB, self.F10)
        self.press(KB, self.ALT)
        self.assertEqual(self.drain(), [])

    def test_two_keyboards_do_not_combine(self):
        self.press(KB, self.ALT)
        self.press(KB2, self.F10)
        self.assertEqual(self.drain(), [])

    def test_gamepad_binding_ignores_keyboard(self):
        self.press(KB, self.TR)
        self.press(KB, self.SOUTH)
        self.assertEqual(self.drain(), [])

    def test_gamepad_binding_fires_on_gamepad(self):
        self.press(PAD, self.TR, kind="gamepad")
        self.press(PAD, self.SOUTH, kind="gamepad")
        self.assertEqual(self.drain(), ["toggle_recording"])

    def test_disabled_binding_never_fires(self):
        cfg = h.HotkeyConfig(True, [
            h.Binding("save_replay", ["KEY_LEFTALT", "KEY_F10"],
                      "keyboard", enabled=False)])
        m = d.Matcher(cfg)
        m.feed(KB, "keyboard", self.ALT, h.VAL_PRESS)
        m.feed(KB, "keyboard", self.F10, h.VAL_PRESS)
        self.assertEqual(self.drain(), [])


class ConfigTests(unittest.TestCase):
    def test_conflicts_detects_same_chord_any_order(self):
        cfg = h.HotkeyConfig(True, [
            h.Binding("save_replay", ["KEY_LEFTALT", "KEY_F10"], "keyboard"),
            h.Binding("screenshot", ["KEY_F10", "KEY_LEFTALT"], "any"),
        ])
        self.assertEqual(cfg.conflicts(), [(0, 1)])

    def test_no_conflict_across_device_kinds(self):
        cfg = h.HotkeyConfig(True, [
            h.Binding("save_replay", ["BTN_TR", "BTN_SOUTH"], "keyboard"),
            h.Binding("screenshot", ["BTN_TR", "BTN_SOUTH"], "gamepad"),
        ])
        self.assertEqual(cfg.conflicts(), [])

    def test_defaults_are_all_known_actions(self):
        for b in h.HotkeyConfig.defaults().bindings:
            self.assertIn(b.action, h.ACTIONS)
            self.assertIsNotNone(b.codes(),
                                 f"{b.action} has unresolvable keys {b.combo}")

    def test_defaults_have_no_conflicts(self):
        self.assertEqual(h.HotkeyConfig.defaults().conflicts(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
