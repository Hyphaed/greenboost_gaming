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

Profile key (top level of the per-game JSON, all optional)::

    "game_mode": {
        "enabled": true,
        "disable_extensions": ["ubuntu-appindicators@ubuntu.com"],
        "stop_user_units": ["hz75-guard.service"],
        "keep_screen_awake": true
    }

Global opt-out: ``GREENBOOST_GAME_MODE=0`` (GlobalSettings.game_mode = False).

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


def default_runner(argv):
    """Run argv, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def state_path():
    return Path(STATE_DIR) / "game_mode.json"


def parse_config(profile):
    """Return a normalised config dict, or None when game mode is off/absent."""
    if os.environ.get("GREENBOOST_GAME_MODE", "1") == "0":
        return None
    gm = (profile or {}).get("game_mode")
    if not isinstance(gm, dict) or not gm.get("enabled", True):
        return None
    cfg = {
        "disable_extensions": [str(x) for x in gm.get("disable_extensions") or []],
        "stop_user_units": [str(x) for x in gm.get("stop_user_units") or []],
        "keep_screen_awake": bool(gm.get("keep_screen_awake", False)),
    }
    if not (cfg["disable_extensions"] or cfg["stop_user_units"] or cfg["keep_screen_awake"]):
        return None
    return cfg


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


def _undo(undo, run):
    """Undo one recorded change. Returns True when it succeeded."""
    kind = undo["kind"]
    if kind == "extension":
        argv = ["gnome-extensions", "enable", undo["name"]]
    elif kind == "unit":
        argv = ["systemctl", "--user", "start", undo["name"]]
    elif kind == "idle":
        argv = ["gsettings", "set", IDLE_SCHEMA, IDLE_KEY, undo["value"]]
    else:
        return True
    return run(argv)[0] == 0


def _restore_state(state, run):
    failed = []
    for u in reversed(state.get("undo", [])):
        if not _undo(u, run):
            failed.append(u)
    return failed


def sweep_stale(run=default_runner):
    """Restore a previous session's changes if its owners are all gone.

    Returns the list of undo records restored (empty when nothing was stale).
    """
    state = _load()
    if not state:
        return []
    if any(is_alive(p) for p in state.get("owners", [])):
        return []
    undo = list(state.get("undo", []))
    failed = _restore_state(state, run)
    if failed:
        state["undo"], state["owners"] = failed, []
        _save(state)
    else:
        try:
            state_path().unlink()
        except OSError:
            pass
    return [u for u in undo if u not in failed]


def acquire(cfg, owner_pid=None, run=default_runner):
    """Apply cfg (from parse_config). Returns the list of changes made."""
    if not cfg:
        return []
    owner_pid = owner_pid or os.getpid()
    sweep_stale(run)
    state = _load() or {"owners": [], "undo": []}
    state["owners"] = [p for p in state.get("owners", []) if is_alive(p)]
    if owner_pid not in state["owners"]:
        state["owners"].append(owner_pid)
    already = {(u["kind"], u["name"]) for u in state["undo"]}
    done = []

    def commit(rec):                 # persist BEFORE the write it undoes
        state["undo"].append(rec)
        _save(state)

    if cfg["disable_extensions"]:
        rc, out = run(["gnome-extensions", "list", "--enabled"])
        enabled = set(out.split()) if rc == 0 else set()
        for name in cfg["disable_extensions"]:
            if name in enabled and ("extension", name) not in already:
                commit({"kind": "extension", "name": name})
                if run(["gnome-extensions", "disable", name])[0] == 0:
                    done.append("extension:" + name)
                else:
                    state["undo"].pop()
    for name in cfg["stop_user_units"]:
        if ("unit", name) in already:
            continue
        if run(["systemctl", "--user", "is-active", name])[0] == 0:
            commit({"kind": "unit", "name": name})
            if run(["systemctl", "--user", "stop", name])[0] == 0:
                done.append("unit:" + name)
            else:
                state["undo"].pop()
    if cfg["keep_screen_awake"] and ("idle", IDLE_KEY) not in already:
        rc, out = run(["gsettings", "get", IDLE_SCHEMA, IDLE_KEY])
        parts = out.split()
        if rc == 0 and parts and parts[-1] != "0":
            commit({"kind": "idle", "name": IDLE_KEY, "value": parts[-1]})
            if run(["gsettings", "set", IDLE_SCHEMA, IDLE_KEY, "0"])[0] == 0:
                done.append("idle:" + parts[-1])
            else:
                state["undo"].pop()
    _save(state)
    return done


def release(owner_pid=None, run=default_runner):
    """Drop this owner; restore everything when no live owner remains.

    Returns True when the desktop is back to its recorded state.
    """
    owner_pid = owner_pid or os.getpid()
    state = _load()
    if not state:
        return True
    state["owners"] = [p for p in state.get("owners", [])
                       if p != owner_pid and is_alive(p)]
    if state["owners"]:
        _save(state)
        return False                  # another game still needs the quiet desktop
    failed = _restore_state(state, run)
    if failed:
        state["undo"] = failed
        _save(state)
        return False
    try:
        state_path().unlink()
    except OSError:
        pass
    return True
