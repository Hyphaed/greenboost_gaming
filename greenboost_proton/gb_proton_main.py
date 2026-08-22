"""GreenBoost Proton , thin wrapper around upstream Steam Proton builds.

MINIMUM PYTHON: 3.9.  Steam does not run this file with the host interpreter.
It runs the compat tool inside the Steam Linux Runtime ("sniper") container
, see toolmanifest.vdf's `require_tool_appid 1628350` , whose /usr/bin/python3
is 3.9.2.  Syntax that only the host understands parses fine in development
and dies at launch, with no way for the wrapper's own error handling to help,
because a SyntaxError fires before any of it exists.  That is not theoretical:
it happened on 2026-08-20, when a PEP 701 f-string (a newline plus nested
same-type quotes inside `{...}`) took every launch down with
"SyntaxError: EOL while scanning string literal" while `py_compile` passed on
the 3.14 host.  So: no PEP 701 f-strings, no `match`, no `except*`, no
`tomllib`, no `datetime.UTC`, no `zip(strict=)`, no `slots=True`, no `Self`.
`greenboost_proton/install.sh` enforces this with a real 3.9 interpreter
before it will deploy , see `tests/test_proton_min_python.py`.

This module is loaded and executed by the `proton` stub next to it, which
survives a SyntaxError here and degrades to a bare upstream Proton launch
rather than preventing the game from starting at all.

Injects GreenBoost environment variables (virtual-VRAM Vulkan layer, Wayland,
DXR config, shader caches, per-game tweaks) into os.environ, then delegates
to the upstream Proton selected by a 'channel' sidecar file in the same directory:
  channel=stable       → latest installed stable Proton (e.g. Proton 10.0)
  channel=experimental → Proton Experimental

All hardware values are auto-detected at runtime , nothing is hard-coded.
"""

import ctypes
import ctypes.util
import errno
import functools
import glob
import json
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time


# ── PR-GGGG: nvidia-smi resolution ─────────────────────────────────────────
# shutil.which("nvidia-smi") alone isn't reliable here: confirmed live
# 2026-08-07 that a real Khazan session logged "nvidia-smi not on PATH (GPU
# perf lock skipped)" even though /usr/bin/nvidia-smi exists on the host ,
# the wrapper's own PATH just doesn't happen to include /usr/bin in every
# launch context. Falls back through the common install locations and
# pressure-vessel's host filesystem passthrough (/run/host) before giving up.
@functools.lru_cache(maxsize=1)
def _resolve_nvidia_smi():
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in (
        "/usr/bin/nvidia-smi",
        "/usr/local/bin/nvidia-smi",
        "/run/host/usr/bin/nvidia-smi",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

# ── PR-GGGG: log rotation ─────────────────────────────────────────────────────
# Removes Proton / DXVK / VKD3D log files older than GREENBOOST_LOG_TTL_DAYS
# (default 14).  Keeps the log dir small on systems where the user routinely
# launches games with PROTON_LOG=1; without this the directory grows
# unboundedly because Proton's own rotation only runs when the user manually
# clears it.

def _rotate_proton_logs():
    ttl_days = int(os.environ.get("GREENBOOST_LOG_TTL_DAYS", "14"))
    if ttl_days <= 0:
        return
    log_dir = os.path.expanduser("~/.local/share/greenboost/proton-logs")
    if not os.path.isdir(log_dir):
        return
    cutoff = time.time() - (ttl_days * 86400)
    removed = 0
    for entry in os.listdir(log_dir):
        path = os.path.join(log_dir, entry)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        sys.stderr.write(
            f"[greenboost-proton] rotated {removed} log file(s) older than "
            f"{ttl_days} days from {log_dir}\n")


# ── B3: per-game JSON profile loader ─────────────────────────────────────────
# Schema: ~/.config/greenboost-gaming/per-game/<AppID>.json
# {
#   "env": {"KEY": "val"},
#   "wrappers": {"gamemode": true, "mangohud": true,
#                "gamescope": ["-W","1920","-H","1080","-r","120","-f"]},
#   "nis": {"enabled": true, "sharpness": 0.6, "scale": 0.77},
#   "hdr": true,
#   "fps_cap": 120,
#   "governor": "performance",
#   "hooks": {"pre": ["/path/script.sh"], "post": []}
# }
# Merge order: process env > per-game JSON env > per-game .env > global_settings

def _load_per_game_json(gameid):
    """Return (profile_dict | None).  Never raises."""
    if not gameid:
        return None
    path = os.path.expanduser(
        f"~/.config/greenboost-gaming/per-game/{gameid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            profile = json.load(f)
        sys.stderr.write(f"[greenboost-proton] per-game JSON profile loaded: {path}\n")
        return profile
    except Exception as e:
        sys.stderr.write(f"[greenboost-proton] per-game JSON parse error ({path}): {e}\n")
        return None


def _activate_gpu_profile(name):
    """Signal the fan daemon to follow <name> and optionally apply power limit via pkexec."""
    _cfg = os.path.expanduser("~/.config/greenboost-gaming")
    # 1. Write active_profile.json so the fan daemon picks up the curve.
    try:
        os.makedirs(_cfg, exist_ok=True)
        _ap = os.path.join(_cfg, "active_profile.json")
        _tmp = _ap + ".tmp"
        import json as _json
        with open(_tmp, "w", encoding="utf-8") as _f:
            _json.dump({"name": name}, _f)
        os.replace(_tmp, _ap)
        sys.stderr.write(f"[greenboost-proton] GPU profile '{name}' → fan daemon\n")
    except Exception as _e:
        sys.stderr.write(f"[greenboost-proton] active_profile write failed: {_e}\n")
    # 2. Try to apply the power limit via pkexec + nvml_control.py (Wayland-safe).
    _profile_path = os.path.join(_cfg, "profiles", f"{name}.json")
    if not os.path.isfile(_profile_path):
        return
    try:
        import json as _json
        _pd = _json.loads(open(_profile_path, encoding="utf-8").read())
        _watts = _pd.get("power_limit_w")
        if _watts and _watts > 0:
            # See the matching comment on the other nvml_control.py caller
            # below (GPU power lock) , os.path.isfile() on either candidate
            # is unreliable from inside pressure-vessel, so attempt the
            # call directly rather than pre-checking.
            for _ctrl in [
                os.path.expanduser("~/.local/lib/greenboost-gaming/gb_gaming/nvml_control.py"),
                "/usr/local/lib/greenboost-gaming/gb_gaming/nvml_control.py",
            ]:
                import subprocess as _sp
                try:
                    _r = _sp.run(
                        ["pkexec", "python3", _ctrl, "set-power", str(int(_watts))],
                        capture_output=True, timeout=10)
                except (_sp.TimeoutExpired, FileNotFoundError, OSError) as _pe:
                    sys.stderr.write(
                        f"[greenboost-proton] power limit skipped ({_pe})\n")
                    continue
                if _r.returncode == 0:
                    sys.stderr.write(
                        f"[greenboost-proton] GPU profile '{name}': "
                        f"power limit → {int(_watts)} W\n")
                    break
                else:
                    sys.stderr.write(
                        f"[greenboost-proton] power limit skipped "
                        f"(pkexec: {_r.returncode})\n")
    except Exception as _e:
        sys.stderr.write(f"[greenboost-proton] GPU profile power limit failed: {_e}\n")


def _apply_per_game_json_env(profile):
    """Apply env dict from per-game JSON profile (setdefault , explicit exports win)."""
    if not profile:
        return
    env_block = profile.get("env") or {}
    applied = 0
    for key, val in env_block.items():
        if key and key not in os.environ:
            os.environ[key] = str(val)
            applied += 1
    # NIS shortcuts
    nis = profile.get("nis") or {}
    if nis.get("enabled"):
        os.environ.setdefault("GREENBOOST_NIS", "1")
        os.environ.setdefault("GREENBOOST_NIS_DISPATCH", "1")
    if "sharpness" in nis:
        os.environ.setdefault("GREENBOOST_NIS_SHARPNESS", str(nis["sharpness"]))
    if "scale" in nis:
        os.environ.setdefault("GREENBOOST_NIS_SCALE", str(nis["scale"]))
    if profile.get("hdr"):
        os.environ.setdefault("ENABLE_HDR_WSI", "1")
    if profile.get("fps_cap"):
        os.environ.setdefault("DXVK_FRAME_RATE", str(profile["fps_cap"]))
    if profile.get("dlss_preset"):
        preset = profile["dlss_preset"]
        os.environ.setdefault("DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE", "on")
        os.environ.setdefault(
            "DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION", preset)
    if profile.get("reflex"):
        os.environ.setdefault("GREENBOOST_REFLEX", "1")
    if profile.get("governor") == "performance":
        os.environ.setdefault("GREENBOOST_PERF_LOCK", "1")
    # Per-game overrides for global runtime settings (null=use global, bool=force).
    for _field, _envvar in (
        ("gplasync",           "GREENBOOST_GPLASYNC"),
        ("perf_lock",          "GREENBOOST_PERF_LOCK"),
        ("compositor_suspend", "GREENBOOST_COMPOSITOR_SUSPEND"),
        ("vk_pipeline_cache",  "GREENBOOST_VK_PIPELINE_CACHE"),
    ):
        _v = profile.get(_field)
        if _v is not None:
            os.environ[_envvar] = "1" if _v else "0"
    if applied:
        sys.stderr.write(f"[greenboost-proton] per-game JSON env: {applied} vars applied\n")
    gpu_profile_name = profile.get("gpu_profile", "")
    if gpu_profile_name:
        _activate_gpu_profile(gpu_profile_name)


def _build_launch_argv(base_argv, profile):
    """Prepend gamescope / mangohud / gamemode wrappers from per-game JSON (B4).

    mangohud is checked BEFORE the no-profile early return (unlike
    gamemode/gamescope, which stay per-game-only) , GREENBOOST_MANGOHUD_DEFAULT
    is the Global Settings "performance overlay" toggle, and most games never
    get a per-game override file at all, so gating this on `profile` existing
    would make the global toggle silently do nothing for the common case.
    A per-game wrappers.mangohud of true forces it on even if the global
    default is off; explicit false in a per-game profile forces it off even
    if the global default is on.
    """
    argv = list(base_argv)
    wrappers = (profile or {}).get("wrappers") or {}

    per_game_mangohud = wrappers.get("mangohud")
    want_mangohud = (
        per_game_mangohud if per_game_mangohud is not None
        else os.environ.get("GREENBOOST_MANGOHUD_DEFAULT", "0") == "1"
    )
    if want_mangohud:
        # This wrapper always runs the game inside Proton/pressure-vessel, so
        # the argv-prepend trick ("mangohud --") never worked: shutil.which()
        # resolves the binary against the HOST PATH, but the game execs
        # inside the container, which has no host mangohud binary on its own
        # PATH. Confirmed live 2026-08-07 , the toggle was silently a no-op.
        # MangoHud ships as an implicit Vulkan layer gated on the MANGOHUD
        # env var, the same mechanism GreenBoost's own layer uses and Valve's
        # documented way to enable it under Proton , set the env var instead
        # of prepending the binary.
        os.environ.setdefault("MANGOHUD", "1")
        os.environ.setdefault("MANGOHUD_CONFIG",
            "fps,frametime,frame_timing=1,gpu_stats,gpu_temp,gpu_power,"
            "cpu_stats,cpu_temp,ram,vram,vulkan_driver,wine,engine_version,"
            "position=top-left,background_alpha=0.5,font_size=20")
        # T1/T2/T3 pool occupancy in the overlay (enhance_gaming.md C4) ,
        # pool_brief is already a formatted, human-readable line; MangoHud's
        # exec= directive is the real, documented mechanism for showing
        # live external command output, so this reuses that file directly
        # instead of duplicating its formatting or patching MangoHud.
        # NO shell fallback text inside the exec= value.
        #
        # This used to append "|| echo 'GreenBoost: kmod not loaded'". MangoHud
        # parses MANGOHUD_CONFIG as a delimited key=value list and cannot carry
        # a quoted sentence inside a value, so every launch produced a burst of
        #   [MANGOHUD] [error] Unknown option 'kmod not loaded''
        # measured 2026-08-21, ~10 per launch, on a machine where the module WAS
        # loaded , the host check below passes and the fallback only fires
        # inside the Proton sandbox, where /sys is not visible.
        #
        # `cat ... 2>/dev/null` already prints nothing when the file cannot be
        # read, which is the right overlay behaviour anyway: a HUD line reading
        # "kmod not loaded" is noise, not information, and it was wrong here.
        if os.path.exists("/sys/class/greenboost/greenboost/pool_brief"):
            os.environ["MANGOHUD_CONFIG"] += (
                ",exec=cat /sys/class/greenboost/greenboost/pool_brief 2>/dev/null"
            )

    if not profile:
        return argv

    # gamemode: prepend 'gamemoderun' if present and executable
    if wrappers.get("gamemode") and shutil.which("gamemoderun"):
        argv = ["gamemoderun"] + argv

    # gamescope: prepend 'gamescope [args] -- argv'
    gamescope_args = wrappers.get("gamescope")
    if gamescope_args and shutil.which("gamescope"):
        if not isinstance(gamescope_args, list):
            gamescope_args = []
        argv = ["gamescope"] + gamescope_args + ["--"] + argv

    return argv


# ── B5: pre/post launch hooks ─────────────────────────────────────────────────
# Scripts in ~/.config/greenboost-gaming/hooks/pre.d/* run before the game.
# Scripts in ~/.config/greenboost-gaming/hooks/post.d/* run after it exits.
# Env exports available to hooks: STEAM_APPID, GREENBOOST_GAME_NAME,
#   STEAM_COMPAT_DATA_PATH, and the full inherited environment.

_df_emit_failed_once = False  # log the import/emit failure once per process, not every call


# ── Game-tree ownership (close-the-Suite-closes-the-game) ───────────────
#
# This wrapper is the only process that is genuinely the parent of the game
# tree, so it is where the tree is owned. It declares itself a SUBREAPER, so
# a launcher that forks the real game and exits hands its children to US
# rather than to init , without that, "is the game still running?" is not
# answerable and "stop the game" has nothing to signal.
#
# The walk, the exclusion list and the two-stage kill live in
# gb_gaming.game_lifecycle so the Suite's own stop path and this wrapper
# share ONE implementation.

_gl = None            # gb_gaming.game_lifecycle, or None if unavailable
_gl_session_appid = None


def _lifecycle():
    """Import gb_gaming.game_lifecycle once, best-effort. Never raises.

    If it is unavailable the wrapper still works exactly as before , it just
    cannot be asked to stop the game from outside, and says so once."""
    global _gl
    if _gl is not None:
        return _gl or None
    try:
        for _p in (os.path.expanduser("~/.local/lib/greenboost-gaming"),
                   "/usr/local/lib/greenboost-gaming",
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from gb_gaming import game_lifecycle as _mod
        _gl = _mod
    except Exception as _e:                     # noqa: BLE001
        _gl = False
        sys.stderr.write(
            f"[greenboost-proton] game lifecycle helper unavailable ({_e}) , "
            "the game still runs normally, but closing the Suite will not be "
            "able to stop it.\n")
    return _gl or None


def _own_game_tree(gameid: str, prefix: str = "") -> None:
    """Become the subreaper for this launch and record it for the Suite."""
    global _gl_session_appid
    gl = _lifecycle()
    if gl is None:
        return
    if gl.set_child_subreaper():
        # Adopting orphans without reaping them leaves `<defunct>` entries
        # piled under this process for the whole session (seen live
        # 2026-08-21: wineboot.exe, wine64-preloader, python3). The reaper
        # never touches the pids we launched ourselves , see install_reaper.
        if not gl.install_reaper():
            sys.stderr.write(
                "[greenboost-proton] could not install the child reaper , "
                "processes this launch adopts will show as <defunct> until "
                "the game exits. The game itself is unaffected.\n")
    else:
        sys.stderr.write(
            "[greenboost-proton] kernel refused PR_SET_CHILD_SUBREAPER , if "
            "this game's launcher forks and exits, stopping it from the Suite "
            "may leave parts of it running. Nothing else is affected.\n")
    try:
        gl.write_session(str(gameid), prefix=prefix)
        _gl_session_appid = str(gameid)
    except Exception as _e:                     # noqa: BLE001
        sys.stderr.write(f"[greenboost-proton] could not record session: {_e}\n")


def _release_game_tree() -> None:
    gl = _lifecycle()
    if gl is not None and _gl_session_appid:
        gl.clear_session(_gl_session_appid)


def _install_stop_handler() -> None:
    """SIGTERM/SIGINT stops the whole game tree, then unwinds normally.

    The Suite signals THIS process when the user closes it. Re-raising as
    KeyboardInterrupt is deliberate: it lets the existing `finally` block run,
    so the perf lock, the compositor, gaming_mode and the session summary are
    all restored exactly as on a normal exit."""
    def _on_stop(signum, _frame):
        gl = _lifecycle()
        if gl is not None:
            os.environ.setdefault("GB_STOP_REASON", "signal")
            try:
                rep = gl.terminate_tree(os.getpid(), grace=5.0, include_root=False)
                sys.stderr.write(
                    "[greenboost-proton] stopping game , "
                    f"{len(rep['terminated'])} process(es) asked to exit, "
                    f"{len(rep['killed'])} force-killed\n")
            except Exception as _e:             # noqa: BLE001
                sys.stderr.write(f"[greenboost-proton] stop failed: {_e}\n")
        raise KeyboardInterrupt(f"signal {signum}")
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _on_stop)
        except (ValueError, OSError):
            pass


def _df_emit(event: dict) -> None:
    """Best-effort emit into core GreenBoost's shared dataflux log
    (~/.local/share/greenboost/dataflux.jsonl) , the same stream gb_quant,
    gb_cluster, and tier moves write to, so a game session shows up
    alongside whatever inference activity is happening on the same GPU at
    the same time. Never raises; gaming must never depend on core being
    importable from this exact interpreter.

    Confirmed live 2026-08-07: this previously swallowed every exception
    silently, so when gb_dataflux failed to import (neither candidate path
    existed / wasn't on this interpreter's sys.path), every gaming_session
    event vanished with zero log output , sessions.jsonl doesn't exist
    either (the old direct-write path was removed), so intelligence
    strategy #1 (VRAM-risk badge, analyze_game_sessions) had no data
    source at all and nothing said why. Log once per process instead."""
    global _df_emit_failed_once
    try:
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        for _p in (
            "/usr/local/lib/greenboost",
            os.path.join(_script_dir, "..", "..", "greenboost"),
        ):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import gb_dataflux
        gb_dataflux.emit(event)
        return
    except Exception as _e:
        _import_err = _e

    # Fallback: append the event ourselves, matching gb_dataflux.emit()'s
    # own file (~/.local/share/greenboost/dataflux.jsonl) and minimal shape
    # (ts, pid). Confirmed live 2026-08-08: from inside Steam's
    # pressure-vessel container, NEITHER `/usr/local/lib/greenboost` (real
    # on the host, but pressure-vessel does not bind-mount /usr/local at
    # all) NOR the relative "../../greenboost" fallback (only valid when
    # running this exact file straight out of a sibling dev checkout, not
    # a deployed compat-tool copy) ever resolves , every gaming_session
    # event was silently dropped, every launch, regardless of whether core
    # GreenBoost was even installed. `~/.local/share/greenboost/` itself
    # IS reachable from in here (this file's own log tee already proves
    # it), so a dependency-free append there recovers the data without
    # needing the cross-repo import to work at all. No rotation/gzip (that
    # lives in gb_dataflux itself) , just don't lose the event.
    try:
        _home = os.environ.get("HOME", os.path.expanduser("~"))
        _log_path = os.path.join(_home, ".local", "share", "greenboost", "dataflux.jsonl")
        os.makedirs(os.path.dirname(_log_path), exist_ok=True)
        _event = dict(event)
        _event.setdefault("ts", time.time())
        _event.setdefault("pid", os.getpid())
        with open(_log_path, "a") as _f:
            _f.write(json.dumps(_event) + "\n")
    except Exception:
        pass  # best-effort fallback of a best-effort emit , never raise

    if not _df_emit_failed_once:
        _df_emit_failed_once = True
        sys.stderr.write(
            f"[greenboost-proton] gb_dataflux not importable ({_import_err}) "
            ", wrote this session's events directly to "
            "~/.local/share/greenboost/dataflux.jsonl instead (no rotation, "
            "same as core writes for VRAM-risk badge / session history)\n"
        )


_POOL_BRIEF_RE = re.compile(
    r"T1:(\d+)GB T2:(\d+)/(\d+)GB\((\d+)%\) T3:(\d+)/(\d+)GB "
    r"PRESSURE:(\w+) KV_RSV:(\d+)MB KV_T2:(\d+)MB")

# `pool_brief` truncates T2/T3 to integer GB (greenboost.c's pool_brief_show()
# does `t2_alloc_mb / 1024`), so a sub-1GB spill reads as "0". The companion
# `status` sysfs file carries the same figures at MB precision , mirrors
# read_status_mb() in src/src-tauri/src/live_stats.rs.
_STATUS_MB_RE = re.compile(
    r"^\s*(T2 allocated|T2 available|T3 allocated)\s*:\s*(\d+)\s*MB", re.M)


def _read_status_mb():
    """Returns (t2_alloc_mb, t3_alloc_mb), each None if its line is absent
    or the file can't be read , never raises."""
    try:
        with open("/sys/class/greenboost/greenboost/status") as f:
            text = f.read()
    except OSError:
        return None, None
    t2_alloc = t3_alloc = None
    for label, val in _STATUS_MB_RE.findall(text):
        if label == "T2 allocated":
            t2_alloc = int(val)
        elif label == "T3 allocated":
            t3_alloc = int(val)
    return t2_alloc, t3_alloc


def _check_t2t3_pressure(gameid, gpu_name):
    """Read the kernel module's pool_brief sysfs file and, if the shim is
    actively spilling weights/textures to T2 DDR or T3 NVMe *during this
    game session*, emit a gaming_vram_pressure dataflux event. Format is
    documented verbatim in greenboost.c's pool_brief_show() comment:
      "T1:12GB T2:8/51GB(15%) T3:0/128GB PRESSURE:ok KV_RSV:2048MB KV_T2:512MB"
    Silent no-op when the module isn't loaded (pool_brief absent) , this is
    supplementary telemetry, never a gate on the game launching.

    Gates on the MB-precision `status` figures, not the GB-truncated
    `pool_brief` ones , with GB truncation a real sub-1GB spill would
    silently never emit an event at all (t2_alloc_gb == 0 forever).

    Returns (t2_alloc_mb, t3_alloc_mb) , each may be None (status unreadable)
    or 0 (nothing spilled) , so the peak-VRAM tracker thread can fold T2
    spill into the session summary without re-reading the file itself."""
    try:
        with open("/sys/class/greenboost/greenboost/pool_brief") as f:
            line = f.read().strip()
    except OSError:
        return None, None
    m = _POOL_BRIEF_RE.match(line)
    if not m:
        return None, None
    (_t1_gb, t2_alloc_gb, _t2_max_gb, t2_pct,
     t3_alloc_gb, _t3_max_gb, pressure, kv_rsv_mb, kv_t2_mb) = m.groups()
    t2_alloc_gb, t3_alloc_gb = int(t2_alloc_gb), int(t3_alloc_gb)
    t2_alloc_mb, t3_alloc_mb = _read_status_mb()
    t2_spilling = (t2_alloc_mb if t2_alloc_mb is not None else t2_alloc_gb) > 0
    t3_spilling = (t3_alloc_mb if t3_alloc_mb is not None else t3_alloc_gb) > 0
    if not t2_spilling and not t3_spilling and pressure == "ok":
        return t2_alloc_mb, t3_alloc_mb  # nothing spilled, nothing to report
    event = {
        "kind": "gaming_vram_pressure", "appid": gameid, "gpu": gpu_name,
        "t2_alloc_gb": t2_alloc_gb, "t2_pct": int(t2_pct),
        "t3_alloc_gb": t3_alloc_gb, "pressure": pressure,
        "kv_reserve_mb": int(kv_rsv_mb), "kv_t2_mb": int(kv_t2_mb),
    }
    if t2_alloc_mb is not None:
        event["t2_alloc_mb"] = t2_alloc_mb
    if t3_alloc_mb is not None:
        event["t3_alloc_mb"] = t3_alloc_mb
    _df_emit(event)
    return t2_alloc_mb, t3_alloc_mb


def _run_hooks(phase, gameid, game_name=""):
    hook_dir = os.path.expanduser(
        f"~/.config/greenboost-gaming/hooks/{phase}.d")
    if not os.path.isdir(hook_dir):
        return
    try:
        entries = sorted(os.listdir(hook_dir))
    except OSError:
        return
    hook_env = dict(os.environ)
    hook_env.setdefault("STEAM_APPID", gameid or "")
    hook_env.setdefault("GREENBOOST_GAME_NAME", game_name)
    for entry in entries:
        path = os.path.join(hook_dir, entry)
        if not os.path.isfile(path):
            continue
        if not (os.stat(path).st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
            continue  # skip non-executable
        try:
            subprocess.run([path], env=hook_env, timeout=30, check=False)
            sys.stderr.write(f"[greenboost-proton] hook {phase}: ran {entry}\n")
        except subprocess.TimeoutExpired:
            sys.stderr.write(
                f"[greenboost-proton] hook {phase}: {entry} timed out (30 s) , skipped\n")
        except Exception as e:
            sys.stderr.write(f"[greenboost-proton] hook {phase}: {entry} failed: {e}\n")


# ── B7: SIGUSR1 stats harvester ───────────────────────────────────────────────
# At session end, find wine64 processes descended from the game launch, send
# SIGUSR1 to trigger a layer stats dump, then append the syslog lines to the
# per-game session log.

def _harvest_layer_stats(gameid, launch_time_iso):
    """Send SIGUSR1 to wine64-preloader descendants and collect stats from journalctl."""
    if not gameid:
        return
    # Find wine64-preloader PIDs from Proton's prefix directory
    pids = []
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "wine64-preloader"], stderr=subprocess.DEVNULL, timeout=3
        ).decode().split()
        pids = [int(p) for p in out if p.strip().isdigit()]
    except Exception:
        pass
    for pid in pids:
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass
    if not pids:
        return
    time.sleep(1)  # give the layer up to 1 s to write to syslog
    log_dir = os.path.expanduser("~/.local/share/greenboost/proton-logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{gameid}.session.log")
    try:
        # SIGUSR1 above is sent to every wine64-preloader descendant
        # regardless of whether the game uses Vulkan or OpenGL , both
        # layers install a SIGUSR1 handler. But greenboost_gl_layer.c never
        # calls openlog(), so its syslog entries fall back to glibc's
        # default identifier (the game's own process name, not a fixed
        # string), meaning a SYSLOG_IDENTIFIER=VK_LAYER_GREENBOOST field
        # filter can only ever see the Vulkan layer's dump. Match on
        # message content instead , both layers tag every line with a
        # fixed prefix ("[VK_LAYER_GREENBOOST] " / "[GB_GL] ") , so an
        # OpenGL game's session log isn't silently left empty.
        dump = subprocess.check_output(
            ["journalctl", "--user", "--no-pager", "-o", "cat",
             f"--since={launch_time_iso}",
             "-g", r"^\[(VK_LAYER_GREENBOOST|GB_GL)\]"],
            timeout=5, stderr=subprocess.DEVNULL,
        ).decode(errors="replace")
        with open(log_path, "a") as f:
            f.write(f"\n--- session {launch_time_iso} ---\n")
            f.write(dump)
        sys.stderr.write(
            f"[greenboost-proton] stats harvested → {log_path}\n")
    except Exception:
        pass


# ── B9: dry-run / env linter ─────────────────────────────────────────────────

def _dry_run_dump(proton_upstream, launch_argv, perf_lock=None, desktop=None):
    """Print sorted env + launch argv, then exit.  Triggered by GREENBOOST_DRY_RUN=1.

    Hands back everything it acquired on the way here first. This exit is
    OUTSIDE the try/finally that unwinds a real session, so without this the
    documented verification command , `GREENBOOST_DRY_RUN=1 ... proton run
    /bin/true` , returned with gaming_mode pinned at 1, the CPU governor on
    performance, the GPU clocks locked and the compositor's animations off,
    and nothing to say so. Confirmed 2026-08-20 by the gaming_mode_stuck
    segment firing seconds after a dry run: a diagnostic that changes the
    machine and leaves it changed is worse than no diagnostic.
    """
    sys.stderr.write("[greenboost-proton] DRY-RUN mode , final environment:\n")
    for k in sorted(os.environ):
        sys.stderr.write(f"  {k}={os.environ[k]}\n")
    sys.stderr.write(f"\n[greenboost-proton] DRY-RUN argv: {launch_argv}\n")
    for res in (desktop, perf_lock):
        if res is None:
            continue
        try:
            res.release()
        except Exception as _e:                  # noqa: BLE001
            sys.stderr.write(
                f"[greenboost-proton] DRY-RUN: could not undo {type(res).__name__} "
                f"({_e!r}) , your machine may still be in game mode. "
                f"Launch and quit a game, or reboot, to clear it.\n")
    # The session record is the third thing this exit would otherwise leave
    # behind: a file naming a pid that is about to stop existing, which the
    # Suite then has to prune before it can answer "is a game running?".
    _release_game_tree()
    sys.stderr.write("[greenboost-proton] DRY-RUN: system state restored.\n")
    sys.exit(0)


# ── PR-GGGG: per-game .env profile loader ─────────────────────────────────────
# Reads ~/.config/greenboost-gaming/per-game/<AppID>.env and applies its
# KEY=VALUE lines as env-var defaults (existing exports win , same precedence
# as global_settings.json).  Supports:
#   KEY=value        → os.environ.setdefault(KEY, value)
#   KEY+=value       → append "value" to KEY using "," as the separator
#                       (matches the VKD3D_CONFIG semantics in _append_vkd3d)
#   # comment        → ignored
#   blank line       → ignored
# Quotes are stripped; no shell expansion (avoid surprises).

def _apply_per_game_env(gameid):
    if not gameid:
        return
    path = os.path.expanduser(
        f"~/.config/greenboost-gaming/per-game/{gameid}.env")
    if not os.path.isfile(path):
        return
    applied, appended = 0, 0
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "+=" in line:
                    key, _, val = line.partition("+=")
                elif "=" in line:
                    key, _, val = line.partition("=")
                else:
                    continue
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if not key:
                    continue
                if "+=" in line:
                    existing = os.environ.get(key, "")
                    os.environ[key] = f"{existing},{val}".lstrip(",") if existing else val
                    appended += 1
                else:
                    if key not in os.environ:
                        os.environ[key] = val
                        applied += 1
    except OSError as e:
        sys.stderr.write(f"[greenboost-proton] per-game env read failed: {e}\n")
        return
    sys.stderr.write(
        f"[greenboost-proton] per-game env ({gameid}.env): "
        f"{applied} set, {appended} appended\n")


# ── PR-GGGG: pre-flight diagnostics ────────────────────────────────────────────
# Verifies the GreenBoost system pieces that this wrapper depends on are
# actually loaded.  Prints a single status line; never fatal , the game still
# runs without these, just without the GreenBoost speedups.

def _preflight(profile=None):
    issues = []
    # PR-GGGG: NVIDIA Image Scaling shader availability.  Detection only ,
    # the actual post-process pass needs swapchain hooks the layer doesn't
    # implement yet.  Logging means a future enabling switch can be tested
    # without an extra preflight pass.
    nis_dir = os.path.expanduser("~/.local/share/greenboost/nis")
    if os.path.isfile(os.path.join(nis_dir, "NIS_Main.glsl")):
        os.environ.setdefault("GREENBOOST_NIS_SHADERS_DIR", nis_dir)
        sys.stderr.write(
            f"[greenboost-proton] NIS shaders staged at {nis_dir} "
            "(post-process dispatch pending layer impl)\n")
    # 1. Kernel module , without greenboost.ko, the Vulkan layer's T2/T3 spill
    #    path is a no-op (silently degrades to vanilla OOM).
    if not os.path.exists("/sys/module/greenboost"):
        issues.append("kernel module greenboost.ko not loaded "
                      "(T2/T3 memory spill disabled)")
    # 2/3. Vulkan layer .so + manifest. Both live under paths that are
    #    KNOWN not to be visible from inside Steam's pressure-vessel
    #    container: the manifests' own "library_path" points at
    #    ~/.local/lib/libVkLayer_greenboost.so and
    #    /usr/local/lib/libVkLayer_greenboost.so (confirmed live
    #    2026-08-08 , /usr/local is not bind-mounted into the sandbox at
    #    all, and neither is $HOME/.local/lib, unlike $HOME/.local/share
    #    which this same function's NIS check above successfully reads).
    #    Whether the layer actually activates for real is NOT decided by
    #    these files being visible from in here: pressure-vessel resolves
    #    Vulkan layers on the HOST side when it builds the container and
    #    re-stages a working copy into its own overrides directory
    #    (confirmed live: a genuine per-launch
    #    .../pressure-vessel/overrides/.../vulkan_imp_layer/NN/
    #    libVkLayer_greenboost.so existed for a launch where this exact
    #    check reported "missing") , the loader inside the container reads
    #    THAT regenerated manifest, never the original files checked here.
    #    A plain os.path.exists() from in here can only ever produce a
    #    false negative under Steam, not a trustworthy answer either way,
    #    so it's demoted to informational instead of an "issue" whenever
    #    Steam launched us (STEAM_COMPAT_DATA_PATH is always set for any
    #    compat tool Steam invokes) , outside that context (a native/
    #    non-Steam launch) the check is trustworthy and stays a real issue.
    _launched_by_steam = "STEAM_COMPAT_DATA_PATH" in os.environ
    layer_lib_candidates = [
        os.path.expanduser("~/.local/lib/libVkLayer_greenboost.so"),
        "/usr/local/lib/libVkLayer_greenboost.so",
        "/usr/lib/libVkLayer_greenboost.so",
    ]
    _home_pf = os.environ.get("HOME", os.path.expanduser("~"))
    manifest_candidates = [
        os.path.join(_home_pf, ".local", "share", "vulkan", "implicit_layer.d",
                      "VkLayer_greenboost.json"),
        "/usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json",
    ]
    _layer_lib_found = any(os.path.exists(p) for p in layer_lib_candidates)
    _manifest_found = any(os.path.exists(p) for p in manifest_candidates)
    if not (_layer_lib_found and _manifest_found):
        if _launched_by_steam:
            sys.stderr.write(
                "[greenboost-proton] layer manifest/library not visible from "
                "inside the Steam sandbox , expected here (pressure-vessel "
                "re-stages layers on the host side before this check runs; "
                "see comment in _preflight() for how this was confirmed), "
                "not evidence the layer failed to load\n")
        else:
            if not _layer_lib_found:
                issues.append("libVkLayer_greenboost.so not found in expected paths")
            if not _manifest_found:
                issues.append("layer manifest missing at " + " or ".join(manifest_candidates))
    # 4. nvidia-smi present (for the GPU clock/power lock that follows).
    if not _resolve_nvidia_smi():
        issues.append("nvidia-smi not found on PATH or common install paths (GPU perf lock skipped)")
    # 5. MangoHud, when the overlay was requested (global toggle or per-game
    #    override) but isn't actually reachable , either as a binary (native
    #    launch) or as an implicit Vulkan layer (Proton/pressure-vessel launch,
    #    the path GREENBOOST_MANGOHUD_DEFAULT normally takes). Confirmed live
    #    2026-08-07: the toggle silently did nothing because MangoHud wasn't
    #    installed at all, and nothing ever said so.
    _wrappers = (profile or {}).get("wrappers") or {}
    _per_game_mh = _wrappers.get("mangohud")
    _want_mh = (
        _per_game_mh if _per_game_mh is not None
        else os.environ.get("GREENBOOST_MANGOHUD_DEFAULT", "0") == "1"
    )
    if _want_mh:
        _mh_layer_candidates = [
            "/usr/share/vulkan/implicit_layer.d/MangoHud.json",
            "/usr/share/vulkan/implicit_layer.d/MangoHud.x86_64.json",
            os.path.join(_home_pf, ".local", "share", "vulkan", "implicit_layer.d",
                          "MangoHud.json"),
            os.path.join(_home_pf, ".local", "share", "vulkan", "implicit_layer.d",
                          "MangoHud.x86_64.json"),
        ]
        if not shutil.which("mangohud") and not any(
                os.path.exists(p) for p in _mh_layer_candidates):
            # Exactly the false negative the layer check above documents, and
            # for the same reason: /usr/share and /usr/bin in here belong to
            # the container, not the host. Confirmed live 2026-08-21 , this
            # printed "MangoHud is not installed" on a machine carrying
            # mangohud 0.8.2 from apt with its manifest at
            # /usr/share/vulkan/implicit_layer.d/MangoHud.x86_64.json. Telling
            # someone to install what they already have sends them off to fix
            # a thing that is not broken, and teaches them to distrust the
            # rest of pre-flight. MangoHud is an implicit Vulkan layer,
            # re-staged host-side by pressure-vessel just like ours, so this
            # check can only ever produce a false negative under Steam.
            # install.sh already carries mangohud as a hard dependency, so on
            # a machine the installer has touched, the honest answer here is
            # "cannot tell from in here".
            if _launched_by_steam:
                sys.stderr.write(
                    "[greenboost-proton] MangoHud not visible from inside the "
                    "Steam sandbox , expected here (host-side implicit Vulkan "
                    "layers are re-staged by pressure-vessel, same as ours "
                    "above), not evidence the overlay is missing. MANGOHUD=1 "
                    "is exported either way.\n")
            else:
                issues.append(
                    "Live Overlay is enabled but MangoHud is not installed "
                    "(sudo apt install mangohud, or re-run install.sh)")
    # 6. gb_dataflux importable , without it every gaming_session event
    #    (_df_emit) is silently dropped and the VRAM-risk badge / session
    #    history (intelligence strategy #1) never gets any data.
    _df_script_dir = os.path.dirname(os.path.abspath(__file__))
    _df_candidates = [
        "/usr/local/lib/greenboost",
        os.path.join(_df_script_dir, "..", "..", "greenboost"),
    ]
    _df_saved_path = list(sys.path)
    try:
        for _p in _df_candidates:
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import importlib.util
        if importlib.util.find_spec("gb_dataflux") is None:
            issues.append(
                "gb_dataflux not importable from "
                + " or ".join(_df_candidates)
                + " (session telemetry / VRAM-risk badge will not be recorded)")
    except Exception:
        issues.append("gb_dataflux import check failed (session telemetry may not be recorded)")
    finally:
        sys.path[:] = _df_saved_path
    if issues:
        sys.stderr.write(
            "[greenboost-proton] pre-flight: " + "; ".join(issues) + "\n")
    else:
        sys.stderr.write("[greenboost-proton] pre-flight: all components OK\n")


# ── PR-GGGG: CPU governor / GPU power lock ────────────────────────────────────
# Snapshots current state, switches the system to maximum-performance for the
# duration of the game, restores on exit.  All best-effort , non-root systems
# silently skip the writes (and warn once).

class _PerfLock:
    def __init__(self):
        self.saved_governors = {}   # cpu# -> previous governor
        self.saved_power_lim = None
        self.persistence_set = False
        self.power_limit_set = False    # whether the pkexec power-limit write actually succeeded
        # PR-GGGG: NVIDIA PowerMizer + GPU clock-lock state.
        self.saved_powermizer = None    # nvidia-settings token (e.g. "0")
        self.locked_gpu_clocks = False  # whether nvidia-smi --lock-gpu-clocks ran
        # PR-GGGG: kernel knobs + power-profile daemons.
        self.saved_swappiness = None    # str , original /proc/sys/vm/swappiness
        self.stopped_power_units = []   # systemctl unit names to restart on exit
        self.saved_powerprofile = None  # powerprofilesctl previous profile name
        # Crash-safe copy of everything above. The attributes on this object
        # only survive as long as this process does , a SIGKILL takes the only
        # record of the original state with it and leaves the box pinned to
        # performance with gaming_mode stuck at 1. See gb_gaming.power_baseline.
        self.baseline = None

    def acquire(self, appid: str = "session"):
        if os.environ.get("GREENBOOST_PERF_LOCK", "1") == "0":
            return
        # Signal greenboost.ko that gaming is active before locking resources.
        self._set_gb_gaming_mode(True)
        self._lock_cpu_governor()
        self._lock_gpu()
        self._lock_kernel_knobs()
        self._stop_power_daemons()
        # Every _lock_* above read its previous value BEFORE writing , that is
        # the baseline, and until now it lived only in this object. Copy it to
        # disk and arm a detached watchdog so a hard kill restores it anyway.
        self._persist_baseline(appid)

    def _persist_baseline(self, appid: str) -> None:
        """Write what release() would undo, in a form that survives us."""
        try:
            from gb_gaming import power_baseline as _pb
        except Exception as _e:                              # noqa: BLE001
            sys.stderr.write(
                f"[greenboost-proton] power baseline unavailable ({_e}) , the "
                "session runs normally, but a crash would leave the CPU "
                "governor, power limit and gaming_mode applied.\n")
            return
        b = _pb.PowerBaseline(appid)
        for cpu, gov in (self.saved_governors or {}).items():
            b.record_file(
                f"/sys/devices/system/cpu/{cpu}/cpufreq/scaling_governor"
                if str(cpu).startswith("cpu")
                else f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor",
                gov)
        if self.saved_swappiness is not None:
            b.record_file("/proc/sys/vm/swappiness", self.saved_swappiness)
        # gaming_mode is recorded even though this process cannot write it
        # from inside pressure-vessel (EROFS , see _set_gb_gaming_mode). The
        # restore script runs on the HOST, where the write does work, and this
        # is the single most expensive knob to leave set: it parks every
        # inference T2 buffer at the eviction queue's tail indefinitely.
        b.record_file(self._GB_GAMING_MODE_PATH, "0")
        if self.locked_gpu_clocks:
            b.record_command(["nvidia-smi", "--reset-gpu-clocks"],
                             "release the GPU clock lock")
            b.record_command(["nvidia-smi", "--reset-memory-clocks"],
                             "release the memory clock lock")
        if self.saved_powermizer is not None:
            b.record_command(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUPowerMizerMode={self.saved_powermizer}"],
                "restore PowerMizer mode")
        if self.power_limit_set and self.saved_power_lim:
            b.record_command(["nvidia-smi", "-pl", str(self.saved_power_lim)],
                             "restore the GPU power limit (needs root)")
        for unit in (self.stopped_power_units or []):
            b.record_command(["systemctl", "start", str(unit)],
                             f"restart {unit}")
        if self.saved_powerprofile:
            b.record_command(["powerprofilesctl", "set", str(self.saved_powerprofile)],
                             "restore the power profile")
        if b.persist() is not None and b.arm():
            self.baseline = b

    def release(self):
        self._start_power_daemons()
        self._restore_kernel_knobs()
        self._restore_gpu()
        self._restore_cpu_governor()
        # Restore inference priority after all locks are released.
        self._set_gb_gaming_mode(False)
        # Clean exit , everything above already restored, so retire the
        # watchdog before it can fire on state that is no longer applied.
        if self.baseline is not None:
            try:
                self.baseline.disarm()
            except Exception:
                pass
            self.baseline = None

    # PR-GGGG: kernel knobs ────────────────────────────────────────────────
    # GreenBoost gaming-mode sysfs path.  Writing "1" tells the kernel module
    # to move inference T2 buffers to the LRU tail so gaming VRAM is evicted last.
    _GB_GAMING_MODE_PATH = "/sys/module/greenboost/parameters/gaming_mode"

    @staticmethod
    def _set_gb_gaming_mode(active: bool) -> None:
        """Signal greenboost.ko that a game is running (or has stopped).
        Non-fatal: greenboost.ko may not be loaded on all machines.

        This process itself always runs INSIDE Steam's pressure-vessel
        container (confirmed live 2026-08-07 via /proc/<pid>/... parent
        chain: srt-bwrap → pv-adverb → this wrapper), and pressure-vessel
        bind-mounts /sys read-only inside the container. The write below
        therefore can never succeed from here, regardless of file
        permissions on the host , the errno is EROFS (30), not EACCES (13);
        `gaming_mode` is already 0664 root:greenboost with the invoking user
        in the `greenboost` group. No chmod fixes this. The real write now
        happens on the host side, in the Tauri backend, around game
        launch/exit (see src/src-tauri/src/manager.rs). This call remains
        as a best-effort no-op for the rare non-Steam/native launch where
        the wrapper is NOT inside a container."""
        try:
            with open(_PerfLock._GB_GAMING_MODE_PATH, "w") as f:
                f.write("1" if active else "0")
            sys.stderr.write(
                f"[greenboost-proton] greenboost gaming_mode → {'1 (game active)' if active else '0 (game stopped)'}\n"
            )
        except FileNotFoundError:
            pass  # greenboost.ko not loaded , nothing to signal
        except OSError as _e:
            if _e.errno == errno.EROFS:
                # Expected inside pressure-vessel , the host-side write in
                # the Tauri backend is the real path; nothing wrong here.
                sys.stderr.write(
                    f"[greenboost-proton] gaming_mode: read-only /sys inside "
                    "the Proton sandbox (expected here , handled host-side "
                    "by the Tauri backend when launched from the app)\n"
                )
            else:
                sys.stderr.write(
                    f"[greenboost-proton] gaming_mode write failed ({_e}) , "
                    f"{_PerfLock._GB_GAMING_MODE_PATH} not writable by this "
                    "user; inference T2 buffers will NOT be deprioritised "
                    "during this session\n"
                )

    def _lock_kernel_knobs(self):
        # vm.swappiness: lower = kernel less eager to swap.  Default 60 is
        # tuned for general workloads; for a foreground game we want 1 (almost
        # never).  Skipped silently on non-root.
        try:
            with open("/proc/sys/vm/swappiness") as f:
                self.saved_swappiness = f.read().strip()
            with open("/proc/sys/vm/swappiness", "w") as f:
                f.write("1")
            sys.stderr.write(
                f"[greenboost-proton] vm.swappiness: {self.saved_swappiness} → 1\n")
        except (OSError, PermissionError):
            self.saved_swappiness = None

    def _restore_kernel_knobs(self):
        if self.saved_swappiness is not None:
            try:
                with open("/proc/sys/vm/swappiness", "w") as f:
                    f.write(self.saved_swappiness)
            except OSError:
                pass

    # PR-GGGG: pause power daemons that might down-clock the system ────────
    def _stop_power_daemons(self):
        # power-profiles-daemon: snapshot active profile, switch to performance.
        if shutil.which("powerprofilesctl"):
            try:
                r = subprocess.run(["powerprofilesctl", "get"],
                                   check=True, capture_output=True, text=True)
                prev = r.stdout.strip()
                if prev and prev != "performance":
                    self.saved_powerprofile = prev
                    subprocess.run(
                        ["powerprofilesctl", "set", "performance"],
                        check=False, stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL)
                    sys.stderr.write(
                        f"[greenboost-proton] power profile: {prev} → performance\n")
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
        # TLP / auto-cpufreq are aggressive throttlers; pause them entirely.
        for unit in ("tlp.service", "auto-cpufreq.service"):
            try:
                r = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True, text=True, check=False)
                if r.stdout.strip() == "active":
                    rc = subprocess.run(
                        ["systemctl", "stop", unit],
                        check=False, stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL).returncode
                    if rc == 0:
                        self.stopped_power_units.append(unit)
                        sys.stderr.write(
                            f"[greenboost-proton] paused {unit} for session\n")
            except FileNotFoundError:
                break  # no systemctl

    def _start_power_daemons(self):
        for unit in self.stopped_power_units:
            subprocess.run(["systemctl", "start", unit],
                           check=False, stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)
        if self.saved_powerprofile:
            subprocess.run(
                ["powerprofilesctl", "set", self.saved_powerprofile],
                check=False, stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL)

    def _cpu_paths(self):
        return sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"))

    def _lock_cpu_governor(self):
        target = "performance"
        for p in self._cpu_paths():
            try:
                with open(p) as f:
                    cur = f.read().strip()
                if cur == target:
                    continue
                # Save before overwrite , only the first override per session.
                cpu = p.split("/cpu")[1].split("/")[0]
                self.saved_governors.setdefault(cpu, cur)
                with open(p, "w") as f:
                    f.write(target)
            except PermissionError:
                # Need root or CAP_SYS_NICE , silently skip; the rest of the
                # gaming.service pipeline (gaming-mode systemd unit) handles
                # this when installed.
                self.saved_governors.clear()
                return
            except OSError:
                pass

    def _restore_cpu_governor(self):
        for cpu, prev in self.saved_governors.items():
            try:
                with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor", "w") as f:
                    f.write(prev)
            except OSError:
                pass

    def _lock_gpu(self):
        # `-pm 1` = persistence mode (driver stays initialised between
        # processes , important for cold starts); `-pl <max>` = pin power
        # limit to the maximum allowed.  Both no-op on non-root.
        nvidia_smi = _resolve_nvidia_smi()
        if not nvidia_smi:
            return
        try:
            r = subprocess.run(
                [nvidia_smi, "--query-gpu=power.max_limit,power.limit,clocks.max.graphics,clocks.max.memory",
                 "--format=csv,noheader,nounits"],
                check=True, capture_output=True, text=True)
            line = r.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            max_w, cur_w = parts[0], parts[1]
            max_gfx_mhz = parts[2] if len(parts) > 2 else None
            max_mem_mhz = parts[3] if len(parts) > 3 else None
            self.saved_power_lim = cur_w
            _persist_rc = subprocess.run(
                [nvidia_smi, "-pm", "1"],
                check=False, stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL).returncode
            self.persistence_set = (_persist_rc == 0)

            # `nvidia-smi -pl` requires root; this wrapper runs as the
            # invoking user, so it always failed silently while the log line
            # below printed unconditionally (confirmed live 2026-08-07: the
            # log claimed 250W → 300W while nvidia-smi -q kept reporting
            # 250W the whole session). Route through the same Wayland-safe
            # pkexec + nvml_control.py path _activate_gpu_profile() already
            # uses for per-profile power limits, and only report success
            # when it actually is one.
            _power_ok = False
            for _ctrl in [
                os.path.expanduser("~/.local/lib/greenboost-gaming/gb_gaming/nvml_control.py"),
                "/usr/local/lib/greenboost-gaming/gb_gaming/nvml_control.py",
            ]:
                # Deliberately NOT gated on os.path.isfile(_ctrl) first.
                # Confirmed live 2026-08-08: from inside Steam's
                # pressure-vessel container, os.path.isfile() reports BOTH
                # candidates missing even though the real host file exists
                # at exactly this path (verified by running this same
                # resolution logic outside the container, where it finds
                # the file and the power lock succeeds) , pressure-vessel
                # does not bind-mount either .../lib/greenboost-gaming/
                # location into the sandbox. pkexec's escalated process is
                # launched by the system polkit daemon, not spawned as a
                # child of this container, so it may reach a path this
                # process itself cannot see. Attempt the call directly and
                # judge success from the actual exit code instead of a
                # pre-check that's proven unreliable in exactly this
                # context , a stale prior fix (2026-08-07) already
                # established the same lesson for TimeoutExpired escaping
                # uncaught; this is the same class of "don't guess,
                # attempt and check the result" fix.
                try:
                    _r = subprocess.run(
                        ["pkexec", "python3", _ctrl, "set-power", max_w],
                        capture_output=True, timeout=10)
                    _power_ok = (_r.returncode == 0)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    _power_ok = False
                if _power_ok:
                    break
            if _power_ok:
                self.power_limit_set = True
                sys.stderr.write(
                    f"[greenboost-proton] GPU power lock: {cur_w}W → {max_w}W "
                    f"(persistence {'on' if self.persistence_set else 'unavailable'})\n")
            else:
                sys.stderr.write(
                    "[greenboost-proton] GPU power lock skipped , set-power via "
                    "pkexec/nvml_control.py failed or nvml_control.py not found "
                    "(re-run install.sh); GPU stays at its default power limit\n")

            # PR-GGGG: lock GPU graphics + memory clocks to their max boost
            # bin so the driver doesn't down-clock during light-load lulls
            # (e.g. cutscenes, loading screens), then need to ramp back up
            # mid-frame and miss the present deadline.
            # `--lock-gpu-clocks=min,max` requires nvidia-smi from driver
            # 470+ and root; silently skipped otherwise.
            if max_gfx_mhz and max_gfx_mhz.isdigit():
                rc = subprocess.run(
                    [nvidia_smi,
                     f"--lock-gpu-clocks=0,{max_gfx_mhz}"],
                    check=False, stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL).returncode
                if rc == 0:
                    self.locked_gpu_clocks = True
                    sys.stderr.write(
                        f"[greenboost-proton] GPU clock lock: graphics 0–{max_gfx_mhz} MHz\n")
            if max_mem_mhz and max_mem_mhz.isdigit():
                # Memory clock lock , only effective on consumer cards from
                # ~Turing onwards; silently skipped if not supported.
                subprocess.run(
                    [nvidia_smi,
                     f"--lock-memory-clocks=0,{max_mem_mhz}"],
                    check=False, stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError, IndexError):
            pass

        # PR-GGGG: GPUPowerMizerMode = 1 (Prefer Maximum Performance) via
        # nvidia-settings.  Snapshots prior mode so we can restore.  This
        # complements the power-limit lock , PowerMizer governs CLOCK
        # selection within the power envelope, the power lock governs the
        # envelope itself.  Needs an X / Wayland session (nvidia-settings is
        # GUI-tied); harmless to fail.
        try:
            q = subprocess.run(
                ["nvidia-settings", "-q", "[gpu:0]/GPUPowerMizerMode",
                 "-t"], check=True, capture_output=True, text=True)
            prev = q.stdout.strip()
            if prev.isdigit():
                self.saved_powermizer = prev
                if subprocess.run(
                        ["nvidia-settings", "-a",
                         "[gpu:0]/GPUPowerMizerMode=1"],
                        check=False, stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL).returncode == 0:
                    sys.stderr.write(
                        f"[greenboost-proton] PowerMizer mode: "
                        f"{prev} → 1 (prefer max perf)\n")
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError, OSError):
            pass

    def _restore_gpu(self):
        nvidia_smi = _resolve_nvidia_smi()
        if nvidia_smi and self.locked_gpu_clocks:
            subprocess.run([nvidia_smi, "--reset-gpu-clocks"],
                           check=False, stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)
            subprocess.run([nvidia_smi, "--reset-memory-clocks"],
                           check=False, stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)
        if self.saved_powermizer is not None:
            subprocess.run(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUPowerMizerMode={self.saved_powermizer}"],
                check=False, stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL)
        if self.power_limit_set and self.saved_power_lim:
            # Restore via the same privileged path used to set it , a plain
            # `nvidia-smi -pl` here would silently no-op as non-root, same
            # bug as the forward direction. Also not gated on
            # os.path.isfile(_ctrl) , same reason as the forward-direction
            # lock above: that pre-check is unreliable from inside
            # pressure-vessel, and this path only runs at all when
            # self.power_limit_set is True, i.e. the forward lock already
            # proved a candidate here actually works this session.
            for _ctrl in [
                os.path.expanduser("~/.local/lib/greenboost-gaming/gb_gaming/nvml_control.py"),
                "/usr/local/lib/greenboost-gaming/gb_gaming/nvml_control.py",
            ]:
                try:
                    _r = subprocess.run(
                        ["pkexec", "python3", _ctrl, "set-power", self.saved_power_lim],
                        capture_output=True, timeout=10)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue  # try the other candidate before giving up
                if _r.returncode == 0:
                    break
        elif nvidia_smi and self.saved_power_lim:
            subprocess.run([nvidia_smi, "-pl", self.saved_power_lim],
                           check=False, stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)
        # Persistence mode left on intentionally , turning it off costs ~1 s
        # of driver-reinit on the next launch; user can `nvidia-smi -pm 0`
        # manually if they ever want to.


# ── PR-GGGG: memory subsystem prep ────────────────────────────────────────────
# RLIMIT_MEMLOCK matters for Wine's large reservations and the DMA-BUF
# imports the Vulkan layer performs.  Transparent hugepages must be at least
# `madvise` for the kernel to back the T2 pool with 2 MB pages.

def _prep_memory():
    if os.environ.get("GREENBOOST_MEMLOCK_UNLIMITED", "1") != "0":
        try:
            resource.setrlimit(resource.RLIMIT_MEMLOCK,
                               (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        except (ValueError, OSError):
            pass  # CAP_SYS_RESOURCE not held , non-fatal
    # Verify THP is sane; warn (don't change) if the admin chose 'never'.
    try:
        with open("/sys/kernel/mm/transparent_hugepage/enabled") as f:
            txt = f.read()
        if "[never]" in txt:
            sys.stderr.write(
                "[greenboost-proton] WARNING: transparent_hugepage=never , "
                "T2 pool will use 4 KB pages.  Run: "
                "echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/enabled\n")
    except OSError:
        pass


# ── PR-GGGG: desktop compositor suspend ───────────────────────────────────────
# Suspends compositor effects (animations, shadows, blur, transparency) for
# the duration of the game.  These are the single largest source of frame
# pacing jitter on Linux desktops outside the game's render loop , a one-
# pixel mouse move can trigger a full compositor recomposite, adding 1–3 ms
# of latency.  Restored in finally so the desktop isn't left jankily flat.

class _DesktopSuspend:
    def __init__(self):
        self.kwin_suspended = False
        self.gnome_anim_was = None
        if os.environ.get("GREENBOOST_COMPOSITOR_SUSPEND", "1") == "0":
            self.enabled = False
            return
        self.enabled = True
        self.desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    def acquire(self, baseline=None):
        """Suspend the compositor's expensive effects for the session.

        `baseline` is `_PerfLock`'s crash-safe record. Pass it, or this class
        repeats the exact mistake power_baseline exists to prevent: the saved
        value lives only on this object, so anything that kills the process
        without unwinding strands the setting , and the NEXT session then
        reads the stranded value and stores it as its own "original". Two
        sessions is all it takes for "animations off" to become permanent
        truth with nothing left that remembers otherwise. Confirmed on this
        machine 2026-08-20, via dry runs that exited before the restore.
        """
        if not getattr(self, "enabled", False):
            return
        # KDE / Plasma , qdbus-style compositor suspend.  Works on X11 *and*
        # Wayland (Wayland needs Plasma 5.27+ for the dbus method).
        if "kde" in self.desktop or "plasma" in self.desktop:
            ok = subprocess.run(
                ["qdbus", "org.kde.KWin", "/Compositor",
                 "org.kde.kwin.Compositing.suspend"],
                check=False, stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL).returncode == 0
            # qdbus6 , newer Qt6 KDE.
            if not ok:
                ok = subprocess.run(
                    ["qdbus6", "org.kde.KWin", "/Compositor",
                     "org.kde.kwin.Compositing.suspend"],
                    check=False, stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL).returncode == 0
            self.kwin_suspended = ok
            if ok:
                self._record(baseline,
                             ["qdbus", "org.kde.KWin", "/Compositor",
                              "org.kde.kwin.Compositing.resume"],
                             "resume the KWin compositor")
                sys.stderr.write(
                    "[greenboost-proton] KWin compositor suspended for session\n")
        # GNOME , flip enable-animations via gsettings.  Doesn't disable the
        # compositor (mutter is always compositing) but kills the most
        # expensive shell effects.
        elif "gnome" in self.desktop:
            try:
                r = subprocess.run(
                    ["gsettings", "get",
                     "org.gnome.desktop.interface", "enable-animations"],
                    check=True, capture_output=True, text=True)
                self.gnome_anim_was = r.stdout.strip()
                subprocess.run(
                    ["gsettings", "set",
                     "org.gnome.desktop.interface", "enable-animations", "false"],
                    check=False, stderr=subprocess.DEVNULL)
                self._record(baseline,
                             ["gsettings", "set",
                              "org.gnome.desktop.interface",
                              "enable-animations", self.gnome_anim_was],
                             "restore GNOME shell animations")
                sys.stderr.write(
                    "[greenboost-proton] GNOME shell animations disabled for session\n")
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

    @staticmethod
    def _record(baseline, argv, note):
        """Add one undo command to the on-disk baseline and rewrite it.

        Re-persisting is required, not optional: `_PerfLock` already wrote and
        armed the baseline before we got here, and the watchdog reads the
        FILE when it fires, not this process's memory.
        """
        if baseline is None:
            return
        try:
            baseline.record_command(argv, note)
            baseline.persist()
        except Exception:                                    # noqa: BLE001
            pass                # a missing undo must never fail the launch

    def release(self):
        if self.kwin_suspended:
            for tool in ("qdbus", "qdbus6"):
                if subprocess.run(
                        [tool, "org.kde.KWin", "/Compositor",
                         "org.kde.kwin.Compositing.resume"],
                        check=False, stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL).returncode == 0:
                    break
        if self.gnome_anim_was is not None:
            subprocess.run(
                ["gsettings", "set",
                 "org.gnome.desktop.interface", "enable-animations",
                 self.gnome_anim_was],
                check=False, stderr=subprocess.DEVNULL)


# ── PR-GGGG: DDR pool pre-warm ────────────────────────────────────────────────
# greenboost.ko pre-allocates the T2 DDR pool at module-load time, but the
# physical pages aren't faulted-in until first touch.  The Vulkan layer's
# DMA-BUF import triggers fault-in during the first big allocation ,
# which means a noticeable hitch on the first scene transition that
# overflows VRAM.
#
# Pre-warm by reading the pool's first 16 MB through the kernel module's
# sysfs prewarm hook (if exposed) or by polling the pool-info ioctl ,
# both are no-ops if the kernel module is absent.

def _prewarm_ddr_pool():
    if os.environ.get("GREENBOOST_DDR_PREWARM", "1") == "0":
        return
    prewarm = "/sys/module/greenboost/parameters/prewarm_mb"
    if os.path.exists(prewarm):
        try:
            # Some kernel builds expose a writable param that, when set,
            # forces page-fault-in of the given size.  Best-effort.
            with open(prewarm, "w") as f:
                f.write("256")
            sys.stderr.write("[greenboost-proton] T2 DDR pool: 256 MB pre-warmed\n")
            return
        except OSError:
            pass
    # Fall back: read pool info via sysfs to trigger the kernel module's
    # info path (which sometimes incidentally warms metadata).  Strictly
    # diagnostic.
    info = "/sys/module/greenboost/parameters/virtual_vram_gb"
    if os.path.exists(info):
        try:
            with open(info) as f:
                gb = f.read().strip()
            sys.stderr.write(
                f"[greenboost-proton] T2 DDR pool reachable ({gb} GB virtual)\n")
        except OSError:
            pass


# ── PR-GGGG: dxvk-gplasync overlay ─────────────────────────────────────────────
# Runs in a forked child after upstream Proton has set up the prefix.  Waits
# for system32 to contain DXVK's d3d11.dll (stock copy), then atomically
# overwrites the four DXVK DLLs with their gplasync counterparts.  We use a
# polling loop (max 20 s) because Proton's setup happens inline before the
# game runs , by the time stock DXVK is in place, the game executable hasn't
# loaded any of these DLLs yet, so the swap is safe.
_GPLASYNC_DLLS = ("d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll")


def _stage_gplasync_async():
    pid = os.fork()
    if pid != 0:
        return  # parent keeps going
    try:
        os.setpgrp()  # detach so the watcher survives Proton exit
        _stage_gplasync_worker()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[greenboost-proton] gplasync staging failed: {e}\n")
    finally:
        os._exit(0)


def _stage_gplasync_worker():
    version = os.environ.get("GREENBOOST_GPLASYNC_VERSION", "current")
    home = os.environ.get("HOME", os.path.expanduser("~"))
    src = os.path.join(home, ".local/share/greenboost/dxvk-gplasync", version)
    if not os.path.isdir(os.path.join(src, "x64")):
        return  # cache absent , install.sh wasn't run or download failed

    compat = os.environ.get("STEAM_COMPAT_DATA_PATH", "")
    if not compat:
        return
    sys32   = os.path.join(compat, "pfx/drive_c/windows/system32")
    syswow  = os.path.join(compat, "pfx/drive_c/windows/syswow64")

    # Poll up to 20 s for Proton's prefix-setup phase to drop stock DXVK
    # into system32 , we want to overwrite, not race with that copy.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if os.path.isfile(os.path.join(sys32, "d3d11.dll")):
            break
        time.sleep(0.2)
    else:
        return  # Proton never staged DXVK; nothing to overlay

    # Small extra delay so Proton finishes its `cp` before we overwrite.
    time.sleep(0.4)

    overlaid = 0
    for dll in _GPLASYNC_DLLS:
        for dst_dir, arch in ((sys32, "x64"), (syswow, "x32")):
            src_dll = os.path.join(src, arch, dll)
            dst_dll = os.path.join(dst_dir, dll)
            if not os.path.isfile(src_dll) or not os.path.isdir(dst_dir):
                continue
            try:
                # Atomic via mktemp + rename , never leaves a half-written DLL.
                tmp = dst_dll + ".gpl_staging"
                shutil.copy2(src_dll, tmp)
                os.replace(tmp, dst_dll)
                overlaid += 1
            except OSError:
                pass

    sys.stderr.write(
        f"[greenboost-proton] gplasync overlay: {overlaid}/{2 * len(_GPLASYNC_DLLS)} "
        f"DLLs staged from {src}\n"
    )


# ── Per-game VKD3D overrides ───────────────────────────────────────────────────
# Games in this set get DXR disabled (dxr / dxr11 not injected into VKD3D_CONFIG).
# Add AppIDs here when a game has a known DXR crash or incompatibility.
_NO_DXR_GAMES = {
    "3764200",  # Resident Evil 9: Requiem , DirectStorage init fails with DXR
}

# ── Per-game Wine/env overrides ────────────────────────────────────────────────
# Maps AppID → dict of env var overrides applied before Proton Experimental runs.
# WINEDLLOVERRIDES entries are appended to any existing value (not replaced).
_GAME_OVERRIDES = {
}


# ── Hardware detection ─────────────────────────────────────────────────────────

def _parse_cpulist(cpulist: str) -> list[int]:
    """Parse Linux cpulist format '0-3,8,12-15' → [0,1,2,3,8,12,13,14,15]."""
    cpus: list[int] = []
    for part in cpulist.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                cpus.extend(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                pass
    return cpus


def _get_gpu_numa_node() -> int:
    """Return the NUMA node index of the first NVIDIA GPU, or -1 if unknown.

    Reads /sys/bus/pci/devices/<bdf>/numa_node for each PCI device whose
    class is 0x030000 (VGA) or 0x030200 (3D Controller) and whose vendor
    is 0x10de (NVIDIA).  Returns -1 on single-NUMA / non-NUMA systems."""
    for dev_path in glob.glob("/sys/bus/pci/devices/*"):
        try:
            vendor = open(os.path.join(dev_path, "vendor")).read().strip()
            if vendor not in ("0x10de", "0x10DE"):
                continue
            dev_class = open(os.path.join(dev_path, "class")).read().strip().lower()
            if not (dev_class.startswith("0x0300") or dev_class.startswith("0x0302")):
                continue
            numa = int(open(os.path.join(dev_path, "numa_node")).read().strip())
            return numa  # -1 means "no NUMA info", ≥0 means a real node
        except Exception:
            continue
    return -1


def _numa_local_cpus(numa_node: int) -> list[int]:
    """Return sorted list of CPU indices on the given NUMA node.
    Returns empty list when node is -1 or the sysfs path is absent."""
    if numa_node < 0:
        return []
    try:
        cpulist = open(
            f"/sys/devices/system/node/node{numa_node}/cpulist"
        ).read().strip()
        return sorted(_parse_cpulist(cpulist))
    except Exception:
        return []


def _detect_nvidia():
    """Returns (gpu_name, vram_mb, is_nvidia, supports_rt). Auto-detected only."""
    gpu_name, vram_mb, is_nvidia, supports_rt = "", 0, False, False

    nvidia_smi = _resolve_nvidia_smi()
    if nvidia_smi:
        try:
            out = subprocess.check_output(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                timeout=3, stderr=subprocess.DEVNULL,
            ).decode().strip().splitlines()
            if out:
                parts = out[0].split(",", 1)
                gpu_name = parts[0].strip()
                m = re.search(r"(\d+)", parts[1]) if len(parts) > 1 else None
                if m:
                    vram_mb = int(m.group(1))
                is_nvidia = True
        except Exception:
            pass

    if not gpu_name:
        for info_path in glob.glob("/proc/driver/nvidia/gpus/*/information"):
            try:
                text = open(info_path).read()
                m_name = re.search(r"^Model:\s+(.+)$", text, re.M)
                m_vram = re.search(r"^Video Memory:\s+(\d+)", text, re.M)
                if m_name:
                    gpu_name = m_name.group(1).strip()
                    is_nvidia = True
                if m_vram:
                    vram_mb = int(m_vram.group(1))
                if gpu_name:
                    break
            except Exception:
                pass

    if not gpu_name:
        try:
            for line in subprocess.check_output(
                ["lspci"], timeout=3, stderr=subprocess.DEVNULL,
            ).decode().splitlines():
                if re.search(r"VGA compatible|3D controller|Display controller", line, re.I):
                    gpu_name = re.sub(r".+: ", "", line).strip()
                    gpu_name = re.sub(r" \(.*\)", "", gpu_name)
                    if "nvidia" in gpu_name.lower():
                        is_nvidia = True
                    break
        except Exception:
            pass

    if is_nvidia and gpu_name:
        if re.search(
            r"rtx|a\d{3,4}[^d]|h\d{2,3}|l\d{2,3}|b\d{3}|blackwell|lovelace|ampere|turing",
            gpu_name, re.I,
        ):
            supports_rt = True
        else:
            try:
                if "VK_KHR_ray_tracing_pipeline" in subprocess.check_output(
                    ["vulkaninfo", "--summary"], timeout=5, stderr=subprocess.DEVNULL,
                ).decode():
                    supports_rt = True
            except Exception:
                pass

    return gpu_name, vram_mb, is_nvidia, supports_rt


def _detect_cpu():
    """Returns (total_threads, p_cores, cpu_model, p_core_cpus).
    p_core_cpus is a sorted list of logical CPU indices that are P-cores
    (includes both HT threads of each P-core). On uniform systems (AMD or
    non-hybrid Intel) all CPUs are returned as p_core_cpus."""
    cpu_model = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    total_threads = os.cpu_count() or 1
    p_cpu_set, e_cpu_set = set(), set()

    # Primary: core_type sysfs (Intel hybrid: 1=P-core, 0=E-core)
    for ct_path in glob.glob("/sys/devices/system/cpu/cpu*/topology/core_type"):
        try:
            ct = int(open(ct_path).read().strip())
            idx = int(re.search(r"/cpu(\d+)/", ct_path).group(1))
            (p_cpu_set if ct == 1 else e_cpu_set).add(idx)
        except Exception:
            pass
    if p_cpu_set and e_cpu_set:
        p_core_cpus = sorted(p_cpu_set)
        return total_threads, len(p_core_cpus), cpu_model, p_core_cpus

    # Fallback: thread_siblings_list , P-cores are hyperthreaded (have siblings),
    # E-cores are single-thread (no siblings)
    p_sibling_groups = set()
    for sib_path in glob.glob("/sys/devices/system/cpu/cpu*/topology/thread_siblings_list"):
        try:
            sib = open(sib_path).read().strip()
            idx = int(re.search(r"/cpu(\d+)/", sib_path).group(1))
            if "-" in sib or "," in sib:
                p_cpu_set.add(idx)
                p_sibling_groups.add(sib)
            else:
                e_cpu_set.add(idx)
        except Exception:
            pass
    if p_cpu_set and e_cpu_set:
        p_core_cpus = sorted(p_cpu_set)
        return total_threads, len(p_sibling_groups), cpu_model, p_core_cpus

    # Uniform CPU (AMD or non-hybrid Intel) , all logical CPUs are P-cores
    p_core_cpus = list(range(total_threads))
    return total_threads, total_threads, cpu_model, p_core_cpus


def _purge_foz_pipeline_cache(gameid):
    """Delete steamapprun fossilize pipeline cache entries for a game.

    Called after a DXR state transition so stale pipelines (compiled without
    DXR, or with DXR when it is now disabled) are not replayed at startup.
    A marker file prevents repeated deletions on every launch.
    """
    import shutil
    shadercache = os.path.expanduser(
        f"~/.local/share/Steam/steamapps/shadercache/{gameid}"
    )
    marker = os.path.join(shadercache, ".greenboost_dxr_cache_purged")
    if os.path.exists(marker):
        return
    foz_root = os.path.join(shadercache, "fozpipelinesv6")
    if not os.path.isdir(foz_root):
        try:
            os.makedirs(shadercache, exist_ok=True)
            open(marker, "w").close()
        except Exception:
            pass
        return
    cleaned = 0
    for entry in os.listdir(foz_root):
        if entry.startswith("steamapprun_pipeline_cache."):
            target = os.path.join(foz_root, entry)
            try:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
                cleaned += 1
            except Exception as e:
                sys.stderr.write(
                    f"[greenboost-proton] WARNING: could not remove {target}: {e}\n"
                )
    try:
        open(marker, "w").close()
    except Exception:
        pass
    if cleaned:
        sys.stderr.write(
            f"[greenboost-proton] Cleared {cleaned} stale DXR pipeline cache(s)"
            f" for AppID {gameid}\n"
        )


def _restore_ue5_rt(gameid):
    """Remove GreenBoost-written RT disable keys from Engine.ini.

    Called when a game is promoted back to DXR-enabled after the underlying
    driver bug is fixed (e.g. NVIDIA 595 fixes the BMWu GPU hang / Xid errors).
    Strips r.RayTracing, r.RayTracing.EnableInGame, r.Lumen.HardwareRayTracing
    from the ini so game defaults (RT on) apply again. A marker file prevents
    repeated rewrites on every launch.
    """
    _UE5_RT_RESTORE_GAMES = {
        "2358720": ("b1", "Windows"),  # Black Myth: Wukong , NVIDIA 595 fixes GPU hang/Xid
    }
    if gameid not in _UE5_RT_RESTORE_GAMES:
        return

    app_name, config_subdir = _UE5_RT_RESTORE_GAMES[gameid]
    compat_root = os.path.expanduser(
        f"~/.local/share/Steam/steamapps/compatdata/{gameid}/pfx"
        f"/drive_c/users/steamuser/AppData/Local"
    )
    config_dir = os.path.join(compat_root, app_name, "Saved", "Config", config_subdir)
    engine_ini = os.path.join(config_dir, "Engine.ini")

    # Purge stale fossilize pipeline cache whenever DXR is being re-enabled.
    # Runs once (idempotent via its own marker), independently of the Engine.ini
    # restoration below , covers the case where a previous launch crashed after
    # writing .greenboost_rt_restored but before the cache was cleaned.
    _purge_foz_pipeline_cache(gameid)

    if not os.path.isfile(engine_ini):
        return

    marker = os.path.join(config_dir, ".greenboost_rt_restored")
    if os.path.exists(marker):
        return  # Already restored.

    rt_keys = {"r.RayTracing", "r.RayTracing.EnableInGame", "r.Lumen.HardwareRayTracing"}

    try:
        with open(engine_ini, encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception:
        return

    out_lines = [
        line for line in lines
        if line.rstrip().split("=", 1)[0].strip() not in rt_keys
    ]

    try:
        with open(engine_ini, "w", encoding="utf-8") as f:
            f.writelines(out_lines)
        open(marker, "w").close()
        sys.stderr.write(
            f"[greenboost-proton] Engine.ini RT overrides removed (DXR re-enabled): {engine_ini}\n"
        )
    except Exception as e:
        sys.stderr.write(
            f"[greenboost-proton] WARNING: could not restore Engine.ini RT settings: {e}\n"
        )


def _inject_ue5_rt_disable(gameid):
    """Write UE5 Engine.ini overrides to disable ray tracing for affected games.

    VKD3D-Proton's internal game database can re-enable DXR even when no_dxr is
    set in VKD3D_CONFIG. Writing r.RayTracing=0 at the UE5 engine level prevents
    the game from ever issuing DX12 RT calls, regardless of what VKD3D-Proton
    advertises via CheckFeatureSupport.
    """
    _UE5_RT_DISABLE_GAMES = {
        "3764200": ("BIOHAZARD RE_requiem", "Windows"),
    }
    if gameid not in _UE5_RT_DISABLE_GAMES:
        return

    app_name, config_subdir = _UE5_RT_DISABLE_GAMES[gameid]
    compat_root = os.path.expanduser(
        f"~/.local/share/Steam/steamapps/compatdata/{gameid}/pfx"
        f"/drive_c/users/steamuser/AppData/Local"
    )
    config_dir = os.path.join(compat_root, app_name, "Saved", "Config", config_subdir)
    engine_ini = os.path.join(config_dir, "Engine.ini")

    rt_section = "[/Script/Engine.RendererSettings]"
    rt_keys = {
        "r.RayTracing": "0",
        "r.RayTracing.EnableInGame": "0",
        "r.Lumen.HardwareRayTracing": "0",
    }

    if not os.path.isdir(config_dir):
        return  # Prefix not initialised yet , game hasn't run once; skip.

    # Parse existing ini, preserve all non-RT content, inject/overwrite RT keys.
    existing_lines = []
    if os.path.isfile(engine_ini):
        try:
            with open(engine_ini, encoding="utf-8-sig") as f:
                existing_lines = f.readlines()
        except Exception:
            pass

    in_section = False
    seen_keys = set()
    out_lines = []

    for line in existing_lines:
        stripped = line.rstrip()
        if stripped == rt_section:
            in_section = True
            out_lines.append(line)
            continue
        if in_section and stripped.startswith("["):
            # Flush any unseen RT keys before the next section starts.
            for k, v in rt_keys.items():
                if k not in seen_keys:
                    out_lines.append(f"{k}={v}\n")
                    seen_keys.add(k)
            in_section = False
        if in_section:
            key = stripped.split("=", 1)[0].strip()
            if key in rt_keys:
                out_lines.append(f"{key}={rt_keys[key]}\n")
                seen_keys.add(key)
                continue
        out_lines.append(line)

    # Section was never found , append it.
    if rt_section not in "".join(out_lines):
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        out_lines.append(f"{rt_section}\n")
        for k, v in rt_keys.items():
            out_lines.append(f"{k}={v}\n")
    elif in_section:
        # Section was last in file , flush remaining keys.
        for k, v in rt_keys.items():
            if k not in seen_keys:
                out_lines.append(f"{k}={v}\n")

    try:
        with open(engine_ini, "w", encoding="utf-8") as f:
            f.writelines(out_lines)
        sys.stderr.write(
            f"[greenboost-proton] Engine.ini RT override written: {engine_ini}\n"
        )
    except Exception as e:
        sys.stderr.write(
            f"[greenboost-proton] WARNING: could not write Engine.ini: {e}\n"
        )


def _cleanup_dxr_pipeline_cache(gameid):
    """Remove stale DXR-containing pipeline cache entries for affected games.

    After disabling DXR, any previously compiled DXR pipelines in the fossilize
    cache are no longer valid and will produce replay warnings (or worse, be
    referenced by in-progress render work before the RT disable takes effect).
    A marker file prevents repeated deletions on every launch.
    """
    _CACHE_CLEANUP_GAMES = set()
    if gameid not in _CACHE_CLEANUP_GAMES:
        return

    shadercache = os.path.expanduser(
        f"~/.local/share/Steam/steamapps/shadercache/{gameid}"
    )
    marker = os.path.join(shadercache, ".greenboost_no_dxr_cleaned")
    if os.path.exists(marker):
        return  # Already cleaned.

    foz_root = os.path.join(shadercache, "fozpipelinesv6")
    if not os.path.isdir(foz_root):
        return

    import shutil
    cleaned = 0
    for entry in os.listdir(foz_root):
        if entry.startswith("steamapprun_pipeline_cache."):
            target = os.path.join(foz_root, entry)
            try:
                shutil.rmtree(target)
                cleaned += 1
            except Exception as e:
                sys.stderr.write(
                    f"[greenboost-proton] WARNING: could not remove cache {target}: {e}\n"
                )

    try:
        open(marker, "w").close()
    except Exception:
        pass

    if cleaned:
        sys.stderr.write(
            f"[greenboost-proton] Cleared {cleaned} stale DXR pipeline cache(s) for AppID {gameid}\n"
        )


def _read_sysfs_int(path, default=0):
    """Read an integer from a sysfs/procfs path; return default on any error."""
    try:
        return int(open(path).read().strip())
    except Exception:
        return default


def _append_vkd3d(key, option):
    cur = os.environ.get(key, "")
    if not cur:
        os.environ[key] = option
    elif "," + option + "," not in "," + cur + ",":
        os.environ[key] = cur + "," + option


def _steam_common_dirs():
    dirs = []
    steam_root = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", "")
    if steam_root:
        dirs.append(os.path.join(steam_root, "steamapps", "common"))
    for lib in os.environ.get("STEAM_COMPAT_LIBRARY_PATHS", "").split(":"):
        if lib:
            dirs.append(os.path.join(lib, "common"))
    for base in (
        os.path.expanduser("~/.local/share/Steam"),
        os.path.expanduser("~/.steam/root"),
        os.path.expanduser("~/.steam/steam"),
    ):
        dirs.append(os.path.join(base, "steamapps", "common"))
    return list(dict.fromkeys(dirs))  # deduplicate, preserve order


def _normalize_shortcut_gameid(val):
    """A non-Steam shortcut's 64-bit gameid, reduced to its 32-bit appid.

    For a shortcut Steam sets `SteamGameId` to `(appid << 32) | 0x02000000`
    and leaves `SteamAppId` at 0, while `STEAM_COMPAT_DATA_PATH` still ends
    in the 32-bit appid. Taking SteamGameId verbatim named this wrapper's log
    `greenboost-proton-14063739891024396288.log` while the Suite, which only
    ever has the 32-bit id, watched `greenboost-proton-3274469611.log`. Two
    files, one launch, and a launch status that could never see any progress
    the wrapper reported. The 32-bit appid wins because everything else is
    already keyed by it: the compatdata directory, the per-game profile, the
    compat-tool mapping.
    """
    if not val.isdigit():
        return val
    try:
        gid = int(val)
    except ValueError:
        return val
    if gid > 0xFFFFFFFF and (gid & 0xFFFFFFFF) == 0x02000000:
        return str(gid >> 32)
    return val


def _resolve_appid():
    """The AppID, by every route Steam might have given it to us.

    `SteamGameId` alone is not enough: dataflux `gaming_session` events were
    landing with `"appid": ""` (confirmed 2026-08-20), which means
    analyze_game_sessions() could never match a game and the Games view's
    VRAM-risk badge stayed dark no matter how many sessions were recorded.
    Non-Steam shortcuts and some launch paths set only a subset of these.
    """
    for var in ("SteamGameId", "SteamAppId", "STEAM_APPID"):
        val = os.environ.get(var, "").strip()
        if val:
            return _normalize_shortcut_gameid(val)
    # Every Proton launch gets a compat-data dir named after the AppID.
    compat = os.environ.get("STEAM_COMPAT_DATA_PATH", "").strip().rstrip("/")
    if compat:
        base = os.path.basename(compat)
        if base.isdigit():
            return base
    # Steam's reaper passes `AppId=<n>` through argv on some launch paths.
    for arg in sys.argv[1:]:
        if arg.startswith("AppId="):
            tail = arg.split("=", 1)[1].strip()
            if tail.isdigit():
                return tail
    return ""


def _find_proton_experimental():
    """Any installed Proton that calls itself Experimental.

    Was an exact match on "Proton - Experimental" and nothing else, so a user
    with only Proton-CachyOS or a differently-named Experimental build hit a
    hard `sys.exit(1)` and had to edit this function by hand to launch
    anything , reported by a user 2026-08-20. Now a substring match, with the
    canonical name still preferred so nothing changes for the common case.
    """
    fallback = None
    for d in _steam_common_dirs():
        p = os.path.join(d, "Proton - Experimental", "proton")
        if os.path.isfile(p):
            return p
        if not os.path.isdir(d):
            continue
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for entry in entries:
            name = entry.lower()
            if "experimental" not in name or "greenboost" in name:
                continue
            cand = os.path.join(d, entry, "proton")
            if os.path.isfile(cand) and fallback is None:
                fallback = cand
    return fallback


def _find_proton_override():
    """An explicit upstream Proton, from GREENBOOST_PROTON_UPSTREAM.

    Accepts either the `proton` script itself or the directory holding it, so
    the Global Settings picker can store whichever the user selected. Returns
    None (and says why) rather than failing the launch, so a stale setting
    degrades to auto-discovery instead of blocking the game.
    """
    raw = os.environ.get("GREENBOOST_PROTON_UPSTREAM", "").strip()
    if not raw:
        # Read the setting directly rather than waiting for as_env_dict():
        # upstream Proton has to be resolved in main(), well before
        # _run_greenboost_launch() gets around to applying global settings.
        try:
            # Same bootstrap the other gb_gaming importers use , this runs
            # from main(), before any of them have touched sys.path.
            for _p in (os.path.expanduser("~/.local/lib/greenboost-gaming"),
                       "/usr/local/lib/greenboost-gaming",
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")):
                if _p not in sys.path:
                    sys.path.insert(0, _p)
            from gb_gaming import global_settings as _gs
            raw = (getattr(_gs.load(), "proton_upstream", "") or "").strip()
        except Exception:
            raw = ""
    if not raw:
        return None
    path = os.path.expanduser(raw)
    if os.path.isdir(path):
        path = os.path.join(path, "proton")
    if os.path.isfile(path):
        return path
    sys.stderr.write(
        "[greenboost-proton] GREENBOOST_PROTON_UPSTREAM points at %s, which is "
        "not a Proton install , ignoring it and auto-detecting instead.\n" % raw)
    return None


def _find_proton_stable():
    """B6: Discovers the newest stable Proton (Valve or GE/custom) across all Steam roots.
    Priority: numeric (major, minor) → highest wins.  GE releases sort by
    GE-<n> suffix; custom builds with no numeric version sort last."""
    best_key = (-1, -1, -1)  # (major, minor, ge_n)
    best_path = None
    for d in _steam_common_dirs():
        if not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for entry in entries:
            p = os.path.join(d, entry, "proton")
            if not os.path.isfile(p):
                continue
            # Valve: "Proton 10.0", "Proton 9.0"
            m = re.match(r'^Proton\s+(\d+)\.(\d+)$', entry)
            if m:
                key = (int(m.group(1)), int(m.group(2)), 0)
            else:
                # GE: "GE-Proton9-26", "Proton-GE-Proton8-32", "ProtonGE-9-5"
                m2 = re.search(r'(\d+)[.\-_](\d+)(?:[.\-_]GE[.\-_]?(\d+))?', entry, re.I)
                if not m2:
                    # Unknown custom build , admit it but give lowest priority
                    key = (-1, -1, 0)
                else:
                    ge_n = int(m2.group(3)) if m2.group(3) else 0
                    key = (int(m2.group(1)), int(m2.group(2)), ge_n)
            if key > best_key:
                best_key, best_path = key, p
    return best_path


# ── Main ───────────────────────────────────────────────────────────────────────

class _TeeStderr:
    """Duplicates every write to the original stderr (journalctl still sees
    everything, exactly as before) AND to a per-appid file under
    proton-logs/ that upstream Proton can't truncate out from under us ,
    unlike steam-<appid>.log, which Proton opens (and truncates) itself the
    moment it starts, discarding every line the wrapper printed before
    that point. Confirmed live 2026-08-08: a launch that visibly ran
    GreenBoost code (confirmed only via `journalctl --user -t steam`)
    looked, from steam-<appid>.log alone, like GreenBoost had never
    participated , a false "layer manifest missing" pre-flight warning and
    a real `gaming_mode` EROFS failure were both invisible there. Diagnosing
    that cost real time; this is the fix. Best-effort: a failure to open
    the log file just means no local copy , stderr still reaches
    journalctl exactly as it always has.
    """
    def __init__(self, original, log_path):
        self._original = original
        try:
            self._file = open(log_path, "a", buffering=1)
        except OSError:
            self._file = None

    def write(self, data):
        self._original.write(data)
        if self._file is not None:
            try:
                self._file.write(data)
            except OSError:
                self._file = None
        return len(data)

    def flush(self):
        self._original.flush()
        if self._file is not None:
            try:
                self._file.flush()
            except OSError:
                pass


# Steam runs this same compat tool for things that are not the game.
# `iscriptevaluator.exe` is the important one: Steam BLOCKS on it before it
# launches anything. Confirmed live 2026-08-21 , that helper came through
# here and got the full game treatment (subreaper, session record, power
# lock + watchdog, DXVK/gplasync staging, its own
# greenboost-proton-<appid>.log), then hung. With Steam waiting on it the
# game never started, and the Suite, reading that log, reported "staging
# DXVK/VKD3D libraries" for something that was never going to become a game.
# `d3ddriverquery64.exe` and `xalia.exe` came through the same way and were
# SIGTERMed at 60 s each. Nothing GreenBoost does means anything for these.
_STEAM_INTERNAL_EXES = frozenset((
    "iscriptevaluator.exe",
    "d3ddriverquery.exe",
    "d3ddriverquery64.exe",
    "xalia.exe",
    "steamerrorreporter.exe",
    "steamerrorreporter64.exe",
    "gameoverlayui.exe",
))


def _steam_internal_helper():
    """Name of the Steam-internal helper this invocation is, or "" for a game.

    Deliberately matched on the target executable, not on the verb alone.
    Steam runs the real game with `waitforexitandrun` and helpers with `run`,
    but `run` is also the verb in the documented dry-run check and in some
    non-Steam shortcuts , silently changing what those do is a worse trade
    than missing some future helper we have not seen yet.
    """
    if os.environ.get("GREENBOOST_DRY_RUN", "0") == "1":
        return ""
    if len(sys.argv) < 3 or sys.argv[1] not in ("run", "runinprefix"):
        return ""
    target = sys.argv[2].replace("\\", "/")
    base = os.path.basename(target).lower()
    if base in _STEAM_INTERNAL_EXES:
        return base
    if "/legacycompat/" in target.lower():
        return base or "legacycompat script"
    for arg in sys.argv[1:]:
        if arg.replace("\\", "/").lower().endswith(".vdf"):
            return base or "vdf script"
    return ""


def main():
    # Install the tee before ANY other output , SteamGameId is already set
    # by Steam by the time this wrapper is exec'd, so the appid is known
    # without waiting for _run_greenboost_launch()'s own `gameid` local.
    # A Steam-internal helper gets its own shared log, never the per-appid
    # one , the Suite reads that file to describe what the LAUNCH is doing,
    # and a helper's lines there read as game progress that never arrives.
    _helper = _steam_internal_helper()
    _wrapper_log_dir = os.path.expanduser("~/.local/share/greenboost/proton-logs")
    try:
        os.makedirs(_wrapper_log_dir, exist_ok=True)
        _wrapper_log_appid = _resolve_appid() or "unknown"
        _wrapper_log_name = ("greenboost-proton-helpers.log" if _helper
                             else f"greenboost-proton-{_wrapper_log_appid}.log")
        sys.stderr = _TeeStderr(sys.stderr,
                                os.path.join(_wrapper_log_dir, _wrapper_log_name))
    except OSError:
        pass  # no local copy , stderr still reaches journalctl as before

    # ── Channel detection , read sidecar 'channel' file ──────────────────────
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _channel = "experimental"
    try:
        with open(os.path.join(_script_dir, "channel")) as _cf:
            _channel = _cf.read().strip()
    except OSError:
        pass

    # Resolution order: an explicit setting wins, then the channel this
    # install was deployed for, then the OTHER channel.
    #
    # That last step is the important one. Until 2026-08-20 a missing
    # Proton Experimental was a hard `sys.exit(1)` even when a perfectly good
    # stable Proton sat right next to it, so users on distro builds
    # (Proton-CachyOS was the reported case) had to edit this file by hand to
    # launch anything at all. Falling back is always better than refusing:
    # the worst case is a launch on a Proton the user did not pick, which we
    # say out loud, and the best case is the game just works.
    _upstream_label = "Proton Experimental"
    _upstream_install_hint = "Library → Tools → Proton Experimental → Install"
    if _channel == "stable":
        _upstream_label = "Proton (stable)"
        _upstream_install_hint = "Library → Games → search 'Proton X.Y' → Install"

    proton_upstream = _find_proton_override()
    if proton_upstream:
        _upstream_label = "Proton (GREENBOOST_PROTON_UPSTREAM)"
    else:
        _primary = _find_proton_stable if _channel == "stable" else _find_proton_experimental
        _secondary = _find_proton_experimental if _channel == "stable" else _find_proton_stable
        proton_upstream = _primary()
        if not proton_upstream:
            proton_upstream = _secondary()
            if proton_upstream:
                sys.stderr.write(
                    "[greenboost-proton] %s is not installed, using %s "
                    "instead.\n" % (_upstream_label, proton_upstream))
                _upstream_label = "Proton (fallback)"

    if not proton_upstream:
        sys.stderr.write(
            "[greenboost-proton] This game cannot start: no upstream Proton is "
            "installed for GreenBoost to build on.\n"
            "[greenboost-proton] GreenBoost is a wrapper around Proton, not a "
            "replacement for it , it needs a real Proton underneath. Nothing "
            "is broken and nothing was changed; Steam's own Proton options are "
            "untouched.\n"
            "[greenboost-proton] Install one in Steam: %s\n"
            "[greenboost-proton] Or point GreenBoost at a Proton you already "
            "have: set GREENBOOST_PROTON_UPSTREAM=/path/to/proton, or pick one "
            "in the Suite under Global Settings → Upstream Proton.\n"
            % _upstream_install_hint)
        sys.exit(1)

    if _helper:
        # Standing aside is not enough on its own. The Vulkan layer is
        # IMPLICIT: the loader activates it from GREENBOOST_VULKAN=1 in the
        # environment, no matter which proton script ran. Steam inherits that
        # variable whenever the Suite is what started Steam, so the layer was
        # loading into `d3ddriverquery64.exe` and `iscriptevaluator.exe` and
        # inflating the VRAM figures of the very probe Steam uses to decide
        # what the driver can do (seen live 2026-08-21, layer log:
        # `CreateInstance: app='unknown'` with no CreateDevice after it).
        # GREENBOOST_VULKAN_DISABLE=1 is the manifest's own documented
        # switch, and disable_environment beats enable_environment in the
        # loader, so this covers the helper and every wine process under it.
        os.environ["GREENBOOST_VULKAN_DISABLE"] = "1"
        sys.stderr.write(
            "[greenboost-proton] %s is Steam's own helper, not the game , "
            "handing it straight to %s with nothing applied (Vulkan layer "
            "disabled for it too).\n"
            % (_helper, proton_upstream))
        os.execv(proton_upstream, [proton_upstream] + sys.argv[1:])

    if os.environ.get("GREENBOOST_DISABLE", "0") == "1":
        sys.stderr.write(f"[greenboost-proton] GreenBoost disabled , delegating to {proton_upstream}\n")
        os.execv(proton_upstream, [proton_upstream] + sys.argv[1:])

    # A performance wrapper must never be able to prevent the game from
    # launching. Everything GreenBoost-specific (per-game profiles, perf
    # lock, pre-flight, NIS staging, the actual launch/monitor loop) runs
    # in _run_greenboost_launch(); any exception it doesn't already handle
    # itself is caught here, reported (stderr + dataflux), and degrades to
    # a bare upstream Proton launch instead of killing the game.
    try:
        _run_greenboost_launch(proton_upstream, _upstream_label, _upstream_install_hint, _channel)
    except SystemExit:
        raise
    except Exception as _e:
        sys.stderr.write(
            f"[greenboost-proton] internal error: {_e!r} , "
            f"disabling GreenBoost for this launch, delegating to {proton_upstream}\n")
        _df_emit({
            "kind": "gaming_session", "action": "error",
            "appid": _resolve_appid(),
            "error": repr(_e),
        })
        os.execv(proton_upstream, [proton_upstream] + sys.argv[1:])


def _run_greenboost_launch(proton_upstream, _upstream_label, _upstream_install_hint, _channel):
    gpu_name, vram_mb, is_nvidia, supports_rt = _detect_nvidia()
    total_threads, p_cores, cpu_model, p_core_cpus = _detect_cpu()
    _threads_env = os.environ.get("GREENBOOST_SHADER_THREADS", "")
    if _threads_env.isdigit() and int(_threads_env) > 0:
        compiler_threads = max(2, int(_threads_env))
    else:
        compiler_threads = max(2, p_cores)
    gameid = _resolve_appid()

    # ── B3: load per-game JSON profile (highest-precedence env overrides) ─────
    _per_game_profile = _load_per_game_json(gameid)

    # ── Per-game fixes (Engine.ini patches, cache cleanup) ───────────────────
    _restore_ue5_rt(gameid)
    _inject_ue5_rt_disable(gameid)
    _cleanup_dxr_pipeline_cache(gameid)

    # ── Per-game overrides ────────────────────────────────────────────────────
    for _key, _val in _GAME_OVERRIDES.get(gameid, {}).items():
        if _key == "WINEDLLOVERRIDES":
            _existing = os.environ.get(_key, "")
            os.environ[_key] = f"{_existing};{_val}" if _existing else _val
        else:
            os.environ.setdefault(_key, _val)

    # ── Streamline bundle sanity check ───────────────────────────────────────
    # Warn (but never block) if sl.*.dll versions in the game dir are mixed.
    # This catches the partial-upgrade case where some plugins were updated
    # but sl.interposer.dll / sl.pcl.dll were left at an older ABI.
    _sl_game_dir: str | None = None
    if len(sys.argv) >= 3 and sys.argv[1] in ("run", "runinprefix", "waitforexitandrun"):
        _sl_game_dir = os.path.dirname(os.path.abspath(sys.argv[2]))
    if _sl_game_dir and os.path.isdir(_sl_game_dir):
        try:
            import struct as _struct
            def _sl_pe_major(p):
                try:
                    data = open(p, "rb").read()
                    idx = data.find(b"\xbd\x04\xef\xfe")
                    if idx < 0 or idx + 12 > len(data):
                        return None
                    ms, = _struct.unpack_from("<I", data, idx + 8)
                    return (ms >> 16) & 0xFFFF
                except OSError:
                    return None
            import glob as _glob
            _sl_dlls = {os.path.basename(p).lower(): p
                        for p in _glob.glob(os.path.join(_sl_game_dir, "sl.*.dll"))
                        if not os.path.basename(p).lower().endswith((".bak", ".gdlss_bak"))
                        and ".bak." not in os.path.basename(p).lower()}
            if len(_sl_dlls) >= 2:
                _sl_majors = {n: _sl_pe_major(p) for n, p in _sl_dlls.items()
                              if n != "sl.common.dll"}  # sl.common uses driver-version encoding
                _sl_valid = {n: v for n, v in _sl_majors.items() if v is not None}
                _sl_major_set = set(_sl_valid.values())
                if len(_sl_major_set) > 1:
                    _sl_detail = ", ".join(
                        f"{n}={v}" for n, v in sorted(_sl_valid.items()))
                    sys.stderr.write(
                        f"[greenboost-proton] WARNING: Streamline bundle in "
                        f"{_sl_game_dir!r} is version-inconsistent "
                        f"({_sl_detail}). "
                        "Re-run 'Update DLSS' on this game from the Suite GUI.\n")
        except Exception:
            pass

    # ── GreenBoost env vars (set before Proton Experimental's init_session) ───
    os.environ.setdefault("GREENBOOST_VULKAN", "1")

    # OpenGL layer , LD_PRELOAD interposer for OpenGL / wined3d games.
    # Activated by GREENBOOST_OPENGL=1; libgb_gl.so is built alongside the Vulkan
    # layer and installed to /usr/local/lib.  The guard below searches the same
    # paths that install.sh uses so both system and user-local installs work.
    os.environ.setdefault("GREENBOOST_OPENGL", "1")
    if os.environ.get("GREENBOOST_OPENGL", "0") == "1":
        _gb_gl_lib = None
        for _p in [
            os.path.expanduser("~/.local/lib/libgb_gl.so"),
            "/usr/local/lib/libgb_gl.so",
            "/usr/lib/libgb_gl.so",
        ]:
            if os.path.exists(_p):
                _gb_gl_lib = _p
                break
        if _gb_gl_lib:
            _existing_preload = os.environ.get("LD_PRELOAD", "")
            if _gb_gl_lib not in _existing_preload.split(":"):
                os.environ["LD_PRELOAD"] = (
                    f"{_gb_gl_lib}:{_existing_preload}".strip(":")
                )
        else:
            sys.stderr.write(
                "[greenboost-proton] libgb_gl.so not found , "
                "OpenGL layer inactive (run install.sh to build it)\n"
            )

    # PR-YYY: source the user's Global Settings (DLSS preset, HDR, Wayland,
    # PROTON_DLSS_INDICATOR / UPGRADE).  The Gaming Suite GUI writes
    # ~/.config/greenboost-gaming/global_settings.json; we import the
    # accompanying Python module to translate it into env vars here.
    # Using setdefault() means per-game launch options or pre-existing
    # exports take precedence over the global defaults.
    try:
        sys.path.insert(0, os.path.expanduser("~/.local/lib/greenboost-gaming"))
        sys.path.insert(0, "/usr/local/lib/greenboost-gaming")
        from gb_gaming import global_settings as _gs
        for _gk, _gv in _gs.as_env_dict().items():
            os.environ.setdefault(_gk, _gv)
    except Exception as _e:
        sys.stderr.write(
            f"[greenboost-proton] global_settings unavailable: {_e}\n")

    # B3: per-game JSON env (overrides global_settings; .env overrides JSON).
    _apply_per_game_json_env(_per_game_profile)

    # PR-GGGG: per-game .env override (highest precedence after explicit
    # exports , overrides global_settings defaults and JSON profile).
    _apply_per_game_env(gameid)

    # Ensure the user-local layer manifest is discoverable inside the
    # pressure-vessel container (home is bindmounted; /etc/vulkan and
    # /usr/local/lib are not).  VK_LAYER_PATH only affects *explicit* layers ,
    # GreenBoost's manifest declares "type": "GLOBAL" (implicit), which the
    # loader only finds via XDG_DATA_DIRS / VK_IMPLICIT_LAYER_PATH /
    # VK_ADD_IMPLICIT_LAYER_PATH. Confirmed live 2026-08-07: with only
    # VK_LAYER_PATH set, libVkLayer_greenboost.so never appeared in the game
    # process's memory map and DXVK reported the raw (non-inflated) VRAM heap
    # size , the layer was silently never loaded. Inside pressure-vessel,
    # VK_IMPLICIT_LAYER_PATH is already pinned to PV's own overrides dir, so
    # it must not be overwritten here; VK_ADD_IMPLICIT_LAYER_PATH is additive
    # and safe to use alongside it.
    _home = os.environ.get("HOME", os.path.expanduser("~"))
    _user_layer_dir = os.path.join(_home, ".local", "share", "vulkan", "implicit_layer.d")
    _existing_add = os.environ.get("VK_ADD_IMPLICIT_LAYER_PATH", "")
    if _user_layer_dir not in _existing_add.split(":"):
        os.environ["VK_ADD_IMPLICIT_LAYER_PATH"] = (
            f"{_user_layer_dir}:{_existing_add}".rstrip(":")
        )
    # XDG_DATA_DIRS fallback for loaders that predate VK_ADD_IMPLICIT_LAYER_PATH
    # (added in the Vulkan-Loader 1.3.234 timeframe) , those scan
    # $dir/vulkan/implicit_layer.d for every entry in XDG_DATA_DIRS.
    _existing_xdg = os.environ.get("XDG_DATA_DIRS", "")
    _user_data_dir = os.path.join(_home, ".local", "share")
    if _user_data_dir not in _existing_xdg.split(":"):
        os.environ["XDG_DATA_DIRS"] = (
            f"{_user_data_dir}:{_existing_xdg}".rstrip(":")
        )

    os.environ.setdefault("PROTON_ENABLE_WAYLAND", "1")

    # VKD3D_CONFIG , DXR only on RT-capable GPUs and only for games that support it.
    # Modern vkd3d-proton auto-enables DXR on RTX GPUs by default; "no_dxr" must be
    # injected explicitly for blocklisted games , not adding "dxr" is not enough.
    if supports_rt and gameid not in _NO_DXR_GAMES and os.environ.get("GREENBOOST_NO_DXR", "0") != "1":
        _append_vkd3d("VKD3D_CONFIG", "dxr")
        _append_vkd3d("VKD3D_CONFIG", "dxr11")
    else:
        _append_vkd3d("VKD3D_CONFIG", "no_dxr")

    for opt in os.environ.get("GREENBOOST_VKD3D_CONFIG", "").split(","):
        opt = opt.strip()
        if opt:
            _append_vkd3d("VKD3D_CONFIG", opt)

    _append_vkd3d("VKD3D_CONFIG", "pipeline_library_app_cache")
    # Improves constant buffer view performance in DX12-to-Vulkan translation.
    _append_vkd3d("VKD3D_CONFIG", "force_static_cbv")
    # PR-GGGG: graphics-pipeline-library shader compilation (vkd3d-proton
    # 2.12+).  Splits each PSO into 4 sub-libraries that compile in parallel
    # and link at the end , ~5× faster wall-clock compile and async-friendly
    # (no kernel blocking).  Pair with `pipeline_library_no_serialize_spirv`
    # to skip the SPIR-V serialization round-trip that GPL otherwise does.
    _append_vkd3d("VKD3D_CONFIG", "pipeline_library_no_serialize_spirv")

    os.environ.setdefault("VKD3D_DEBUG", os.environ.get("VKD3D_DEBUG_OVERRIDE") or "warn")
    # Tell vkd3d-proton to use the same thread count we compute for DXVK.
    os.environ.setdefault("VKD3D_SHADER_COMPILER_THREADS", str(compiler_threads))

    if is_nvidia:
        # Allow vkd3d-proton to use the full available device memory;
        # the GreenBoost Vulkan layer manages spill, so be generous.
        os.environ.setdefault("VKD3D_DESCRIPTOR_POOL_SIZE", "512:131072")
        os.environ.setdefault("DXVK_ENABLE_NVAPI", "1")
        # Threaded OpenGL dispatch , significant for OpenGL games and Proton's wined3d
        # fallback (the d3d backend used when DXVK is unavailable; it runs on OpenGL).
        os.environ.setdefault("__GL_THREADED_OPTIMIZATIONS", "1")
        # Prevent driver from yielding CPU time in spin-waits; reduces frame time jitter.
        os.environ.setdefault("__GL_YIELD", "NOTHING")
        # PR-GGGG: full NVIDIA driver perf set.  Allow VRR / G-SYNC for any
        # window (not only fullscreen), opt-in to "max frames allowed = 1"
        # for Reflex-like low input latency, and let the game decide vsync
        # (the driver default of 1 forces vsync even when the game says no).
        os.environ.setdefault("__GL_GSYNC_ALLOWED", "1")
        os.environ.setdefault("__GL_VRR_ALLOWED", "1")
        os.environ.setdefault("__GL_MaxFramesAllowed", "1")
        os.environ.setdefault("__GL_SYNC_TO_VBLANK", "0")
        # Sharper LOD without forcing trilinear; default is bias 0.
        os.environ.setdefault("__GL_LOG_MAX_ANISO", "16")
        # Reflex-style low-latency Vulkan present mode (driver 535+).
        os.environ.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
        os.environ.setdefault("__VK_LAYER_NV_optimus", "NVIDIA_only")
        # PR-GGGG: NVIDIA next-gen shader compiler (driver 535+ , opt-in
        # for older versions, default for 545+).  Faster compile times for
        # complex DXIL→SPIR-V→SASS chains; small risk of compatibility
        # regressions on very old card families (Maxwell and earlier).
        os.environ.setdefault("__GL_NextGenCompiler", "1")
        # Tile cache size , 1024 is the NVIDIA-recommended value for modern
        # AAA games on Turing/Ampere/Ada/Blackwell.
        os.environ.setdefault("__GL_TileCacheSize", "1024")
        # NVIDIA shader-cache version bump cleanup is automatic, but explicit
        # is better than implicit when shipping a Steam compat tool.
        os.environ.setdefault("__GL_SHADER_DISK_CACHE_VERSION", "1")
        # Pick the primary display deterministically so __GL_SYNC_TO_VBLANK
        # doesn't race on multi-monitor setups (the wrong head can cause
        # tearing or VRR-not-applying).
        if "__GL_SYNC_DISPLAY_DEVICE" not in os.environ:
            # Try xrandr first (X11 / XWayland)
            _sync_dev = None
            try:
                out = subprocess.check_output(
                    ["xrandr", "--current"], text=True, stderr=subprocess.DEVNULL)
                for line in out.splitlines():
                    if " connected primary " in line:
                        _sync_dev = line.split()[0]
                        break
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
            # Wayland fallback: read DRM connector name from sysfs
            if not _sync_dev:
                try:
                    import glob as _glob
                    for _conn in sorted(_glob.glob("/sys/class/drm/card*/card*-*/status")):
                        if open(_conn).read().strip() == "connected":
                            # e.g. /sys/class/drm/card1/card1-HDMI-A-1/status → HDMI-A-1
                            _sync_dev = os.path.basename(os.path.dirname(_conn)).split("-", 1)[1]
                            break
                except Exception:
                    pass
            if _sync_dev:
                os.environ["__GL_SYNC_DISPLAY_DEVICE"] = _sync_dev

    # PR-GGGG: Proton parallel process initialisation and SCHED_RR , both
    # are Proton 9.0+ opt-ins.  Parallel init shaves ~1–2 s off Wine prefix
    # warm-up; SCHED_RR helps prevent priority inversion on heavy I/O
    # (loading screens) but requires the @video limits.conf entry to grant
    # RTPRIO 99 , falls back to nice otherwise, no harm done.
    os.environ.setdefault("PROTON_PARALLEL_PROCESS_INIT", "1")
    os.environ.setdefault("PROTON_SCHED_RR", "1")

    # PR-GGGG: Steam Fossilize batch tuning , process more PSOs per batch
    # so the "Processing Vulkan Shaders" stage finishes faster on first
    # launch.  Default is conservative (1 batch of 4); modern systems can
    # comfortably handle 8 batches of 8 in parallel.
    os.environ.setdefault("STEAM_FOSSILIZE_TASKS_PER_BATCH", "8")
    os.environ.setdefault("STEAM_FOSSILIZE_BATCH_COUNT", "8")

    os.environ.setdefault("DXVK_NUM_COMPILER_THREADS", str(compiler_threads))
    # PR-GGGG: framerate uncapped by default (matches DXVK upstream but
    # explicit so per-game launch options can override).
    os.environ.setdefault("DXVK_FRAME_RATE", "0")
    # PR-GGGG: HUD compiler readout opt-in for diagnosing shader-comp stalls.
    if os.environ.get("GREENBOOST_DEBUG_SHADER_COMPILE", "0") == "1":
        os.environ.setdefault("DXVK_HUD", "compiler,fps,frametimes,version,api")

    # When user explicitly enables DXVK NvAPI HUD overlay, show GPU/DLSS stats.
    if os.environ.get("GREENBOOST_NVAPI_HUD", "0") == "1":
        _existing_hud = os.environ.get("DXVK_HUD", "")
        _hud_extras = "nvapistats,dlss"
        if _existing_hud:
            os.environ["DXVK_HUD"] = f"{_existing_hud},{_hud_extras}"
        else:
            os.environ.setdefault("DXVK_HUD", _hud_extras)

    # PR-GGGG: Wine perf knobs , sync primitives, large-address-aware,
    # disable write-watch, no Wine-FSR (DLSS/FSR is the game's job).  All
    # are setdefault() so per-game / launch-option overrides stick.
    os.environ.setdefault("WINEESYNC", "1")
    os.environ.setdefault("WINEFSYNC", "1")
    os.environ.setdefault("WINEFSYNC_FUTEX2", "1")
    os.environ.setdefault("PROTON_NO_ESYNC", "0")
    os.environ.setdefault("PROTON_NO_FSYNC", "0")
    os.environ.setdefault("PROTON_FORCE_LARGE_ADDRESS_AWARE", "1")
    os.environ.setdefault("PROTON_NO_WRITE_WATCH", "1")
    os.environ.setdefault("WINE_FULLSCREEN_FSR", "0")
    os.environ.setdefault("WINE_LARGE_ADDRESS_AWARE", "1")
    # PR-GGGG: heap delay-free improves D3D9 / older D3D11 games.  Default
    # off (Proton Experimental's recommendation); opt-in per game via
    # GREENBOOST_HEAP_DELAY_FREE=1 if a specific title benefits.
    if os.environ.get("GREENBOOST_HEAP_DELAY_FREE", "0") == "1":
        os.environ.setdefault("PROTON_HEAP_DELAY_FREE", "1")

    # PR-GGGG: Mesa shader cache (covers DXVK on AMD/Intel fallback and the
    # NVK open driver).  No-op on the proprietary NVIDIA driver, harmless
    # to set.  `radeonsi`, `iris`, `nvk` all honour MESA_SHADER_CACHE_DIR.
    os.environ.setdefault("MESA_GLTHREAD", "true")
    # Default to no GL error checking , small but real driver-side speedup.
    os.environ.setdefault("MESA_NO_ERROR", "1")
    # NVK / RADV / ANV write Vulkan pipeline cache here.
    os.environ.setdefault("MESA_VK_WSI_PRESENT_MODE", "mailbox")

    # Log routing , errors and warnings only to avoid multi-GB log files.
    # Proton Experimental sets WINEDEBUG to very verbose levels when PROTON_LOG=1;
    # override it to capture crashes (seh, loaddll) without trace/unwind spam.
    gb_log_dir = os.path.expanduser("~/.local/share/greenboost/proton-logs")
    os.makedirs(gb_log_dir, exist_ok=True)
    os.environ.setdefault("PROTON_LOG", "1")
    os.environ.setdefault("PROTON_LOG_DIR", gb_log_dir)
    os.environ.setdefault("DXVK_LOG_PATH", gb_log_dir)
    # Restrict Wine debug channels: keep err+all for crash diagnostics, warn for SEH,
    # loaddll for DLL load tracking , suppress trace/unwind/mscoree/threadname.
    os.environ.setdefault("WINEDEBUG", "err+all,warn+seh,+loaddll,-threadname,-unwind,-mscoree")

    # Shader caches
    gb_cache_dir = os.path.expanduser("~/.local/share/greenboost/proton-cache")
    for sub in ("gl-shaders", "dxvk-state", "vkd3d-shader", "mesa-shader"):
        os.makedirs(os.path.join(gb_cache_dir, sub), exist_ok=True)

    if is_nvidia:
        os.environ.setdefault("__GL_SHADER_DISK_CACHE", "1")
        os.environ.setdefault("__GL_SHADER_DISK_CACHE_PATH", os.path.join(gb_cache_dir, "gl-shaders"))
        _cache_gb = int(os.environ.get("GREENBOOST_SHADER_CACHE_GB", "8"))
        os.environ.setdefault("__GL_SHADER_DISK_CACHE_SIZE", str(_cache_gb * 1024 * 1024 * 1024))
        os.environ.setdefault("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP", "1")
        os.environ.setdefault("DXVK_STATE_CACHE", "1")
        os.environ.setdefault("DXVK_STATE_CACHE_PATH", os.path.join(gb_cache_dir, "dxvk-state"))

    os.environ.setdefault("VKD3D_SHADER_CACHE_PATH", os.path.join(gb_cache_dir, "vkd3d-shader"))
    os.environ.setdefault("MESA_SHADER_CACHE_DIR", os.path.join(gb_cache_dir, "mesa-shader"))

    # ── GreenBoost Vulkan layer , memory budget ───────────────────────────────
    # Lower overflow threshold for gaming: start T2 fallback at 32 MB (vs 64 MB for AI)
    os.environ.setdefault("GREENBOOST_VK_OVERFLOW_MIN_MB", "32")
    # Tell the Vulkan layer how much T2 budget is available from the kernel module
    virt_gb = _read_sysfs_int("/sys/module/greenboost/parameters/virtual_vram_gb")
    if virt_gb > 0:
        os.environ.setdefault("GREENBOOST_VK_T2_BUDGET_MB", str(virt_gb * 1024))
    total_pool_mb = vram_mb + (virt_gb * 1024) if virt_gb > 0 else vram_mb

    # ── GREENBOOST_AFFINITY , pcores | numa | all (default) ───────────────────
    # "pcores": pin to P-core siblings only (previous hard-coded behaviour).
    # "numa":   P-cores intersected with the GPU's local NUMA node (previous
    #           hard-coded behaviour when both applied).
    # "all":    no restriction , every logical CPU, P- and E-core, stays
    #           available. Now the default: confirmed live 2026-08-07 that
    #           a real UE4 title (The First Berserker: Khazan) spawns 150+
    #           threads on an 8P/16E/32-thread i9-14900KF, so pinning to the
    #           16 P-core threads left 16 E-core threads idle for the
    #           duration of the session. "pcores"/"numa" remain available
    #           for titles that measurably do better with less scheduler
    #           jitter on the render thread , this needs a real frametime
    #           A/B per game, not a blanket assumption either way.
    _affinity_mode = os.environ.get("GREENBOOST_AFFINITY", "all").strip().lower()
    if _affinity_mode not in ("all", "pcores", "numa"):
        sys.stderr.write(
            f"[greenboost-proton] GREENBOOST_AFFINITY={_affinity_mode!r} not "
            "recognised (all|pcores|numa) , using 'all'\n")
        _affinity_mode = "all"

    # ── WINE_CPU_TOPOLOGY , report only P-cores to Windows games ─────────────
    # Prevents DXVK/VKD3D and the game's own thread pool from scheduling render
    # threads on E-cores, which have lower IPC and a different cache topology.
    # Only meaningful when the process affinity itself is also P-core-only ,
    # reporting a P-core-only topology while leaving E-cores schedulable
    # would just make Wine's own thread pool under-use what's available.
    if _affinity_mode != "all" and p_core_cpus and len(p_core_cpus) < total_threads:
        os.environ.setdefault(
            "WINE_CPU_TOPOLOGY",
            "{:d}:{:s}".format(len(p_core_cpus), ",".join(str(c) for c in p_core_cpus)),
        )

    # ── Status line ────────────────────────────────────────────────────────────
    sys.stderr.write(
        "[greenboost-proton] GPU: {} {} MiB  VirtualPool:{} MiB  RT:{}  Wayland:{}  NVAPI:{}\n".format(
            gpu_name or "unknown", vram_mb, total_pool_mb,
            int(supports_rt),
            os.environ.get("PROTON_ENABLE_WAYLAND", "0"),
            os.environ.get("DXVK_ENABLE_NVAPI", "0"),
        )
    )
    _affinity_str = (
        "P-cores {:s}".format(str(sorted(p_core_cpus)))
        if _affinity_mode != "all" and p_core_cpus and len(p_core_cpus) < total_threads
        else "all {:d} threads".format(total_threads)
    )
    sys.stderr.write(
        "[greenboost-proton] CPU: {}  P-cores:{}  threads:{}  compiler-threads:{}  affinity:{}\n".format(
            cpu_model or "unknown", p_cores, total_threads,
            os.environ.get("DXVK_NUM_COMPILER_THREADS", str(compiler_threads)),
            _affinity_str,
        )
    )
    sys.stderr.write(
        "[greenboost-proton] VKD3D_CONFIG={}  GREENBOOST_VULKAN={}\n".format(
            os.environ.get("VKD3D_CONFIG", "<defaults>"),
            os.environ.get("GREENBOOST_VULKAN", "0"),
        )
    )
    sys.stderr.write(f"[greenboost-proton] Delegating to: {proton_upstream}  (channel={_channel})\n")

    # ── CPU affinity , governed by GREENBOOST_AFFINITY (see above) ───────────
    # "all" (default): no sched_setaffinity call at all , every CPU stays
    #   schedulable, which is what a 150+-thread UE4/UE5 title actually wants
    #   on a hybrid CPU (see comment at the GREENBOOST_AFFINITY read, above).
    # "pcores": restrict to P-core siblings only, no NUMA intersection.
    # "numa": P-cores intersected with the GPU's local NUMA node , halves
    #   cross-node DDR traffic for shader upload buffers and reduces
    #   vkd3d/DXVK command-buffer staging latency, at the cost of fewer
    #   usable threads; only applied when it leaves ≥4 CPUs.
    #
    # The affinity (when set) is inherited by Proton → Wine → game executable.
    sys.stderr.write(f"[greenboost-proton] CPU affinity mode: {_affinity_mode}\n")
    _target_cpus = set()
    if _affinity_mode in ("pcores", "numa"):
        _target_cpus = set(p_core_cpus) if (p_core_cpus and len(p_core_cpus) < total_threads) else set()
    if _affinity_mode == "numa":
        gpu_numa  = _get_gpu_numa_node()
        numa_cpus = _numa_local_cpus(gpu_numa)  # empty when single-NUMA / unknown
        if _target_cpus and numa_cpus:
            # Intersect: P-cores that are also on the GPU's NUMA node.
            numa_local_p = _target_cpus & set(numa_cpus)
            if numa_local_p:
                # Only apply NUMA restriction when it leaves ≥4 CPUs; otherwise the
                # CPU count would be too low and Wine threadpool would stall.
                _target_cpus = numa_local_p if len(numa_local_p) >= 4 else _target_cpus
                sys.stderr.write(
                    f"[greenboost-proton] NUMA: GPU on node {gpu_numa} "
                    f"→ affinity restricted to {sorted(_target_cpus)}\n"
                )
        elif numa_cpus and not _target_cpus:
            # Uniform CPU (AMD/non-hybrid): only apply NUMA restriction if multi-NUMA.
            if gpu_numa >= 0 and len(numa_cpus) < total_threads:
                _target_cpus = set(numa_cpus)
                sys.stderr.write(
                    f"[greenboost-proton] NUMA: GPU on node {gpu_numa} "
                    f"→ affinity restricted to node-local CPUs {sorted(_target_cpus)}\n"
                )
    if _target_cpus:
        try:
            os.sched_setaffinity(0, _target_cpus)
        except OSError:
            pass  # Non-fatal: cgroup or capability restriction

    # ── Process priority , elevate game above background nice-0 tasks ─────────
    # Requires /etc/security/limits.d/99-greenboost-gaming.conf: @video hard nice -5
    # (installed by greenboost_setup.sh full-install / install-sys-configs)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, -5)
    except OSError:
        pass  # Non-fatal: falls back to default nice 0

    # ── I/O priority , best-effort class 2, level 0 (highest BE) ─────────────
    try:
        subprocess.run(
            ["ionice", "-c2", "-n0", "-p", str(os.getpid())],
            check=False, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # ionice not available on this system

    # ── Pre-flight checks (non-fatal status line) ──────────────────────────────
    _preflight(_per_game_profile)

    # ── PR-GGGG: rotate old logs before opening a new one ──────────────────────
    _rotate_proton_logs()

    # ── PR-GGGG: launch-time benchmark anchor ──────────────────────────────────
    _launch_t0 = time.monotonic()

    # ── PR-GGGG: memory subsystem prep (memlock, THP advisory) ─────────────────
    _prep_memory()

    # ── PR-GGGG: CPU governor + GPU power lock (restored in finally) ───────────
    _perf_lock = _PerfLock()
    _perf_lock.acquire(appid=str(gameid))

    # ── PR-GGGG: desktop compositor suspend (restored in finally) ──────────────
    _desktop = _DesktopSuspend()
    # Share _PerfLock's crash-safe baseline , see _DesktopSuspend.acquire().
    _desktop.acquire(baseline=getattr(_perf_lock, "baseline", None))

    # ── PR-GGGG: warm the GreenBoost DDR pool so first scene-transition
    #     overflow doesn't fault-in pages mid-frame ─────────────────────────────
    _prewarm_ddr_pool()

    # A "greenboost-gaming.service" systemctl start/stop pair used to live
    # here, unconditionally, on every launch and every exit. No such unit
    # ships anywhere in this repo (system or --user) and never has ,
    # confirmed live 2026-08-08 (`systemctl --user status
    # greenboost-gaming.service` → unit not found). Both calls used
    # `check=False, stderr=DEVNULL`, so the failure was invisible: a
    # completely silent no-op on every single game launch. Removed rather
    # than shipped, since nothing in the codebase defines what this unit
    # was actually meant to do , inventing one now would be guessing at a
    # feature, not fixing a bug. The fan daemon's real unit is
    # scripts/gb-gaming-fan-daemon.service, unaffected by this.
    # Not on a dry run. The dry-run exit below is outside the session
    # try/finally, so a start emitted here would never get its matching stop
    # , a phantom session in that appid's history, which is what the Games
    # view's VRAM-risk badge and analyze_game_sessions() read. Seen live
    # 2026-08-21: two dry runs, two starts, no stops.
    if os.environ.get("GREENBOOST_DRY_RUN", "0") != "1":
        _df_emit({"kind": "gaming_session", "action": "start",
                  "appid": gameid, "gpu": gpu_name})

    # ── PR-GGGG: dxvk-gplasync DLL staging ─────────────────────────────────────
    # Stages a background helper that, once upstream Proton has set up the
    # prefix (and copied stock DXVK DLLs into system32/syswow64), overlays
    # the gplasync build of d3d{9,10core,11}.dll + dxgi.dll.  This gives
    # FF XVI–class titles VK_EXT_graphics_pipeline_library async compilation,
    # eliminating most "Processing Vulkan Shaders" stalls during gameplay.
    #
    # Opt-out:  GREENBOOST_GPLASYNC=0
    # Version:  GREENBOOST_GPLASYNC_VERSION (default: "current" symlink)
    if os.environ.get("GREENBOOST_GPLASYNC", "1") != "0":
        _stage_gplasync_async()

    # A previous session that died hard left its power baseline on disk and
    # its settings applied. Restore those before this session captures its
    # own , otherwise we would record the CRASHED session's state as the
    # baseline and make it permanent.
    try:
        from gb_gaming import power_baseline as _pb_sweep
        for _rec in _pb_sweep.restore_stale():
            # Deliberately NOT an f-string with a nested conditional: this file
            # is parsed by Steam's sniper-container python3 (3.9), not the host
            # interpreter. See the module docstring's MINIMUM PYTHON note.
            _how = ("restored" if _rec.get("restored")
                    else "needs root, run " + str(_rec.get("_script")))
            sys.stderr.write(
                "[greenboost-proton] recovered power settings left by a "
                "crashed session (appid %s): %s\n" % (_rec.get("appid"), _how))
    except Exception:
        pass

    # Own the process tree before anything is spawned , subreaper first, so
    # no descendant can escape to init between here and the launch.
    _own_game_tree(gameid, prefix=os.environ.get("STEAM_COMPAT_DATA_PATH", ""))
    _install_stop_handler()

    # ── B4: build launch argv with optional gamescope/mangohud/gamemode wrappers ─
    _launch_argv = _build_launch_argv([proton_upstream] + sys.argv[1:], _per_game_profile)

    # ── B9: dry-run / env linter ─────────────────────────────────────────────────
    if os.environ.get("GREENBOOST_DRY_RUN", "0") == "1":
        # Pass what we acquired , this exit skips the session try/finally.
        _dry_run_dump(proton_upstream, _launch_argv,
                      perf_lock=_perf_lock, desktop=_desktop)  # exits

    # ── B5: pre-launch hooks ──────────────────────────────────────────────────────
    _run_hooks("pre", gameid, gpu_name)

    # B7/B8: capture ISO timestamp for syslog harvest at session end
    _launch_ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    # ── Peak/avg VRAM + T2 spill tracker , samples every 5 s, starting
    # immediately (not after a 30 s delay, which used to mean every session
    # shorter than 30 s recorded peak_vram_mb=0). Runs in a daemon thread so
    # it is killed automatically when the main process exits. Non-fatal:
    # any error is silently ignored , this is telemetry, never a launch gate.
    #
    # Seeded at 0, not `vram_mb` , that variable holds the GPU's *total*
    # capacity (nvidia-smi memory.total, from _detect_nvidia() above), not
    # actual usage. The tracker below only raises the holder when a real
    # NVML sample exceeds it (`if _used_mb > holder[0]`); physical VRAM
    # usage can't exceed the card's own total capacity, so seeding with the
    # capacity made that comparison structurally always false , peak_vram_mb
    # was silently just the static GPU capacity every session, never the
    # game's real peak VRAM footprint.
    #
    # Sampling prefers per-process NVML accounting (this process's own
    # descendant PID tree , the whole wine/proton tree lives under it), not
    # whole-GPU `nvmlDeviceGetMemoryInfo`, so the figure reflects THIS game,
    # not the desktop/compositor/anything else resident on the card. Falls
    # back to whole-GPU when per-process accounting is unavailable (older
    # driver, WDDM NVML_VALUE_NOT_AVAILABLE, or genuinely nothing matched)
    # , `vram_source` records which mode produced the number so the UI can
    # caveat it instead of presenting both identically.
    import threading as _threading
    import ctypes as _ctypes
    _peak_vram_mb_holder = [0]           # mutable container so the thread can write
    _vram_avg_state = {"sum_mb": 0, "samples": 0, "source": "gpu_total"}
    _peak_t2_mb_holder = [0]
    _stop_vram_tracker = _threading.Event()

    def _vram_tracker_worker(peak_holder, avg_state, t2_holder, stop_event):
        try:
            _nvml = None
            for _name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
                try:
                    _nvml = _ctypes.CDLL(_name)
                    break
                except OSError:
                    continue
            if _nvml is None or _nvml.nvmlInit_v2() != 0:
                return
            _dev = _ctypes.c_void_p()
            if _nvml.nvmlDeviceGetHandleByIndex(_ctypes.c_uint(0), _ctypes.byref(_dev)) != 0:
                return

            class _NvmlMemory(_ctypes.Structure):
                _fields_ = [("total", _ctypes.c_uint64),
                             ("free",  _ctypes.c_uint64),
                             ("used",  _ctypes.c_uint64)]

            # Current NVML ABI (v2/v3) process-info layout , pid +
            # usedGpuMemory + MIG instance ids. Driver versions old enough
            # to use the 2-field legacy struct are out of scope here.
            class _NvmlProcessInfo(_ctypes.Structure):
                _fields_ = [("pid",               _ctypes.c_uint32),
                            ("usedGpuMemory",      _ctypes.c_uint64),
                            ("gpuInstanceId",      _ctypes.c_uint32),
                            ("computeInstanceId",  _ctypes.c_uint32)]

            NVML_SUCCESS = 0
            NVML_ERROR_INSUFFICIENT_SIZE = 7
            NVML_VALUE_NOT_AVAILABLE = (1 << 64) - 1  # -1 reinterpreted as u64

            def _resolve_process_fns():
                # Try newest ABI first, fall back for older drivers. Games
                # on Proton typically show up as graphics-context processes
                # (Vulkan/OpenGL via DXVK/wined3d); compute-context is
                # queried too for CUDA-using titles.
                for suffix in ("_v3", "_v2", ""):
                    fns = []
                    for base in ("nvmlDeviceGetGraphicsRunningProcesses",
                                 "nvmlDeviceGetComputeRunningProcesses"):
                        fn = getattr(_nvml, base + suffix, None)
                        if fn is not None:
                            fns.append(fn)
                    if fns:
                        return fns
                return []

            _process_fns = _resolve_process_fns()

            def _query_processes(fn):
                count = _ctypes.c_uint32(0)
                rc = fn(_dev, _ctypes.byref(count), None)
                if rc not in (NVML_SUCCESS, NVML_ERROR_INSUFFICIENT_SIZE) or count.value == 0:
                    return {}
                arr = (_NvmlProcessInfo * count.value)()
                if fn(_dev, _ctypes.byref(count), arr) != NVML_SUCCESS:
                    return {}
                return {arr[i].pid: arr[i].usedGpuMemory for i in range(count.value)
                        if arr[i].usedGpuMemory != NVML_VALUE_NOT_AVAILABLE}

            def _descendant_pids(root_pid):
                # Full descendant set of root_pid via every /proc/*/stat
                # PPid chain , the wine/proton tree is entirely under this
                # wrapper's own pid, so this needs no game-specific knowledge.
                children = {}
                try:
                    proc_entries = os.listdir("/proc")
                except OSError:
                    return set()
                for entry in proc_entries:
                    if not entry.isdigit():
                        continue
                    try:
                        with open(f"/proc/{entry}/stat") as f:
                            stat = f.read()
                        # comm field can contain "(" / ")"; split after the
                        # LAST ")" to skip it reliably (standard proc(5) advice).
                        after = stat.rsplit(")", 1)[1].split()
                        ppid = int(after[1])
                    except (OSError, IndexError, ValueError):
                        continue
                    children.setdefault(ppid, []).append(int(entry))
                seen, stack = set(), [root_pid]
                while stack:
                    for child in children.get(stack.pop(), ()):
                        if child not in seen:
                            seen.add(child)
                            stack.append(child)
                return seen

            def _sample_mb():
                """Returns (used_mb, source); source is 'process' or 'gpu_total'."""
                if _process_fns:
                    pids = _descendant_pids(os.getpid())
                    pids.add(os.getpid())
                    total_bytes, matched = 0, False
                    for fn in _process_fns:
                        for pid, used in _query_processes(fn).items():
                            if pid in pids:
                                total_bytes += used
                                matched = True
                    if matched:
                        return total_bytes // (1024 * 1024), "process"
                _m = _NvmlMemory()
                if _nvml.nvmlDeviceGetMemoryInfo(_dev, _ctypes.byref(_m)) == 0:
                    return int(_m.used) // (1024 * 1024), "gpu_total"
                return None, None

            _sum_mb, _samples = 0, 0
            while True:
                _used_mb, _source = _sample_mb()
                if _used_mb is not None:
                    if _used_mb > peak_holder[0]:
                        peak_holder[0] = _used_mb
                    _sum_mb += _used_mb
                    _samples += 1
                    avg_state["sum_mb"] = _sum_mb
                    avg_state["samples"] = _samples
                    avg_state["source"] = _source
                _t2_mb, _t3_mb = _check_t2t3_pressure(gameid, gpu_name)
                if _t2_mb is not None and _t2_mb > t2_holder[0]:
                    t2_holder[0] = _t2_mb
                if stop_event.wait(5):
                    break
            _nvml.nvmlShutdown()
        except Exception:
            pass

    _vram_thread = _threading.Thread(
        target=_vram_tracker_worker,
        args=(_peak_vram_mb_holder, _vram_avg_state, _peak_t2_mb_holder, _stop_vram_tracker),
        daemon=True, name="gb-vram-tracker")
    _vram_thread.start()

    rc = 0
    try:
        # subprocess.run inherits our modified os.environ automatically (no env= needed).
        # Proton Experimental's proton script uses sys.argv[0] to find its own files/,
        # protonfixes/, and Wine binaries , all supplied by the Proton Experimental install.
        rc = subprocess.run(_launch_argv).returncode
        # PR-GGGG: session duration summary , useful for spotting regressions in
        # crash rates or first-launch shader-compile time.
        _elapsed = time.monotonic() - _launch_t0
        _hh = int(_elapsed) // 3600
        _mm = (int(_elapsed) % 3600) // 60
        _ss = int(_elapsed) % 60
        sys.stderr.write(
            f"[greenboost-proton] session ended rc={rc} after "
            f"{_hh:02d}h{_mm:02d}m{_ss:02d}s\n")
    except KeyboardInterrupt:
        # _install_stop_handler() already stopped the game tree; this just
        # unwinds cleanly so the finally block below still restores the perf
        # lock, the compositor and gaming_mode, and still writes the session
        # summary. 143 = 128 + SIGTERM, the conventional "stopped by signal".
        rc = 143
        sys.stderr.write("[greenboost-proton] session stopped on request\n")
    finally:
        # Matching "greenboost-gaming.service" stop call removed , see the
        # comment at the start-side call above.
        # ── B8: session summary , single source of truth, single sink.
        # `sessions.jsonl` used to be a second, independently-written record
        # of the same data (see gb_gaming/greenboost_gaming_polish.md's G3);
        # unified into the dataflux `gaming_session` stop event below, which
        # already carried appid/gpu/elapsed_s/rc/peak_vram_mb , only
        # `vram_mb` (GPU total capacity, for the frontend's "peak/total"
        # display) was missing, added here. `analyze_game_sessions_impl`/
        # `get_session_history_impl` (manager.rs) now read this event kind
        # directly instead of a separate file.
        _stop_vram_tracker.set()
        # Drop the session record first: from here on this wrapper is no
        # longer a thing the Suite should try to signal.
        _release_game_tree()
        _vram_samples = _vram_avg_state["samples"]
        _session_summary = {
            "elapsed_s": time.monotonic() - _launch_t0,
            "vram_mb": vram_mb,
            "peak_vram_mb": _peak_vram_mb_holder[0],
            "avg_vram_mb": (_vram_avg_state["sum_mb"] // _vram_samples) if _vram_samples else 0,
            "vram_samples": _vram_samples,
            "vram_source": _vram_avg_state["source"],
            "peak_t2_mb": _peak_t2_mb_holder[0],
            "rc": rc,
        }
        _df_emit({"kind": "gaming_session", "action": "stop",
                  "appid": gameid, "gpu": gpu_name, **_session_summary})
        # PR-GGGG: restore the system perf state we changed at startup.
        try:
            _perf_lock.release()
        except Exception:
            pass
        try:
            _desktop.release()
        except Exception:
            pass
        # ── B5: post-launch hooks ─────────────────────────────────────────────
        try:
            _run_hooks("post", gameid, gpu_name)
        except Exception:
            pass
        # ── B7: harvest Vulkan layer stats from syslog ────────────────────────
        try:
            _harvest_layer_stats(gameid, _launch_ts_iso)
        except Exception:
            pass

    sys.exit(rc)


main()
