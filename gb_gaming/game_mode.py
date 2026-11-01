# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.game_mode , quiet the desktop while a game runs, then put it back EXACTLY.

Measured on a Switch-emulator title under Proton:
a periodic gnome-shell burst from the AppIndicators extension stalls the swapchain
acquire for 80-140 ms once a minute, and an idle-blanked screen breaks a run
outright. This module lets a per-game profile name the GNOME extensions to
disable, the systemd user units to stop and whether to keep the screen awake,
for the duration of the game only.

On by default for every game: GNOME Shell extensions are switched off (the
single UserExtensionsEnabled switch, so the user's own list is untouched) and
the screen is kept awake, then both are put back when the game exits.

Profile key (top level of the per-game JSON, all optional)::

    "game_mode": {
        "enabled": true,
        "pause_extensions": true,
        "keep_screen_awake": true,
        "disable_extensions": ["ubuntu-appindicators@ubuntu.com"],
        "stop_user_units": ["hz75-guard.service"]
    }

Global switches: ``GREENBOOST_GAME_MODE=0`` turns it off everywhere;
``GREENBOOST_GAME_MODE_EXTENSIONS`` / ``GREENBOOST_GAME_MODE_KEEP_AWAKE`` set
the per-game defaults (both "1").

Inside the Steam Linux Runtime container there is no gnome-extensions,
gsettings or systemctl, so the work goes over the session bus
(gb_gaming.session_bus); the CLI tools are only the fallback for a wrapper
that runs on the host. Every launch records what it did or why it could not
in LAST_NOTES so the log never says "nothing to change" without a reason.

Restore is exact: the previous state of every item is captured BEFORE the first
write and persisted to ``STATE_DIR/game_mode.json``; only items this module
changed are undone, to the value they had. If the owner process dies hard,
``sweep_stale()`` (called at the next game start) restores from the file.
Everything is best-effort and never raises at the caller. The command runner is
injectable so tests need no real systemctl/gsettings.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

try:
    from gb_gaming.game_lifecycle import STATE_DIR, is_alive
except Exception:                                            # pragma: no cover
    STATE_DIR = Path(
        os.environ.get("GB_GAMING_STATE_DIR")
        or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "greenboost-gaming"))

    def is_alive(pid):                                       # minimal fallback
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

IDLE_SCHEMA, IDLE_KEY = "org.gnome.desktop.session", "idle-delay"
SHELL, SHELL_PATH, SHELL_EXT = "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions"
NO_BUS = object()            # sentinel: CLI tools only (tests, host runs)
_BUS = None                  # the live connection acquire() kept for release()
LAST_NOTES: list = []        # human-readable outcome of the last acquire()


def default_runner(argv):
    """Run argv, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def state_path():
    return Path(STATE_DIR) / "game_mode.json"


def _flag(env, default):
    v = os.environ.get(env)
    return default if v is None else v != "0"


def parse_config(profile):
    """Normalised config dict, or None when game mode is off.

    On by default: a profile without a "game_mode" block still pauses GNOME
    extensions and keeps the screen awake. Opt out with "enabled": false or
    GREENBOOST_GAME_MODE=0."""
    if os.environ.get("GREENBOOST_GAME_MODE", "1") == "0":
        return None
    gm = (profile or {}).get("game_mode")
    if gm is None:
        gm = {}
    if not isinstance(gm, dict) or not gm.get("enabled", True):
        return None
    cfg = {
        "pause_extensions": bool(gm.get("pause_extensions",
                                        _flag("GREENBOOST_GAME_MODE_EXTENSIONS", True))),
        "keep_screen_awake": bool(gm.get("keep_screen_awake",
                                         _flag("GREENBOOST_GAME_MODE_KEEP_AWAKE", True))),
        "disable_extensions": [str(x) for x in gm.get("disable_extensions") or []],
        "stop_user_units": [str(x) for x in gm.get("stop_user_units") or []],
    }
    if not (cfg["pause_extensions"] or cfg["keep_screen_awake"]
            or cfg["disable_extensions"] or cfg["stop_user_units"]):
        return None
    return cfg


def connect_bus():
    """Session bus connection, or None (callers then use the CLI tools)."""
    try:
        from gb_gaming import session_bus
        return session_bus.connect()
    except Exception:                                        # pragma: no cover
        return None


def _load():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None


def _save(state):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, p)


def _bus_call(bus, fn):
    """Run fn(bus); True on success, False on any bus error."""
    try:
        fn(bus)
        return True
    except Exception:
        return False


def _undo(undo, run, bus=None):
    """Undo one recorded change. Returns True when it succeeded."""
    kind = undo["kind"]
    if kind == "ext_all":
        if bus is not None and _bus_call(bus, lambda b: b.set_property(
                SHELL, SHELL_PATH, SHELL_EXT, "UserExtensionsEnabled", "b", True)):
            return True
        return run(["gsettings", "set", "org.gnome.shell",
                    "disable-user-extensions", "false"])[0] == 0
    if kind == "extension":
        if bus is not None and _bus_call(bus, lambda b: b.call(
                SHELL, SHELL_PATH, SHELL_EXT, "EnableExtension", "s", (undo["name"],))):
            return True
        argv = ["gnome-extensions", "enable", undo["name"]]
    elif kind == "unit":
        if bus is not None and _bus_call(bus, lambda b: b.call(
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager", "StartUnit", "ss",
                (undo["name"], "replace"))):
            return True
        argv = ["systemctl", "--user", "start", undo["name"]]
    elif kind == "idle":
        argv = ["gsettings", "set", IDLE_SCHEMA, IDLE_KEY, undo["value"]]
    else:
        return True
    return run(argv)[0] == 0


def _restore_state(state, run, bus=None):
    failed = []
    for u in reversed(state.get("undo", [])):
        if not _undo(u, run, bus):
            failed.append(u)
    return failed


def _pick_bus(bus):
    return _BUS if bus is NO_BUS else bus


def sweep_stale(run=default_runner, bus=NO_BUS):
    """Restore a previous session's changes if its owners are all gone.

    Returns the list of undo records restored (empty when nothing was stale).
    """
    bus = _pick_bus(bus)
    state = _load()
    if not state:
        return []
    if any(is_alive(p) for p in state.get("owners", [])):
        return []
    undo = list(state.get("undo", []))
    failed = _restore_state(state, run, bus)
    if failed:
        state["undo"], state["owners"] = failed, []
        _save(state)
    else:
        try:
            state_path().unlink()
        except OSError:
            pass
    return [u for u in undo if u not in failed]


def _extension_enabled(bus, run, uuid):
    if bus is not None:
        try:
            info = bus.call(SHELL, SHELL_PATH, SHELL_EXT, "GetExtensionInfo", "s", (uuid,))[0]
            return int(info.get("state", 0)) == 1
        except Exception:
            pass
    rc, out = run(["gnome-extensions", "list", "--enabled"])
    return rc == 0 and uuid in out.split()


def _inhibit_idle(bus):
    """Keep the screen awake until the connection closes (also on a crash)."""
    attempts = (
        ("org.gnome.SessionManager", "/org/gnome/SessionManager",
         "org.gnome.SessionManager", "Inhibit", "susu", ("GreenBoost", 0, "Game running", 8)),
        ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
         "org.freedesktop.ScreenSaver", "Inhibit", "ss", ("GreenBoost", "Game running")),
    )
    for dest, path, iface, member, sig, args in attempts:
        if _bus_call(bus, lambda b: b.call(dest, path, iface, member, sig, args)):
            return True
    return False


def acquire(cfg, owner_pid=None, run=default_runner, bus=NO_BUS):
    """Apply cfg (from parse_config). Returns the list of changes made.

    `bus` is a session_bus connection (kept open until release() so the
    screen inhibit lives exactly as long as the game), None for "no bus", or
    NO_BUS for CLI tools only. Why anything was not done goes to LAST_NOTES."""
    global _BUS
    del LAST_NOTES[:]
    if not cfg:
        return []
    if bus is not NO_BUS:
        _BUS = bus
    owner_pid = owner_pid or os.getpid()
    bus = _pick_bus(bus)
    sweep_stale(run, bus)
    state = _load() or {"owners": [], "undo": []}
    state["owners"] = [p for p in state.get("owners", []) if is_alive(p)]
    if owner_pid not in state["owners"]:
        state["owners"].append(owner_pid)
    already = {(u["kind"], u["name"]) for u in state["undo"]}
    done = []
    if bus is None and cfg["pause_extensions"]:
        LAST_NOTES.append("no session bus reachable, falling back to host tools")

    def commit(rec):                 # persist BEFORE the write it undoes
        state["undo"].append(rec)
        _save(state)

    if cfg["pause_extensions"]:
        if ("ext_all", "*") in already:
            pass
        elif bus is not None:
            try:
                on = bus.get_property(SHELL, SHELL_PATH, SHELL_EXT, "UserExtensionsEnabled")
            except Exception as e:
                on = None
                LAST_NOTES.append("GNOME Shell not reachable (%s)" % e)
            if on is True:
                commit({"kind": "ext_all", "name": "*"})
                if _bus_call(bus, lambda b: b.set_property(
                        SHELL, SHELL_PATH, SHELL_EXT, "UserExtensionsEnabled", "b", False)):
                    done.append("extensions:off")
                else:
                    state["undo"].pop()
                    LAST_NOTES.append("GNOME Shell refused to switch extensions off")
            elif on is False:
                LAST_NOTES.append("extensions were already off, left alone")
        else:
            rc, out = run(["gsettings", "get", "org.gnome.shell", "disable-user-extensions"])
            if rc == 0 and out.strip() == "false":
                commit({"kind": "ext_all", "name": "*"})
                if run(["gsettings", "set", "org.gnome.shell",
                        "disable-user-extensions", "true"])[0] == 0:
                    done.append("extensions:off")
                else:
                    state["undo"].pop()
    for name in cfg["disable_extensions"]:
        if ("extension", name) in already or ("ext_all", "*") in {(u["kind"], u["name"]) for u in state["undo"]}:
            continue
        if _extension_enabled(bus, run, name):
            commit({"kind": "extension", "name": name})
            ok = (bus is not None and _bus_call(bus, lambda b: b.call(
                SHELL, SHELL_PATH, SHELL_EXT, "DisableExtension", "s", (name,)))) \
                or run(["gnome-extensions", "disable", name])[0] == 0
            if ok:
                done.append("extension:" + name)
            else:
                state["undo"].pop()
    for name in cfg["stop_user_units"]:
        if ("unit", name) in already:
            continue
        active = False
        if bus is not None:
            try:
                rows = bus.call("org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                                "org.freedesktop.systemd1.Manager", "ListUnitsByNames",
                                "as", ([name],))[0]
                active = bool(rows) and rows[0][3] == "active"
            except Exception:
                active = run(["systemctl", "--user", "is-active", name])[0] == 0
        else:
            active = run(["systemctl", "--user", "is-active", name])[0] == 0
        if active:
            commit({"kind": "unit", "name": name})
            ok = (bus is not None and _bus_call(bus, lambda b: b.call(
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager", "StopUnit", "ss", (name, "replace")))) \
                or run(["systemctl", "--user", "stop", name])[0] == 0
            if ok:
                done.append("unit:" + name)
            else:
                state["undo"].pop()
    if cfg["keep_screen_awake"]:
        if bus is not None and _inhibit_idle(bus):
            done.append("awake:inhibit")
        elif ("idle", IDLE_KEY) not in already:
            rc, out = run(["gsettings", "get", IDLE_SCHEMA, IDLE_KEY])
            parts = out.split()
            if rc == 0 and parts and parts[-1] != "0":
                commit({"kind": "idle", "name": IDLE_KEY, "value": parts[-1]})
                if run(["gsettings", "set", IDLE_SCHEMA, IDLE_KEY, "0"])[0] == 0:
                    done.append("idle:" + parts[-1])
                else:
                    state["undo"].pop()
            elif rc != 0:
                LAST_NOTES.append("could not keep the screen awake (no session bus, no gsettings)")
    _save(state)
    return done


def release(owner_pid=None, run=default_runner, bus=NO_BUS):
    """Drop this owner; restore everything when no live owner remains.

    Returns True when the desktop is back to its recorded state.
    """
    global _BUS
    owner_pid = owner_pid or os.getpid()
    bus = _pick_bus(bus)
    state = _load()
    if not state:
        _close_bus()
        return True
    state["owners"] = [p for p in state.get("owners", [])
                       if p != owner_pid and is_alive(p)]
    if state["owners"]:
        _save(state)
        return False                  # another game still needs the quiet desktop
    failed = _restore_state(state, run, bus)
    if failed:
        state["undo"] = failed
        _save(state)
        return False
    try:
        state_path().unlink()
    except OSError:
        pass
    _close_bus()
    return True


def _close_bus():
    """Closing the connection also drops the screen inhibit."""
    global _BUS
    if _BUS is not None:
        try:
            _BUS.close()
        except Exception:
            pass
        _BUS = None
