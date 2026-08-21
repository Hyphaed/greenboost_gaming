# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.power_baseline , put the machine back the way it was, even after a crash.

Starting a game changes real system state: CPU governors get pinned to
performance, a GPU power limit is applied, clocks are locked, swappiness is
lowered, power-profile daemons are stopped, and greenboost.ko's `gaming_mode`
is set to 1. The Proton wrapper restores all of it on the way out.

**Unless it dies hard.** `_PerfLock` keeps every saved value in memory, so a
SIGKILL, an OOM kill, or a power cut takes the only record of the original
state with it. What the user is left with, silently: CPUs pinned to
performance (fans up, idle power up), a GPU power limit that no longer matches
anything, and , the expensive one , `gaming_mode` stuck at 1, which parks
every inference T2 buffer at the eviction queue's tail indefinitely and makes
the shim keep doubling its KV reserve. None of that announces itself; it just
reads as "the box got slower".

This module makes the record survive the process:

1. **Capture before the first write.** A value read after we have already
   changed it is not a baseline.
2. **Persist to disk** as JSON *and* as a plain `sh` script, next to the
   session record `gb_gaming.game_lifecycle` writes. The script matters ,
   restoring must not depend on Python, this package, or anything importable.
3. **Arm a detached watchdog**: `while kill -0 <pid>; do sleep 5; done;
   sh restore.sh`. It outlives its parent by design.
4. **Sweep on startup**: a baseline whose owner is gone gets restored and
   reported, so a crash from yesterday does not stay applied today.

Ported from the shape GameNative uses (`PowerBaseline.kt` +
`PowerBaselineScripts`), reimplemented , that project is GPLv3 and this file
is GPL-2.0-only.

Everything here is best-effort and never raises at the caller. A failed
restore is reported, not thrown: the caller is usually already on an exit path.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

# One state directory, shared with game_lifecycle , one lifecycle owner, not
# two. Import it rather than re-deriving the path, so a change lands in one
# place.
try:
    from gb_gaming.game_lifecycle import STATE_DIR, is_alive
except Exception:                                            # pragma: no cover
    STATE_DIR = Path(
        os.environ.get("GB_GAMING_STATE_DIR")
        or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "greenboost-gaming"))

    def is_alive(pid: int) -> bool:                           # minimal fallback
        return Path(f"/proc/{pid}").exists()

#: How often the watchdog checks whether its owner is still alive. Five seconds
#: is a compromise: a shorter poll wakes the CPU more often for a process whose
#: whole job is to do nothing, a longer one leaves the box mis-tuned for longer
#: after a crash.
WATCHDOG_POLL_S = 5

#: Marker in the watchdog's own command line so it can be found and killed
#: without a pattern that also matches itself , the bracketed-glob trick.
WATCHDOG_TAG = "gb-power-watchdog"


def _state_dir() -> Path:
    # Read the env var on every call rather than caching STATE_DIR at import:
    # tests override it, and a frozen Path would silently ignore them.
    override = os.environ.get("GB_GAMING_STATE_DIR")
    return Path(override) if override else Path(STATE_DIR)


class PowerBaseline:
    """The original state of everything one game session is about to change."""

    def __init__(self, appid: str) -> None:
        self.appid = str(appid)
        self.pid = os.getpid()
        self.started_at = time.time()
        #: path -> value to write back. Insertion order is restore order.
        self.files: "dict[str, str]" = {}
        #: (argv, human note) pairs for state a file cannot express (NVML
        #: power limit, clock locks, systemd units).
        self.commands: "list[tuple[list[str], str]]" = []

    # ── capture ────────────────────────────────────────────────────────

    def capture_file(self, path: str) -> "str | None":
        """Read and remember a sysfs/proc file's CURRENT value.

        Call this immediately before writing the file, never after. Returns
        the value read, or None if the file is unreadable , in which case
        nothing is recorded, because restoring a guess is worse than not
        restoring at all.
        """
        try:
            with open(path) as fh:
                value = fh.read().strip()
        except OSError:
            return None
        # First capture wins: acquire() may touch the same knob twice, and the
        # second read would already be our own value.
        self.files.setdefault(path, value)
        return value

    def record_file(self, path: str, previous: str) -> None:
        """Record a value the caller already read for its own bookkeeping."""
        if previous is None:
            return
        self.files.setdefault(str(path), str(previous).strip())

    def record_command(self, argv: "list[str]", note: str = "") -> None:
        """Record state that is restored by running something.

        Used for the NVML power limit and clock locks, which have no file to
        write. Note that these usually need root , see `restore()`.
        """
        if argv:
            self.commands.append(([str(a) for a in argv], note))

    # ── persist ────────────────────────────────────────────────────────

    def json_path(self) -> Path:
        return _state_dir() / f"power-baseline-{self.appid}.json"

    def script_path(self) -> Path:
        return _state_dir() / f"power-restore-{self.appid}.sh"

    def persist(self) -> "Path | None":
        """Write the baseline as JSON and as a standalone restore script.

        The script is the important half: restoring must work when this
        package, this Python, or this repo is not available.
        """
        try:
            d = _state_dir()
            d.mkdir(parents=True, exist_ok=True)
            rec = {
                "appid": self.appid,
                "owner_pid": self.pid,
                "started_at": self.started_at,
                "files": self.files,
                "commands": [{"argv": a, "note": n} for a, n in self.commands],
            }
            tmp = self.json_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, indent=1))
            tmp.replace(self.json_path())        # atomic , never a half record

            script = self.script_path()
            stmp = script.with_suffix(".tmp")
            stmp.write_text(self._render_script())
            stmp.chmod(0o755)
            stmp.replace(script)
            return script
        except OSError as e:
            print(f"[power_baseline] could not persist ({e}) , a crash will "
                  "leave this session's power settings applied",
                  file=sys.stderr)
            return None

    def _render_script(self) -> str:
        lines = [
            "#!/bin/sh",
            "# Generated by gb_gaming.power_baseline , do not edit.",
            f"# Restores what game session {self.appid} changed, captured at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started_at))}.",
            "# Deliberately plain sh with no dependencies: this has to work when",
            "# the thing that wrote it is gone.",
            "failed=0",
        ]
        for path, value in self.files.items():
            lines.append(
                f"printf '%s' {shlex.quote(value)} > {shlex.quote(path)} "
                f"2>/dev/null || failed=$((failed+1))")
        for argv, note in self.commands:
            if note:
                lines.append(f"# {note}")
            lines.append(" ".join(shlex.quote(a) for a in argv)
                         + " >/dev/null 2>&1 || failed=$((failed+1))")
        lines += [
            'if [ "$failed" -gt 0 ]; then',
            '  echo "gb-power-restore: $failed item(s) could not be restored '
            '(most need root; run this script with sudo to finish)" >&2',
            "fi",
            "exit 0",
        ]
        return "\n".join(lines) + "\n"

    # ── watchdog ───────────────────────────────────────────────────────

    def arm(self, watch_pid: "int | None" = None) -> bool:
        """Spawn the detached watchdog that restores if we die without cleanup.

        `setsid` + `nohup` so it survives this process's session going away ,
        which is the entire point, since the case it exists for is this process
        being killed.
        """
        script = self.script_path()
        if not script.exists() and self.persist() is None:
            return False
        pid = int(watch_pid or self.pid)
        # The tag makes the watchdog findable later. `[g]b-power-watchdog` in
        # the pattern is what stops a pgrep from matching its own command line.
        body = (
            f"while kill -0 {pid} 2>/dev/null; do sleep {WATCHDOG_POLL_S}; done; "
            f"sh {shlex.quote(str(script))}; "
            f"rm -f {shlex.quote(str(script))} {shlex.quote(str(self.json_path()))}"
        )
        try:
            subprocess.Popen(
                ["sh", "-c", f"# {WATCHDOG_TAG} {self.appid}\n{body}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError as e:
            print(f"[power_baseline] watchdog did not start ({e}) , a crash "
                  "will leave this session's power settings applied",
                  file=sys.stderr)
            return False

    def disarm(self) -> None:
        """Clean exit: kill the watchdog and drop the baseline.

        Order matters , kill first, then remove. The other way round leaves a
        window where the watchdog notices we exited and restores state the
        normal cleanup path has already restored.
        """
        try:
            subprocess.run(
                ["pkill", "-f", f"[{WATCHDOG_TAG[0]}]{WATCHDOG_TAG[1:]} {self.appid}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass
        for p in (self.script_path(), self.json_path()):
            try:
                p.unlink()
            except OSError:
                pass


# ── startup sweep ──────────────────────────────────────────────────────

def stale_baselines() -> "list[dict]":
    """Baselines whose owning process is gone , i.e. sessions that crashed."""
    out = []
    try:
        files = sorted(_state_dir().glob("power-baseline-*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if is_alive(int(rec.get("owner_pid", 0) or 0)):
            continue
        rec["_json"] = str(f)
        rec["_script"] = str(f.with_name(f.name.replace("power-baseline-", "power-restore-")
                                          .replace(".json", ".sh")))
        out.append(rec)
    return out


def restore_stale() -> "list[dict]":
    """Restore every crashed session's baseline. Returns what was done.

    Called at Suite startup and before a new launch. Each entry carries
    `restored: bool` so the caller can tell a clean recovery from one that
    needs root , that distinction is the difference between "fixed it" and
    "tell the user one command".
    """
    done = []
    for rec in stale_baselines():
        script = rec.get("_script")
        ok = False
        if script and Path(script).exists():
            try:
                r = subprocess.run(["sh", script], capture_output=True,
                                   text=True, timeout=30)
                ok = r.returncode == 0 and "could not be restored" not in (r.stderr or "")
            except (OSError, subprocess.SubprocessError):
                ok = False
        rec["restored"] = ok
        for key in ("_json", "_script"):
            p = rec.get(key)
            if ok and p:
                try:
                    Path(p).unlink()
                except OSError:
                    pass
        done.append(rec)
        _df_emit({
            "kind": "gaming_session", "action": "state_recovered",
            "appid": rec.get("appid", ""), "owner_pid": rec.get("owner_pid"),
            "files": len(rec.get("files") or {}),
            "commands": len(rec.get("commands") or []),
            "restored": ok,
        })
    return done


# ── telemetry ──────────────────────────────────────────────────────────

_df_emit_failed_once = False


def _df_emit(event: dict) -> None:
    """Best-effort emit into core GreenBoost's shared dataflux log , same
    path and pattern as the Proton wrapper and gb_gaming.fan_daemon. Never
    raises."""
    global _df_emit_failed_once
    try:
        here = Path(__file__).resolve()
        for p in ("/usr/local/lib/greenboost",
                  str(here.parent.parent.parent / "greenboost")):
            if p not in sys.path:
                sys.path.insert(0, p)
        import gb_dataflux
        gb_dataflux.emit(event)
    except Exception as e:                                    # noqa: BLE001
        if not _df_emit_failed_once:
            _df_emit_failed_once = True
            print(f"[power_baseline] dataflux emit failed ({e}) , this "
                  "recovery will not appear in telemetry", file=sys.stderr)


def _main(argv: "list[str]") -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="gb_gaming.power_baseline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stale", help="list baselines whose owner is gone")
    sub.add_parser("restore", help="restore every crashed session's baseline")
    args = ap.parse_args(argv)
    if args.cmd == "stale":
        print(json.dumps(stale_baselines()))
    elif args.cmd == "restore":
        print(json.dumps(restore_stale()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
