#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.recorder , GPU Screen Recorder lifecycle and control.

GPU Screen Recorder (GSR, https://git.dec05eba.com) captures the screen
entirely on the GPU, so unlike OBS it costs almost nothing in-game.  This
module owns one long-lived GSR process running in *replay* mode , the
ShadowPlay model, where the last N seconds sit in a ring buffer and are
only written to disk when you ask for them.

Why one persistent process instead of spawning GSR per action:
  - Starting an encode session takes hundreds of milliseconds and briefly
    perturbs the GPU.  Doing that at the moment something worth saving
    happened is exactly the wrong time.
  - GSR can record a regular video *while* the replay buffer runs
    (-ro), so one process serves replay, manual recording and
    screenshots.  Running several would encode the screen several times.

Control path.  GSR exposes two interfaces, and we prefer the newer one:

  1. `-ipc <socket>` plus the `gsr-cli` binary.  Commands are
     acknowledged, `save-replay` reports the file it wrote, and failures
     come back as a non-zero exit with a reason on stderr.  This is what
     lets the UI say "saved to X" instead of "a signal was sent".
  2. POSIX signals (SIGUSR1 save, SIGRTMIN toggle recording, SIGUSR2
     pause, SIGINT stop).  Fire-and-forget, no acknowledgement, and
     `pkill -f` hits every GSR on the machine.  Used only when the
     running GSR is too old to have `-ipc`.

Everything here is best-effort and non-fatal: a recorder that fails must
never take a game down with it.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config/greenboost-gaming"
CONFIG_PATH = CONFIG_DIR / "recorder.json"

STATE_DIR = Path(
    os.environ.get("GB_GAMING_STATE_DIR")
    or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        / "greenboost-gaming")
)
PID_PATH = STATE_DIR / "recorder.pid"

# The IPC socket lives in the runtime dir so it dies with the session.
_RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
IPC_SOCKET = _RUNTIME / "greenboost-gsr.sock"

# GSR prints its own name in argv[0]; anchor the pkill pattern so a
# fallback signal cannot hit an unrelated process.
_PKILL_PATTERN = "^gpu-screen-recorder"


# ── configuration ─────────────────────────────────────────────────────

@dataclass
class RecorderConfig:
    """User-visible recorder settings, persisted as recorder.json.

    Defaults are chosen for this project's target machine (NVIDIA, Wayland,
    GNOME) rather than for GSR's own generic defaults.
    """
    # Capture source.  On GNOME Wayland "portal" is the only source that
    # works without KMS access, and it survives monitor hotplug.  A bare
    # monitor name (e.g. "DP-1") is faster but needs gsr-kms-server.
    capture: str = "portal"
    # Replay ring length in seconds.  60 s of 45 Mbps is roughly 340 MB,
    # which is why the buffer defaults to disk rather than RAM below.
    replay_seconds: int = 60
    framerate: int = 60
    # "auto" lets GSR pick; h264 is the safe default for browsers/Discord,
    # hevc and av1 give better quality per bit but play back poorly in
    # some tools.
    codec: str = "auto"
    audio_codec: str = "opus"
    # Constant bitrate keeps ram/disk usage predictable in high-motion
    # scenes, which matters for a ring buffer far more than for a
    # straight recording.
    bitrate_mode: str = "cbr"
    quality_kbps: int = 45000
    # "ram" is GSR's default; "disk" trades a little I/O for not holding
    # a few hundred MB of encoded video resident while you play.
    replay_storage: str = "disk"
    audio_devices: list[str] = field(default_factory=lambda: ["default_output"])
    output_dir: str = str(Path.home() / "Videos/GreenBoost")
    # Directory for manual recordings started while the replay runs.
    recording_dir: str = str(Path.home() / "Videos/GreenBoost/recordings")
    container: str = "mp4"
    # Save each replay into a folder named after the running game.  Uses
    # GSR's -df plus our own post-save script.
    date_folders: bool = False
    # Start the replay buffer automatically when a game launches.
    auto_start_on_game: bool = True
    # Frame-capture mode.  "content" syncs capture to actual screen
    # updates and avoids the vsync/capture beat that makes some games
    # look stuttery in the video; it is only available on X11 and on
    # portal capture.
    framerate_mode: str = "vfr"
    cursor: bool = True

    @staticmethod
    def load() -> "RecorderConfig":
        try:
            raw = json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError):
            return RecorderConfig()
        known = {f for f in RecorderConfig.__dataclass_fields__}
        return RecorderConfig(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(CONFIG_PATH)


# ── binary discovery ──────────────────────────────────────────────────

def gsr_path() -> str | None:
    return shutil.which("gpu-screen-recorder")


def gsr_cli_path() -> str | None:
    return shutil.which("gsr-cli")


def gsr_version() -> str | None:
    exe = gsr_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=5)
        return (out.stdout or out.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def capture_options() -> list[str]:
    """Capture sources GSR reports on this machine.

    Returns an empty list rather than raising when GSR is missing, so the
    UI can render a "not installed" state from the same call.
    """
    exe = gsr_path()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--list-capture-options"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    opts: list[str] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Monitor lines are "NAME|WIDTHxHEIGHT"; keep just the name.
        opts.append(line.split("|", 1)[0])
    return opts


def audio_devices() -> list[tuple[str, str]]:
    """(name, description) for every audio device GSR can capture."""
    exe = gsr_path()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--list-audio-devices"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    devs: list[tuple[str, str]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, desc = line.partition("|")
        devs.append((name, desc or name))
    return devs


# ── process state ─────────────────────────────────────────────────────

def _read_pid() -> int | None:
    try:
        return int(PID_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A recycled pid belonging to something else must not be treated as
    # ours; confirm the comm before believing it.
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return False
    return comm.startswith("gpu-screen-rec")


def running_pid() -> int | None:
    pid = _read_pid()
    if pid is not None and _alive(pid):
        return pid
    # Fall back to a scan so we still find a GSR started outside the app.
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x",
                              "gpu-screen-recorder"],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.split():
            return int(line)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


# ── IPC ───────────────────────────────────────────────────────────────

class RecorderError(RuntimeError):
    pass


def _ipc(*args: str, timeout: float = 20.0) -> str:
    """Send one command over the GSR IPC socket.

    Raises RecorderError with GSR's own reason on failure, so callers can
    surface a real message instead of inventing one.
    """
    cli = gsr_cli_path()
    if not cli:
        raise RecorderError("gsr-cli not installed")
    if not IPC_SOCKET.exists():
        raise RecorderError("recorder is not running")
    try:
        out = subprocess.run([cli, "-ipc", str(IPC_SOCKET), *args],
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RecorderError(f"'{args[0]}' timed out after {timeout:g}s")
    except OSError as exc:
        raise RecorderError(str(exc))
    if out.returncode != 0:
        raise RecorderError((out.stderr or out.stdout).strip()
                            or f"gsr-cli exited {out.returncode}")
    return out.stdout.strip()


def _signal_fallback(sig: int) -> None:
    """Signal the running GSR when IPC is unavailable."""
    pid = running_pid()
    if pid is None:
        raise RecorderError("recorder is not running")
    try:
        os.kill(pid, sig)
    except OSError as exc:
        raise RecorderError(str(exc))


def _has_ipc() -> bool:
    return gsr_cli_path() is not None and IPC_SOCKET.exists()


# ── commands ──────────────────────────────────────────────────────────

def build_argv(cfg: RecorderConfig) -> list[str]:
    """The exact GSR command line for a replay session.

    Kept as its own function so the UI can show the operator what will be
    run, and so it is testable without spawning anything.
    """
    exe = gsr_path() or "gpu-screen-recorder"
    argv = [exe, "-w", cfg.capture, "-f", str(cfg.framerate),
            "-c", cfg.container, "-r", str(cfg.replay_seconds),
            "-o", cfg.output_dir]
    if cfg.recording_dir:
        argv += ["-ro", cfg.recording_dir]
    if cfg.codec and cfg.codec != "auto":
        argv += ["-k", cfg.codec]
    if cfg.audio_codec:
        argv += ["-ac", cfg.audio_codec]
    for dev in cfg.audio_devices:
        if dev:
            argv += ["-a", dev]
    if cfg.bitrate_mode:
        argv += ["-bm", cfg.bitrate_mode]
        # -q means different things per bitrate mode; it is a kbps number
        # only in cbr, a quality preset name otherwise.
        if cfg.bitrate_mode == "cbr":
            argv += ["-q", str(cfg.quality_kbps)]
    if cfg.replay_storage:
        argv += ["-replay-storage", cfg.replay_storage]
    if cfg.framerate_mode:
        argv += ["-fm", cfg.framerate_mode]
    argv += ["-cursor", "yes" if cfg.cursor else "no"]
    if cfg.date_folders:
        argv += ["-df", "yes"]
    argv += ["-ipc", str(IPC_SOCKET)]
    return argv


def start_replay(cfg: RecorderConfig | None = None) -> int:
    """Start the replay buffer.  Returns the pid.  Idempotent."""
    cfg = cfg or RecorderConfig.load()
    existing = running_pid()
    if existing is not None:
        return existing
    if not gsr_path():
        raise RecorderError("gpu-screen-recorder is not installed")

    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    if cfg.recording_dir:
        Path(cfg.recording_dir).mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # A stale socket from a killed GSR blocks the new one from binding.
    IPC_SOCKET.unlink(missing_ok=True)

    log = open(STATE_DIR / "recorder.log", "ab", buffering=0)
    env = dict(os.environ)
    # Steam's launcher LD_PREFIX makes GSR lag after 30-40 minutes; GSR's
    # own README calls this out.  Clear it for our child only.
    env["LD_PREFIX"] = ""
    proc = subprocess.Popen(build_argv(cfg), stdout=log, stderr=log,
                            env=env, start_new_session=True)
    PID_PATH.write_text(str(proc.pid))

    # Wait for the socket so the first hotkey press after start does not
    # race the bind and report "not running".
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if IPC_SOCKET.exists():
            break
        if proc.poll() is not None:
            raise RecorderError(
                f"recorder exited immediately (code {proc.returncode}); "
                f"see {STATE_DIR / 'recorder.log'}")
        time.sleep(0.05)
    return proc.pid


def stop() -> None:
    """Stop the recorder, saving any in-progress manual recording."""
    if _has_ipc():
        try:
            _ipc("stop")
            PID_PATH.unlink(missing_ok=True)
            return
        except RecorderError:
            pass
    _signal_fallback(signal.SIGINT)
    PID_PATH.unlink(missing_ok=True)


def save_replay(seconds: int | None = None) -> str:
    """Write the replay buffer out.  Returns the saved file path.

    With IPC this is the real path GSR reports after the file is closed.
    Without it we can only send SIGUSR1 and have nothing to report, which
    is precisely why IPC is preferred.
    """
    if _has_ipc():
        args = ["save-replay"] + ([str(seconds)] if seconds else [])
        out = _ipc(*args, timeout=60.0)
        return out.splitlines()[-1].strip() if out else ""
    _signal_fallback(signal.SIGUSR1)
    return ""


def toggle_recording() -> None:
    """Start or stop a full recording alongside the replay buffer."""
    if _has_ipc():
        _ipc("toggle-replay-recording")
        return
    _signal_fallback(signal.SIGRTMIN)


def toggle_pause() -> None:
    if _has_ipc():
        _ipc("toggle-pause")
        return
    _signal_fallback(signal.SIGUSR2)


def status() -> dict[str, Any]:
    """Everything the UI needs to render the recorder panel in one call."""
    pid = running_pid()
    st: dict[str, Any] = {
        "installed": gsr_path() is not None,
        "cli_installed": gsr_cli_path() is not None,
        "version": gsr_version(),
        "running": pid is not None,
        "pid": pid,
        "ipc": _has_ipc(),
        "socket": str(IPC_SOCKET),
        "log": str(STATE_DIR / "recorder.log"),
        "state": "stopped",
    }
    if pid is not None and _has_ipc():
        try:
            st["state"] = _ipc("status", timeout=5.0) or "running"
        except RecorderError as exc:
            st["state"] = "running"
            st["error"] = str(exc)
    elif pid is not None:
        st["state"] = "running"
    return st


# ── CLI, so the hotkey daemon and systemd can drive this ──────────────

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="gb-recorder",
                                description="GreenBoost recorder control")
    p.add_argument("command",
                   choices=["start", "stop", "save", "toggle-recording",
                            "toggle-pause", "status", "argv"])
    p.add_argument("--seconds", type=int, default=None,
                   help="for 'save': how many seconds of the buffer to write")
    p.add_argument("--json", action="store_true")
    ns = p.parse_args(argv)

    try:
        if ns.command == "start":
            pid = start_replay()
            print(json.dumps({"pid": pid}) if ns.json else f"started pid {pid}")
        elif ns.command == "stop":
            stop()
            print("stopped")
        elif ns.command == "save":
            path = save_replay(ns.seconds)
            print(json.dumps({"path": path}) if ns.json else (path or "saved"))
        elif ns.command == "toggle-recording":
            toggle_recording()
            print("toggled recording")
        elif ns.command == "toggle-pause":
            toggle_pause()
            print("toggled pause")
        elif ns.command == "status":
            print(json.dumps(status(), indent=None if ns.json else 2))
        elif ns.command == "argv":
            print(" ".join(build_argv(RecorderConfig.load())))
    except RecorderError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
