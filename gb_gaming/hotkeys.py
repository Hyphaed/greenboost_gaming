#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.hotkeys , key/button codes, device discovery and binding storage.

Why raw evdev rather than a desktop hotkey API:

GNOME on Wayland does not hand out global hotkeys to arbitrary
applications, and even where a portal exists it stops delivering while a
game holds a fullscreen surface , exactly when a "save that replay" key
has to work.  Reading /dev/input/event* sees the key before the
compositor does, works identically on X11 and Wayland, and is the only
route that also covers a gamepad, since no desktop hotkey API binds
BTN_SOUTH.

The cost is device permission.  Membership of the `input` group is
enough; nothing here needs root.  `devices()` reports which nodes are
readable so the UI can say so plainly instead of silently never firing.

No third-party module is used.  python-evdev is not installed on the
target machine and pulling a dependency into a component that must run
before/around a game is not worth it for a 24-byte struct and three
ioctls.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import struct
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterator

CONFIG_DIR = Path.home() / ".config/greenboost-gaming"
CONFIG_PATH = CONFIG_DIR / "hotkeys.json"

# struct input_event on 64-bit Linux:
#   struct timeval { long tv_sec; long tv_usec; }  __u16 type, code; __s32 value
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03

# Key value field: 0 release, 1 press, 2 autorepeat.
VAL_RELEASE, VAL_PRESS, VAL_REPEAT = 0, 1, 2

_IOC_READ = 2


def _ioc(direction: int, typ: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _EVIOCGNAME(length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x06, length)


def _EVIOCGBIT(ev: int, length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x20 + ev, length)


# ── code tables ───────────────────────────────────────────────────────

_HEADER = Path("/usr/include/linux/input-event-codes.h")


def _load_codes() -> dict[str, int]:
    """KEY_*/BTN_* name -> numeric code.

    Parsed from the kernel's own header when present so the table cannot
    drift from the kernel this runs on.  Falls back to a small built-in
    set if linux-libc-dev is not installed, which is enough to keep the
    daemon working with default bindings.
    """
    codes: dict[str, int] = {}
    try:
        text = _HEADER.read_text()
    except OSError:
        return dict(_FALLBACK_CODES)
    # Two forms appear: a literal, or an alias to a previously defined
    # name (e.g. "#define BTN_A BTN_SOUTH").
    pat = re.compile(r"^#define\s+((?:KEY|BTN)_\w+)\s+(0x[0-9a-fA-F]+|\d+|\w+)",
                     re.M)
    for name, value in pat.findall(text):
        if value.startswith("0x"):
            codes[name] = int(value, 16)
        elif value.isdigit():
            codes[name] = int(value)
        elif value in codes:
            codes[name] = codes[value]
    codes.pop("KEY_MAX", None)
    codes.pop("KEY_CNT", None)
    return codes or dict(_FALLBACK_CODES)


# Enough to bind the defaults if the kernel header is unavailable.
_FALLBACK_CODES: dict[str, int] = {
    "KEY_LEFTCTRL": 29, "KEY_LEFTSHIFT": 42, "KEY_RIGHTSHIFT": 54,
    "KEY_LEFTALT": 56, "KEY_RIGHTCTRL": 97, "KEY_RIGHTALT": 100,
    "KEY_LEFTMETA": 125, "KEY_RIGHTMETA": 126,
    "KEY_F1": 59, "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62, "KEY_F5": 63,
    "KEY_F6": 64, "KEY_F7": 65, "KEY_F8": 66, "KEY_F9": 67, "KEY_F10": 68,
    "KEY_F11": 87, "KEY_F12": 88,
    "BTN_SOUTH": 0x130, "BTN_EAST": 0x131, "BTN_NORTH": 0x133,
    "BTN_WEST": 0x134, "BTN_TL": 0x136, "BTN_TR": 0x137,
    "BTN_TL2": 0x138, "BTN_TR2": 0x139, "BTN_SELECT": 0x13a,
    "BTN_START": 0x13b, "BTN_MODE": 0x13c,
    "BTN_THUMBL": 0x13d, "BTN_THUMBR": 0x13e,
}

CODES: dict[str, int] = _load_codes()
NAMES: dict[int, str] = {}
for _n, _c in CODES.items():
    # Aliases collide (BTN_A == BTN_SOUTH).  Prefer the canonical
    # directional name for gamepads and the shortest name otherwise, so
    # the UI shows one stable label per physical control.
    prev = NAMES.get(_c)
    if prev is None or (len(_n), _n) < (len(prev), prev):
        NAMES[_c] = _n

# Keys that only ever qualify a chord, never complete one.  A chord
# fires on the press of its non-modifier member, so Alt-then-F10 fires
# and F10-then-Alt does not, which is what every other hotkey system
# does and what muscle memory expects.  Gamepad shoulder buttons are
# included because that is how they are used in the default bindings:
# as a safety qualifier so a face button cannot fire mid-fight.
MODIFIERS: set[int] = {
    c for n, c in CODES.items()
    if n in {"KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT",
             "KEY_RIGHTSHIFT", "KEY_LEFTALT", "KEY_RIGHTALT",
             "KEY_LEFTMETA", "KEY_RIGHTMETA", "KEY_CAPSLOCK", "KEY_FN",
             "BTN_TL", "BTN_TR", "BTN_TL2", "BTN_TR2", "BTN_MODE"}
}


def is_modifier(code: int) -> bool:
    return code in MODIFIERS


# Codes that identify a device as a gamepad rather than a keyboard.
_GAMEPAD_MARKERS = {CODES.get("BTN_SOUTH"), CODES.get("BTN_GAMEPAD"),
                    CODES.get("BTN_A")} - {None}
_KEYBOARD_MARKERS = {CODES.get("KEY_A"), CODES.get("KEY_Z"),
                     CODES.get("KEY_SPACE")} - {None}


def code_of(name: str) -> int | None:
    return CODES.get(name.upper())


def name_of(code: int) -> str:
    return NAMES.get(code, f"CODE_{code}")


# ── device discovery ──────────────────────────────────────────────────

@dataclass
class InputDevice:
    path: str
    name: str
    kind: str          # "keyboard" | "gamepad" | "other"
    readable: bool


def _device_name(fd: int) -> str:
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, _EVIOCGNAME(len(buf)), buf)
    except OSError:
        return "?"
    return buf.split(b"\x00", 1)[0].decode("utf-8", "replace")


def _key_bits(fd: int) -> set[int]:
    """Every EV_KEY code the device can emit."""
    nbytes = (max(CODES.values(), default=0x2ff) // 8) + 1
    buf = bytearray(nbytes)
    try:
        fcntl.ioctl(fd, _EVIOCGBIT(EV_KEY, len(buf)), buf)
    except OSError:
        return set()
    out: set[int] = set()
    for byte_i, byte in enumerate(buf):
        if not byte:
            continue
        for bit in range(8):
            if byte & (1 << bit):
                out.add(byte_i * 8 + bit)
    return out


def _classify(keys: set[int]) -> str:
    if keys & _GAMEPAD_MARKERS:
        return "gamepad"
    if keys & _KEYBOARD_MARKERS:
        return "keyboard"
    return "other"


def devices() -> list[InputDevice]:
    """Every /dev/input/event* node, classified, with readability noted.

    Unreadable nodes are still listed.  A silent empty list would look
    identical to "no devices attached", and the actual cause is almost
    always a missing `input` group membership, which the UI should be
    able to say out loud.
    """
    out: list[InputDevice] = []
    try:
        paths = sorted(Path("/dev/input").glob("event*"),
                       key=lambda p: int(p.name[5:]) if p.name[5:].isdigit()
                       else 0)
    except OSError:
        return out
    for path in paths:
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            out.append(InputDevice(str(path), "?", "other", False))
            continue
        try:
            out.append(InputDevice(str(path), _device_name(fd),
                                   _classify(_key_bits(fd)), True))
        finally:
            os.close(fd)
    return out


def in_input_group() -> bool:
    try:
        import grp
        return grp.getgrnam("input").gr_gid in os.getgroups()
    except (KeyError, OSError, ImportError):
        return False


# ── bindings ──────────────────────────────────────────────────────────

# Every action the daemon can dispatch.  Kept here rather than in the UI
# so the daemon and the front-end cannot disagree about what exists.
ACTIONS: dict[str, str] = {
    "save_replay":        "Save the last N seconds of the replay buffer",
    "toggle_recording":   "Start or stop a full recording",
    "toggle_pause":       "Pause or resume the current recording",
    "start_replay":       "Start the replay buffer",
    "stop_replay":        "Stop the recorder",
    "screenshot":         "Take a screenshot",
    "toggle_overlay":     "Show or hide the GreenBoost overlay",
    "cycle_overlay_page": "Cycle the overlay through its pages",
    "toggle_mangohud":    "Toggle the MangoHud overlay",
}


@dataclass
class Binding:
    action: str
    # Chord of KEY_*/BTN_* names.  Fires when the last one goes down
    # while every other is already held.
    combo: list[str] = field(default_factory=list)
    # Restrict to one device kind, or "any".
    device: str = "any"       # "keyboard" | "gamepad" | "any"
    enabled: bool = True
    # Action-specific, e.g. {"seconds": 30} for save_replay.
    args: dict[str, Any] = field(default_factory=dict)

    def codes(self) -> set[int] | None:
        """Numeric chord, or None if any name is unknown on this kernel."""
        out: set[int] = set()
        for name in self.combo:
            code = code_of(name)
            if code is None:
                return None
            out.add(code)
        return out or None


@dataclass
class HotkeyConfig:
    enabled: bool = True
    bindings: list[Binding] = field(default_factory=list)

    @staticmethod
    def defaults() -> "HotkeyConfig":
        """ShadowPlay-like defaults.

        Alt+F9/F10/F11 mirror what NVIDIA's overlay uses on Windows, so
        the muscle memory transfers.  The gamepad chords all require a
        shoulder button so they cannot fire during normal play.
        """
        return HotkeyConfig(enabled=True, bindings=[
            Binding("save_replay",      ["KEY_LEFTALT", "KEY_F10"], "keyboard"),
            Binding("toggle_recording", ["KEY_LEFTALT", "KEY_F9"],  "keyboard"),
            Binding("screenshot",       ["KEY_LEFTALT", "KEY_F1"],  "keyboard"),
            Binding("toggle_overlay",   ["KEY_LEFTALT", "KEY_F11"], "keyboard"),
            Binding("cycle_overlay_page", ["KEY_LEFTALT", "KEY_F12"], "keyboard"),
            Binding("save_replay",      ["BTN_TR", "BTN_SOUTH"],    "gamepad"),
            Binding("toggle_recording", ["BTN_TR", "BTN_EAST"],     "gamepad"),
            Binding("toggle_overlay",   ["BTN_TR", "BTN_NORTH"],    "gamepad"),
        ])

    @staticmethod
    def load() -> "HotkeyConfig":
        try:
            raw = json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError):
            return HotkeyConfig.defaults()
        binds: list[Binding] = []
        for b in raw.get("bindings", []):
            if not isinstance(b, dict) or "action" not in b:
                continue
            binds.append(Binding(
                action=str(b["action"]),
                combo=[str(k) for k in b.get("combo", [])],
                device=str(b.get("device", "any")),
                enabled=bool(b.get("enabled", True)),
                args=dict(b.get("args") or {}),
            ))
        return HotkeyConfig(enabled=bool(raw.get("enabled", True)),
                            bindings=binds)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"enabled": self.enabled,
             "bindings": [asdict(b) for b in self.bindings]}, indent=2))
        tmp.replace(CONFIG_PATH)

    def conflicts(self) -> list[tuple[int, int]]:
        """Index pairs whose chord and device scope overlap.

        Two bindings on the same chord are not an error the kernel will
        report; both would simply fire.  The UI should flag them.
        """
        out: list[tuple[int, int]] = []
        for i, a in enumerate(self.bindings):
            for j, b in enumerate(self.bindings[i + 1:], start=i + 1):
                if not (a.enabled and b.enabled):
                    continue
                if sorted(a.combo) != sorted(b.combo):
                    continue
                if a.device == b.device or "any" in (a.device, b.device):
                    out.append((i, j))
        return out


def read_events(fd: int) -> Iterator[tuple[int, int, int]]:
    """(type, code, value) for every complete event available on fd."""
    try:
        data = os.read(fd, EVENT_SIZE * 64)
    except BlockingIOError:
        return
    except OSError:
        return
    for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        _, _, etype, code, value = struct.unpack_from(EVENT_FORMAT, data, off)
        yield etype, code, value
