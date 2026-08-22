# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.game_lifecycle , own the game's process tree, and stop it cleanly.

The problem this solves, plainly: until now, closing the Gaming Suite left the
game running, and nothing in the Suite could stop a game at all , there was no
SIGTERM anywhere in the codebase. Worse, if a session died hard, `gaming_mode`
stayed at 1, which parks inference T2 buffers at the LRU tail indefinitely.

Three mechanisms, tried in this order, because who owns the tree depends on who
launched it:

1. **The GreenBoost Proton wrapper**, when it launched the game. The wrapper
   declares itself a *subreaper* (`PR_SET_CHILD_SUBREAPER`), so every orphaned
   descendant re-parents to it instead of to init , which is the only reason
   "the launcher forked and exited, is the game still running?" is answerable.
   It records itself in a session file; signalling it tears the tree down.

2. **Steam's own `reaper`**, when Steam launched the game. Steam already wraps
   every launch as `reaper SteamLaunch AppId=<appid> -- <game>` and does the
   same prctl trick. Signalling Valve's reaper is more correct than
   reimplementing what it already does.

3. **A bare wine process**, as a last resort , walk its tree ourselves.

Tree enumeration uses `/proc/<pid>/task/<tid>/children`, the kernel's own child
list, never a PPID scan: the children list survives an intermediate process
exiting, a PPID scan does not.

No third-party dependencies. Every function is best-effort and never raises at
the caller; failures are reported in the returned dict.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import time
from pathlib import Path

# ── Process classes we never signal ───────────────────────────────────
#
# Wine's own infrastructure tears itself down once the game exits, and it is
# shared with any OTHER prefix running at the same time , killing wineserver
# would take down a second game that has nothing to do with this one. Same
# list Lutris maintains (util/process_watcher.py), for the same reason.
SYSTEM_PROCESSES = {
    "wineserver", "services.exe", "winedevice.exe", "plugplay.exe",
    "explorer.exe", "wineconsole", "svchost.exe", "rpcss.exe",
    "rundll32.exe", "mscorsvw.exe", "iexplore.exe", "winedbg.exe",
    "tabtip.exe", "conhost.exe",
    # Steam's own client must survive us stopping one of its games.
    "steam", "steamwebhelper", "steamerrorrepor",
}

PR_SET_CHILD_SUBREAPER = 36

# Where a live session records itself. Shared with the Proton wrapper and,
# later, the crash-safe power-baseline restore , one state directory, one
# lifecycle owner.
STATE_DIR = Path(
    os.environ.get("GB_GAMING_STATE_DIR")
    or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        / "greenboost-gaming")
)

# A tree walk is bounded so a pathological /proc can never hang the UI.
MAX_TREE_NODES = 4096


# ── subreaper ─────────────────────────────────────────────────────────

def set_child_subreaper() -> bool:
    """Declare this process a subreaper. Returns False if the kernel refused.

    Called by the Proton wrapper before it launches anything. Without it, a
    launcher that forks and exits hands its children to init and we lose the
    tree entirely.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except Exception:
        return False


# ── reaping what the subreaper adopts ─────────────────────────────────
#
# A subreaper that does not reap is a bug, not a half-measure. Confirmed live
# 2026-08-21: the Proton wrapper had adopted `wineboot.exe`, `wine64-preloader`
# and a `python3`, all sitting as `<defunct>` under it, because nothing ever
# called waitpid on an adopted orphan. Zombies hold a pid and a slot in the
# kernel's child list for the whole session, and they change what a wait on
# that tree does , which is not a state to leave a game launch in.
#
# The rule: reap ONLY processes this one adopted. Anything it deliberately
# spawned belongs to the code that spawned it , stealing the exit status of
# `subprocess.run(<the game>)` would report a crash as a clean rc=0.
_PROTECTED_PIDS: set[int] = set()
_reaper_installed = False


def protect_child(pid: int) -> None:
    """Never reap `pid` , its exit status belongs to whoever spawned it."""
    try:
        _PROTECTED_PIDS.add(int(pid))
    except (TypeError, ValueError):
        pass


def _reap_orphans(_signum=None, _frame=None) -> None:
    """Reap every adopted child that has exited. Never raises."""
    try:
        kids = set(child_pids(os.getpid()))
    except Exception:
        return
    # A protected pid stays protected only while it is still our child. Once
    # its own spawner has reaped it, it leaves the list and the entry goes
    # too , which is also what keeps pid reuse from protecting a stranger.
    _PROTECTED_PIDS.intersection_update(kids)
    for pid in kids:
        if pid in _PROTECTED_PIDS:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass


def install_reaper() -> bool:
    """Reap adopted orphans on SIGCHLD. Returns False if the kernel refused.

    Also records every process this one starts through `subprocess`, so the
    reaper can tell "the game we launched and are waiting on" from "something
    that re-parented onto us". SIGCHLD is blocked across the spawn so no
    sweep can run between the fork and the bookkeeping.
    """
    global _reaper_installed
    if _reaper_installed:
        return True
    try:
        import subprocess
        _orig_init = subprocess.Popen.__init__

        def _tracking_init(self, *args, **kwargs):
            try:
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
            except (AttributeError, OSError, ValueError):
                _orig_init(self, *args, **kwargs)
                protect_child(self.pid)
                return
            try:
                _orig_init(self, *args, **kwargs)
                protect_child(self.pid)
            finally:
                try:
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGCHLD})
                except (OSError, ValueError):
                    pass

        subprocess.Popen.__init__ = _tracking_init
        signal.signal(signal.SIGCHLD, _reap_orphans)
        _reaper_installed = True
        return True
    except Exception:
        return False


# ── /proc readers ─────────────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def comm_of(pid: int) -> str:
    return _read(f"/proc/{pid}/comm").strip()


def cmdline_of(pid: int) -> str:
    return _read(f"/proc/{pid}/cmdline").replace("\x00", " ").strip()


def environ_of(pid: int) -> dict:
    raw = _read(f"/proc/{pid}/environ")
    out = {}
    for item in raw.split("\x00"):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def is_alive(pid: int) -> bool:
    """True only for a process that is still running.

    A zombie still has a /proc entry until its parent reaps it, so a bare
    path test reports a game as alive seconds after it has actually exited ,
    which would make every clean shutdown look like it left orphans.
    """
    stat = _read(f"/proc/{pid}/stat")
    if not stat:
        return False
    close = stat.rfind(")")          # comm can contain spaces and parens
    if close < 0:
        return False
    parts = stat[close + 2:].split()
    return bool(parts) and parts[0] != "Z"


def child_pids(pid: int) -> list[int]:
    """Direct children of `pid`, from the kernel's own per-thread children list.

    Falls back to a PPID scan of /proc only when `children` is unreadable
    (CONFIG_PROC_CHILDREN off) , that fallback loses grandchildren whose
    parent already exited, which is exactly why it is not the primary path.
    """
    kids: list[int] = []
    task_dir = Path(f"/proc/{pid}/task")
    try:
        tids = list(task_dir.iterdir())
    except OSError:
        return kids
    saw_children_file = False
    for tid in tids:
        raw = _read(str(tid / "children"))
        if raw:
            saw_children_file = True
        for tok in raw.split():
            try:
                kids.append(int(tok))
            except ValueError:
                pass
    if kids or saw_children_file:
        return sorted(set(kids))
    return _child_pids_via_ppid(pid)


def _child_pids_via_ppid(pid: int) -> list[int]:
    kids = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return kids
    for name in entries:
        if not name.isdigit():
            continue
        stat = _read(f"/proc/{name}/stat")
        # comm can contain spaces and parentheses; PPID is the field after
        # the closing paren + state.
        close = stat.rfind(")")
        if close < 0:
            continue
        parts = stat[close + 2:].split()
        if len(parts) >= 2 and parts[1] == str(pid):
            kids.append(int(name))
    return sorted(kids)


def descendants(root: int) -> list[int]:
    """Every descendant of `root`, deepest-last, excluding `root` itself."""
    out: list[int] = []
    seen = {root}
    queue = [root]
    while queue and len(out) < MAX_TREE_NODES:
        cur = queue.pop(0)
        for kid in child_pids(cur):
            if kid in seen:
                continue
            seen.add(kid)
            out.append(kid)
            queue.append(kid)
    return out


# ── termination ───────────────────────────────────────────────────────

def _signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def terminate_tree(root: int, grace: float = 5.0,
                   include_root: bool = True) -> dict:
    """SIGTERM the tree, wait `grace` seconds, SIGKILL whatever is left.

    Two stages, never a blind `kill -9`: a game asked politely gets to flush
    its saves and let Wine tear itself down; only what ignores that gets
    killed. Processes in SYSTEM_PROCESSES are never signalled.
    """
    targets = descendants(root)
    if include_root:
        targets.append(root)
    targets = [p for p in targets if comm_of(p) not in SYSTEM_PROCESSES]

    termed = [p for p in targets if _signal(p, signal.SIGTERM)]

    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        if not any(is_alive(p) for p in termed):
            break
        time.sleep(0.2)

    survivors = [p for p in termed if is_alive(p)]
    killed = [p for p in survivors if _signal(p, signal.SIGKILL)]

    # One more sweep: a subreaper can have gained children *during* the
    # grace window (a launcher spawning the real game as it dies).
    late = [p for p in descendants(root) if comm_of(p) not in SYSTEM_PROCESSES]
    for p in late:
        _signal(p, signal.SIGKILL)

    time.sleep(0.2)
    orphans = [p for p in set(termed) | set(late) if is_alive(p)]
    return {
        "terminated": sorted(termed),
        "killed": sorted(set(killed) | set(late)),
        "orphans": sorted(orphans),
    }


# ── session state (written by the Proton wrapper) ─────────────────────

def session_path(appid: str) -> Path:
    return STATE_DIR / f"session-{appid}.json"


def write_session(appid: str, prefix: str = "", extra: dict | None = None) -> Path:
    """Record this process as the lifecycle owner of `appid`'s game tree."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "appid": str(appid),
        "wrapper_pid": os.getpid(),
        "prefix": prefix,
        "started_at": time.time(),
    }
    if extra:
        rec.update(extra)
    path = session_path(appid)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(path)          # atomic , a reader never sees half a record
    return path


def clear_session(appid: str) -> None:
    try:
        session_path(appid).unlink()
    except OSError:
        pass


def read_sessions() -> list[dict]:
    out = []
    try:
        files = sorted(STATE_DIR.glob("session-*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rec["_file"] = str(f)
        rec["_alive"] = is_alive(int(rec.get("wrapper_pid", 0) or 0))
        out.append(rec)
    return out


def prune_stale_sessions() -> list[dict]:
    """Drop session records whose owning process is gone.

    A stale record means a session died hard. The caller decides what else to
    repair (gaming_mode, power limits); this only cleans the bookkeeping.
    """
    stale = [r for r in read_sessions() if not r["_alive"]]
    for r in stale:
        try:
            os.unlink(r["_file"])
        except OSError:
            pass
    return stale


# ── locating the owner of a running game ──────────────────────────────

def find_steam_reaper(appid: str | None) -> int | None:
    """Steam wraps every launch as `reaper SteamLaunch AppId=<n> -- <game>`."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    fallback = None
    for name in entries:
        if not name.isdigit():
            continue
        cmd = cmdline_of(int(name))
        if "reaper" not in cmd or "SteamLaunch" not in cmd:
            continue
        if appid and f"AppId={appid}" in cmd:
            return int(name)
        fallback = int(name)
    # No appid match: only usable when there is exactly one game running,
    # which is the case the caller has to accept knowingly.
    return fallback if appid is None else None


def find_wine_root(appid: str | None = None) -> int | None:
    """Topmost wine process, optionally restricted to one prefix/appid."""
    try:
        entries = [int(n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return None
    candidates = []
    for pid in entries:
        comm = comm_of(pid)
        if not (comm.startswith("wine") and "preloader" in comm):
            continue
        if appid:
            env = environ_of(pid)
            marker = f"compatdata/{appid}/"
            if not (env.get("SteamAppId") == str(appid)
                    or env.get("STEAM_COMPAT_APP_ID") == str(appid)
                    or marker in env.get("WINEPREFIX", "")
                    or marker in env.get("STEAM_COMPAT_DATA_PATH", "")):
                continue
        candidates.append(pid)
    if not candidates:
        return None
    # The shallowest one is the closest thing to a tree root.
    return min(candidates)


# ── the public entry point ────────────────────────────────────────────

def stop_game(appid: str | None = None, grace: float = 5.0) -> dict:
    """Stop the running game, whoever launched it. Never raises.

    Returns a report naming the mechanism that was used, so the caller (and
    dataflux) can tell "the wrapper tore it down" from "we had to walk the
    tree ourselves" , they have very different reliability.
    """
    method = None
    root = None

    for rec in read_sessions():
        if appid and str(rec.get("appid")) != str(appid):
            continue
        if rec.get("_alive"):
            root, method = int(rec["wrapper_pid"]), "wrapper"
            break

    if root is None:
        pid = find_steam_reaper(appid)
        if pid:
            root, method = pid, "reaper"

    if root is None:
        pid = find_wine_root(appid)
        if pid:
            root, method = pid, "tree"

    if root is None:
        report = {"ok": True, "method": "none", "appid": appid,
                  "terminated": [], "killed": [], "orphans": [],
                  "detail": "no running game found"}
        return report

    result = terminate_tree(root, grace=grace)
    report = {"ok": not result["orphans"], "method": method, "appid": appid,
              "root": root, **result}
    _df_emit({
        "kind": "gaming_session",
        "action": "terminated",
        "appid": appid or "",
        "method": method,
        "root_pid": root,
        "pids_term": len(result["terminated"]),
        "pids_kill": len(result["killed"]),
        "orphans": len(result["orphans"]),
        "reason": os.environ.get("GB_STOP_REASON", "user_request"),
    })
    return report


# ── telemetry ─────────────────────────────────────────────────────────

_df_emit_failed_once = False


def _df_emit(event: dict) -> None:
    """Best-effort emit into core GreenBoost's shared dataflux log , same
    path and pattern as greenboost_proton/proton and gb_gaming.fan_daemon.
    One write path for gaming telemetry; never raises."""
    global _df_emit_failed_once
    try:
        here = Path(__file__).resolve()
        for p in ("/usr/local/lib/greenboost",
                  str(here.parent.parent.parent / "greenboost")):
            if p not in sys.path:
                sys.path.insert(0, p)
        import gb_dataflux
        gb_dataflux.emit(event)
    except Exception as e:                                  # noqa: BLE001
        if not _df_emit_failed_once:
            _df_emit_failed_once = True
            print(f"[game_lifecycle] dataflux emit failed ({e}) , this stop "
                  "will not appear in telemetry", file=sys.stderr)


# ── CLI (the Suite's Rust side calls this) ────────────────────────────

def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="gb_gaming.game_lifecycle")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_stop = sub.add_parser("stop", help="stop the running game")
    p_stop.add_argument("--appid", default=None)
    p_stop.add_argument("--grace", type=float, default=5.0)

    sub.add_parser("sessions", help="list recorded sessions")
    sub.add_parser("prune", help="drop session records whose owner is gone")

    args = ap.parse_args(argv)
    if args.cmd == "stop":
        print(json.dumps(stop_game(args.appid, grace=args.grace)))
    elif args.cmd == "sessions":
        print(json.dumps(read_sessions()))
    elif args.cmd == "prune":
        print(json.dumps(prune_stale_sessions()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
