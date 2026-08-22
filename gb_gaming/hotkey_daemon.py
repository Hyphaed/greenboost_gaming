#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.hotkey_daemon , global keyboard and gamepad shortcuts.

Watches every readable keyboard and gamepad under /dev/input and fires
GreenBoost actions when a bound chord is pressed.  See gb_gaming.hotkeys
for why this reads evdev directly instead of asking the desktop.

Run it as a user systemd service:
    systemctl --user enable --now gb-gaming-hotkey-daemon

Behaviour worth knowing:

  Chords fire on the *completing* press.  Alt+F10 fires when F10 goes
  down while Alt is already held, not when Alt goes down afterwards.
  This matches every other hotkey system and stops a chord firing twice
  when modifiers are released in an arbitrary order.

  A chord will not repeat while held.  Key autorepeat (value 2) is
  ignored outright, and a fired chord is latched until one of its keys
  is released, so leaning on Alt+F10 saves one replay rather than
  hundreds.

  Devices are re-scanned every few seconds.  A gamepad plugged in after
  the daemon started still works, which is the normal case: people
  connect a controller when they sit down to play, not at login.

  Modifier state is tracked per device.  Two keyboards cannot combine
  into a chord, which would otherwise let a stray modifier on one
  keyboard arm a hotkey on another.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from gb_gaming import hotkeys  # noqa: E402
from gb_gaming.hotkeys import Binding, HotkeyConfig  # noqa: E402

RESCAN_INTERVAL_S = 3.0
SELECT_TIMEOUT_S = 1.0

_RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
# The Vulkan layer polls this file to decide whether to draw the overlay
# and which page to show.  A file rather than a socket so the layer never
# blocks in vkQueuePresentKHR waiting on us, and so overlay state
# survives this daemon restarting mid-game.
OVERLAY_STATE = _RUNTIME / "greenboost-overlay.state"
OVERLAY_PAGES = 4

_log_prefix = "[gb-hotkeys]"


def log(msg: str) -> None:
    print(f"{_log_prefix} {msg}", flush=True)


# ── overlay control ───────────────────────────────────────────────────

def _read_overlay() -> tuple[bool, int]:
    try:
        raw = OVERLAY_STATE.read_text().split()
        return raw[0] == "1", int(raw[1])
    except (OSError, ValueError, IndexError):
        return False, 0


def _write_overlay(visible: bool, page: int) -> None:
    """Write overlay state atomically.

    The Vulkan layer reads this from inside a game's present path.  A
    torn read there would be a graphical glitch at best, so the write is
    a rename over a temp file rather than a truncate-and-write.
    """
    try:
        OVERLAY_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OVERLAY_STATE.with_suffix(".tmp")
        tmp.write_text(f"{1 if visible else 0} {page}\n")
        tmp.replace(OVERLAY_STATE)
    except OSError as exc:
        log(f"WARN: cannot write overlay state: {exc}")


# ── actions ───────────────────────────────────────────────────────────

def _do(action: str, args: dict) -> str:
    from gb_gaming import recorder

    if action == "save_replay":
        path = recorder.save_replay(args.get("seconds"))
        return f"saved {path}" if path else "save requested"
    if action == "toggle_recording":
        recorder.toggle_recording()
        return "recording toggled"
    if action == "toggle_pause":
        recorder.toggle_pause()
        return "pause toggled"
    if action == "start_replay":
        return f"replay started (pid {recorder.start_replay()})"
    if action == "stop_replay":
        recorder.stop()
        return "recorder stopped"
    if action == "screenshot":
        # GSR takes screenshots as a separate short-lived invocation; the
        # replay process is not involved and keeps running.
        cfg = recorder.RecorderConfig.load()
        exe = recorder.gsr_path()
        if not exe:
            raise recorder.RecorderError("gpu-screen-recorder is not installed")
        out = Path(cfg.output_dir) / time.strftime("shot-%Y%m%d-%H%M%S.jpg")
        subprocess.Popen([exe, "-w", cfg.capture, "-o", str(out),
                          "--screenshot"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"screenshot {out}"
    if action == "toggle_overlay":
        vis, page = _read_overlay()
        _write_overlay(not vis, page)
        return f"overlay {'on' if not vis else 'off'}"
    if action == "cycle_overlay_page":
        vis, page = _read_overlay()
        _write_overlay(True, (page + 1) % OVERLAY_PAGES)
        return f"overlay page {(page + 1) % OVERLAY_PAGES}"
    if action == "toggle_mangohud":
        # MangoHud's own toggle key is compiled into its config; the
        # portable way to flip it for an already-running game is its
        # documented SIGUSR1 handler.
        subprocess.run(["pkill", "-SIGUSR1", "-x", "mangohud"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "mangohud toggled"
    return f"unknown action {action}"


def dispatch(action: str, args: dict) -> None:
    """Run an action without ever letting it kill the daemon."""
    try:
        log(_do(action, args))
    except Exception as exc:                      # noqa: BLE001
        log(f"WARN: {action} failed: {exc}")


# ── device set ────────────────────────────────────────────────────────

class DeviceSet:
    """Open fds for the keyboards and gamepads we care about."""

    def __init__(self) -> None:
        self.fds: dict[int, tuple[str, str]] = {}   # fd -> (path, kind)
        self._last_scan = 0.0

    def scan(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_scan < RESCAN_INTERVAL_S:
            return
        self._last_scan = now
        wanted = {d.path: d.kind for d in hotkeys.devices()
                  if d.readable and d.kind in ("keyboard", "gamepad")}
        have = {p for p, _ in self.fds.values()}

        for fd, (path, _) in list(self.fds.items()):
            if path not in wanted:
                self._close(fd)

        for path, kind in wanted.items():
            if path in have:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                log(f"WARN: cannot open {path}: {exc}")
                continue
            self.fds[fd] = (path, kind)
            log(f"watching {kind:8} {path}")

    def _close(self, fd: int) -> None:
        path = self.fds.pop(fd, ("?", "?"))[0]
        try:
            os.close(fd)
        except OSError:
            pass
        log(f"dropped {path}")

    def drop(self, fd: int) -> None:
        self._close(fd)

    def close_all(self) -> None:
        for fd in list(self.fds):
            self._close(fd)


# ── matcher ───────────────────────────────────────────────────────────

class Matcher:
    """Chord state machine, one instance per daemon."""

    def __init__(self, cfg: HotkeyConfig) -> None:
        self.reload(cfg)
        # Per-device pressed sets; see the module docstring on why this
        # is not one global set.
        self.pressed: dict[str, set[int]] = {}
        self.latched: set[int] = set()      # indexes of fired bindings

    def reload(self, cfg: HotkeyConfig) -> None:
        self.cfg = cfg
        self.compiled: list[tuple[Binding, set[int]]] = []
        for b in cfg.bindings:
            if not b.enabled:
                continue
            codes = b.codes()
            if codes is None:
                log(f"WARN: binding {b.action} has unknown keys {b.combo}, "
                    "ignored")
                continue
            if b.action not in hotkeys.ACTIONS:
                log(f"WARN: unknown action {b.action}, ignored")
                continue
            self.compiled.append((b, codes))
        log(f"{len(self.compiled)} active binding(s)")

    def feed(self, path: str, kind: str, code: int, value: int) -> None:
        if value == hotkeys.VAL_REPEAT:
            return
        held = self.pressed.setdefault(path, set())

        if value == hotkeys.VAL_RELEASE:
            held.discard(code)
            # Releasing any member of a fired chord re-arms it.
            for idx, (_, codes) in enumerate(self.compiled):
                if idx in self.latched and code in codes:
                    self.latched.discard(idx)
            return

        held.add(code)
        for idx, (b, codes) in enumerate(self.compiled):
            if idx in self.latched:
                continue
            if b.device != "any" and b.device != kind:
                continue
            if code not in codes or not codes <= held:
                continue
            # The completing press must be the chord's non-modifier key.
            # Without this, holding F10 and then tapping Alt would fire
            # Alt+F10, and every subsequent modifier press while the
            # chord is held would fire it again.  A chord made only of
            # modifiers is completed by any of them.
            triggers = {c for c in codes if not hotkeys.is_modifier(c)}
            if triggers and code not in triggers:
                continue
            self.latched.add(idx)
            dispatch(b.action, b.args)


# ── main loop ─────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="gb-hotkey-daemon")
    p.add_argument("--once", action="store_true",
                   help="scan devices, report, and exit (for diagnostics)")
    ns = p.parse_args(argv)

    if not hotkeys.in_input_group():
        log("WARNING: this user is not in the 'input' group. Global "
            "shortcuts will not fire. Fix with: "
            "sudo usermod -aG input $USER   (then log out and back in)")

    devs = DeviceSet()
    devs.scan(force=True)

    if ns.once:
        for d in hotkeys.devices():
            log(f"{d.kind:9} {'readable' if d.readable else 'NO ACCESS':10} "
                f"{d.name}  ({d.path})")
        devs.close_all()
        return 0

    if not devs.fds:
        log("no readable keyboard or gamepad found; still running, will "
            "pick devices up as they appear")

    cfg = HotkeyConfig.load()
    matcher = Matcher(cfg)

    stop = False
    reload_requested = False

    def _on_term(_s, _f):
        nonlocal stop
        stop = True

    def _on_hup(_s, _f):
        nonlocal reload_requested
        reload_requested = True

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    signal.signal(signal.SIGHUP, _on_hup)

    log("started")
    while not stop:
        if reload_requested:
            reload_requested = False
            matcher.reload(HotkeyConfig.load())
            log("config reloaded")

        devs.scan()
        if not devs.fds:
            time.sleep(SELECT_TIMEOUT_S)
            continue

        try:
            ready, _, _ = select.select(list(devs.fds), [], [],
                                        SELECT_TIMEOUT_S)
        except (OSError, ValueError):
            # A device vanished between scan and select; re-scan and retry
            # rather than dying on an unplug.
            devs.scan(force=True)
            continue
        except InterruptedError:
            continue

        for fd in ready:
            entry = devs.fds.get(fd)
            if entry is None:
                continue
            path, kind = entry
            got_any = False
            for etype, code, value in hotkeys.read_events(fd):
                got_any = True
                if etype != hotkeys.EV_KEY:
                    continue
                if not matcher.cfg.enabled:
                    continue
                matcher.feed(path, kind, code, value)
            if not got_any and not Path(path).exists():
                devs.drop(fd)

    devs.close_all()
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
