use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use tauri::ipc::Channel;
use regex::Regex;
use serde::Serialize;

/// Read the last `limit` events from core GreenBoost's shared dataflux log
/// (~/.local/share/greenboost/dataflux.jsonl) , the same stream gb_quant,
/// gb_cluster, tier moves, and (as of this change) the Proton wrapper's
/// gaming_session start/stop all write to. Event shape varies by `kind`
/// (snapshot vs quantize vs gaming_session all carry different fields), so
/// this returns raw JSON values and leaves field selection to the caller ,
/// modeling every kind as a fixed Rust struct would break the moment core
/// adds a field, which happens routinely per its own dataflux rule.
/// Missing file (core not installed, or nothing emitted yet) returns empty,
/// not an error , this panel is supplementary, not load-bearing.
pub fn get_dataflux_recent_impl(limit: usize) -> Vec<serde_json::Value> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let path = std::path::PathBuf::from(home)
        .join(".local").join("share").join("greenboost").join("dataflux.jsonl");
    let Ok(text) = std::fs::read_to_string(&path) else { return Vec::new(); };
    // "snapshot" fires every ~5s from every gb_init-importing process , pure
    // periodic telemetry, not activity. It would drown out everything else
    // in a small recent-N window, so it's excluded from this "what is
    // GreenBoost doing" panel (still queryable in full via the dataflux MCP).
    text.lines().rev()
        .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .filter(|v| v.get("kind").and_then(|k| k.as_str()) != Some("snapshot"))
        .take(limit)
        .collect()
}

pub fn stream_layer_log_impl(channel: Channel<String>) {
    // `greenboost_vulkan_layer.c` calls openlog("VK_LAYER_GREENBOOST", ...),
    // so its syslog entries carry that SYSLOG_IDENTIFIER field. The OpenGL
    // layer (`greenboost_gl_layer.c`) never calls openlog() at all, so its
    // entries fall back to glibc's default identifier (the host process's
    // own name, e.g. the game binary , not a fixed string we can filter
    // on). A SYSLOG_IDENTIFIER field match therefore can never see GL
    // layer output, regardless of what string is used. Both layers do tag
    // every message body with a fixed prefix ("[VK_LAYER_GREENBOOST] " /
    // "[GB_GL] ") though, so match on MESSAGE content instead , this is
    // the only filter that reaches both layers' `GreenBoost|...` /
    // `GreenBoost-GL|...` stat lines (and their surrounding log chatter).
    let child = Command::new("journalctl")
        .args(["--user", "--follow", "-o", "cat",
               "-g", r"^\[(VK_LAYER_GREENBOOST|GB_GL)\]"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn();

    match child {
        Err(e) => {
            let _ = channel.send(format!("[error] journalctl: {e}"));
        }
        Ok(mut proc) => {
            let stdout = proc.stdout.take().unwrap();
            let reader = BufReader::new(stdout);
            for line in reader.lines() {
                match line {
                    Ok(l) => {
                        if channel.send(l).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
            let _ = proc.kill();
        }
    }
}

/// Last gaming_mode state this process itself wrote, so `sync_gb_gaming_mode`
/// only touches sysfs on an actual transition rather than every 2s poll tick.
static GB_GAMING_MODE_ACTIVE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

const GB_GAMING_MODE_PATH: &str = "/sys/module/greenboost/parameters/gaming_mode";

/// Write greenboost.ko's gaming_mode sysfs param, host-side.
///
/// The Proton wrapper (greenboost_proton/proton) tried this too, but it
/// always runs INSIDE Steam's pressure-vessel container, which bind-mounts
/// /sys read-only , confirmed live 2026-08-07, the write there fails with
/// EROFS on every launch regardless of file permissions. This process (the
/// Tauri backend) runs unconfined on the host, where the node really is
/// writable (0664 root:greenboost, invoking user in the greenboost group),
/// so this is the one path that can actually reach it. Best-effort: a
/// missing greenboost.ko (no file) or a permissions/group mismatch is
/// silently ignored , this is a perf-tuning nicety, not load-bearing.
fn write_gb_gaming_mode(active: bool) {
    let _ = std::fs::write(GB_GAMING_MODE_PATH, if active { "1" } else { "0" });
}

/// Sync gaming_mode to whether a game is currently detected as running.
/// Called from find_game_pid_impl() so it rides the Live view's existing
/// 2 s poll , no separate background thread needed.
fn sync_gb_gaming_mode(running: bool) {
    use std::sync::atomic::Ordering;
    let was_active = GB_GAMING_MODE_ACTIVE.swap(running, Ordering::Relaxed);
    if was_active != running {
        write_gb_gaming_mode(running);
    }
}

/// Steam runs its own helpers through the compat tool, under wine, in the
/// game's own prefix and with the game's `SteamGameId` , `iscriptevaluator.exe`
/// (which Steam BLOCKS on before it launches anything), `d3ddriverquery64.exe`,
/// `xalia.exe`. Each of those spawns a wine preloader that is indistinguishable
/// from the game by process name, prefix or appid. Only the command line says
/// which is which.
///
/// `steam.exe` is a different case in the SAME family: it isn't a separate
/// pre-launch compat-tool invocation, it's Steamworks' own in-prefix
/// DRM/ownership-check bootstrap, spawned as a CHILD of the real launch
/// (`waitforexitandrun ...ff7rebirth.exe` starts it internally). But the
/// practical problem is identical , its wine preloader is, for a moment,
/// indistinguishable from the game by process name, and if this poll
/// catches it in that window it reads as "the game is running" forever
/// after (the caller only re-scans for exit, not for "was this actually
/// the game"). Confirmed live 2026-08-27: multiple titles (FINAL FANTASY
/// VII REBIRTH, 007 First Light) showed Running in Steam's own UI for 45+
/// minutes with no game window and no GreenBoost error, because `steam.exe`
/// itself was hung , not the game, not GreenBoost, not this compat tool ,
/// and nothing here was watching to notice the real executable never
/// followed it.
///
/// Accepting one of these as "the game is running" reports a launch as
/// succeeded while Steam is still deciding whether to start it , the exact
/// failure LaunchStatus exists to prevent , and flips `gaming_mode` on for a
/// script evaluator. The wrapper keeps GreenBoost out of the pre-launch
/// helpers (`_steam_internal_helper` in gb_proton_main.py, which does NOT
/// list steam.exe , it's the real launch and should get full GreenBoost
/// treatment); this list is narrower and just keeps them out of the Suite's
/// idea of a running game.
const STEAM_INTERNAL_EXES: [&str; 8] = [
    "iscriptevaluator.exe",
    "d3ddriverquery.exe",
    "d3ddriverquery64.exe",
    "xalia.exe",
    "steamerrorreporter.exe",
    "steamerrorreporter64.exe",
    "gameoverlayui.exe",
    "steam.exe",
];

/// Exactly the `steam.exe` case documented on `STEAM_INTERNAL_EXES` above ,
/// split out so a caller can tell "nothing ran at all" apart from "Steam's
/// own DRM/ownership bootstrap ran and never handed off to the real game",
/// which is a different, nameable failure (`LaunchStatus::Failed`'s
/// `stuck_reason`) rather than a generic timeout.
fn is_steam_drm_bootstrap(pid: &str) -> bool {
    let Ok(raw) = std::fs::read(format!("/proc/{pid}/cmdline")) else { return false };
    String::from_utf8_lossy(&raw).replace('\0', " ").to_ascii_lowercase().contains("steam.exe")
}

/// Is `steam.exe` (Steamworks' in-prefix DRM/ownership-check bootstrap)
/// currently running anywhere on the system? Best-effort, used only to
/// build a human-readable "stuck on Steam's own DRM check" message , a
/// false positive here just makes that message slightly less specific, it
/// never blocks or delays real detection.
pub fn drm_bootstrap_running() -> bool {
    let Ok(entries) = std::fs::read_dir("/proc") else { return false };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.parse::<u32>().is_err() { continue; }
        if is_steam_drm_bootstrap(&name) { return true; }
    }
    false
}

fn is_steam_internal_helper(pid: &str) -> bool {
    let Ok(raw) = std::fs::read(format!("/proc/{pid}/cmdline")) else {
        // Unreadable cmdline: treat it as a game. A false negative here only
        // delays detection by one poll; a false positive would hide a real
        // running game from the Live view.
        return false;
    };
    let cmdline = String::from_utf8_lossy(&raw)
        .replace('\0', " ")
        .replace('\\', "/")
        .to_ascii_lowercase();
    STEAM_INTERNAL_EXES.iter().any(|exe| cmdline.contains(exe))
        || cmdline.contains("/legacycompat/")
}

/// Steam's own systemd scope names a launched game directly: on setups where
/// the client places games in one, the scope is
/// `app-steam-app<appid>-<reaperpid>.scope`. Reading it is immune to the
/// whole comm-name/cmdline guessing game above , it doesn't matter what the
/// process is called or whether it's still under a wine64-preloader comm,
/// the scope says "this IS Steam appid N" directly.
///
/// Ported from gamescope's `GetAppIdFromCgroup()`
/// (ValveSoftware/gamescope@565397f, `Utils/Process.cpp`), which replaced
/// exactly the process-tree-walking approach this file otherwise uses, for
/// exactly this reason. That commit's own message is explicit that the old
/// walk "remains as a fallback... for cases where the Steam client doesn't
/// put games it launches in a scope cgroup (currently the case on
/// desktop)" , confirmed true on THIS machine (2026-08-27): a live launch's
/// reaper sat under `app-gnome-com.ferran.greenboost-gaming-suite-*.scope`,
/// a GNOME app-launch scope, not a Steam one. So this is genuinely inert
/// here today; it's included as the free, correct fast path for setups
/// where it does apply (Steam Deck / gamescope sessions, or a future
/// desktop Steam that adopts it), with the existing exclusion-list walk
/// staying the real mechanism on this deployment , same layering Valve's
/// own commit uses, not a replacement for it.
fn appid_from_cgroup_scope(pid: &str) -> Option<u32> {
    let text = std::fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    for line in text.lines() {
        let scope = line.rsplit('/').next()?;
        let rest = scope.strip_prefix("app-steam-app")?;
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !digits.is_empty() {
            return digits.parse().ok();
        }
    }
    None
}

pub fn find_game_pid_impl() -> Option<u32> {
    let entries = std::fs::read_dir("/proc").ok()?;
    for entry in entries.flatten() {
        let fname = entry.file_name();
        let name = fname.to_string_lossy();
        if name.parse::<u32>().is_err() {
            continue;
        }
        // Fast path: a Steam app-scope cgroup names the game directly, no
        // comm/cmdline guessing needed. See appid_from_cgroup_scope's doc ,
        // inert on today's known deployment, real on others.
        if appid_from_cgroup_scope(&name).is_some() {
            if let Ok(pid) = name.parse::<u32>() {
                sync_gb_gaming_mode(true);
                return Some(pid);
            }
        }
        let comm_path = entry.path().join("comm");
        if let Ok(comm) = std::fs::read_to_string(&comm_path) {
            let comm = comm.trim();
            if comm == "wine64-preloader"
                || comm == "wine-preloader"
                || (comm.starts_with("wine") && comm.contains("preloader"))
            {
                if is_steam_internal_helper(&name) {
                    continue;
                }
                if let Ok(pid) = name.parse::<u32>() {
                    sync_gb_gaming_mode(true);
                    return Some(pid);
                }
            }
        }
    }
    sync_gb_gaming_mode(false);
    None
}

pub fn send_sigusr1_impl(pid: u32) -> Result<(), String> {
    let status = Command::new("kill")
        .args(["-USR1", &pid.to_string()])
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("kill -USR1 {pid} failed"))
    }
}

pub fn send_sigusr2_impl(pid: u32) -> Result<(), String> {
    let status = Command::new("kill")
        .args(["-USR2", &pid.to_string()])
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("kill -USR2 {pid} failed"))
    }
}

/// Live T1/T2/T3 memory-tier pool state, read straight from the kernel
/// module's `pool_brief` sysfs file. This is the un-lagged counterpart to
/// the `gaming_vram_pressure` dataflux event (which only fires from
/// `greenboost_proton/proton`'s 30s poll during an active game session):
/// this command can be polled at UI cadence for a live gauge regardless of
/// whether a game is currently running.
///
/// `pool_brief` reports T2/T3 in truncated integer GB
/// (`greenboost.c:2804` does `t2_alloc_mb / 1024`), so a game spilling
/// e.g. 700 MB renders as "0 GB". The `t2_alloc_mb`/`t2_avail_mb`/
/// `t3_alloc_mb`/`t2_fill_pct` fields below come from the companion
/// `status` sysfs file instead, which carries the same numbers at MB
/// precision. They are `None` when `status` is unreadable or doesn't
/// parse , callers should fall back to the `_gb` fields in that case,
/// never render a bare `0`.
///
/// NOTE: `pressure` (aka `PRESSURE:` in `pool_brief`) is
/// `swap_pressure` in the kernel (`greenboost.c:2796`) , i.e. **T3's**
/// pressure enum, not T2's. There is no T2 pressure enum reachable
/// outside an ioctl, so this struct does not synthesize one; T2 fill
/// is instead conveyed via `t2_fill_pct`/`t2_pct`.
#[derive(Serialize, Debug, Clone)]
pub struct PoolBrief {
    pub t1_gb:        u64,
    pub t2_alloc_gb:  u64,
    pub t2_max_gb:    u64,
    pub t2_pct:       u32,
    pub t3_alloc_gb:  u64,
    pub t3_max_gb:    u64,
    pub t3_pressure:  String,
    pub kv_reserve_mb: u64,
    pub kv_t2_mb:      u64,
    pub t2_alloc_mb:  Option<u64>,
    pub t2_avail_mb:  Option<u64>,
    pub t3_alloc_mb:  Option<u64>,
    pub t2_fill_pct:  Option<f32>,
}

/// Same line format + regex as `greenboost_proton/proton`'s
/// `_check_t2t3_pressure()` / `_POOL_BRIEF_RE`, documented in
/// greenboost.c's `pool_brief_show()`:
///   "T1:12GB T2:8/51GB(15%) T3:0/128GB PRESSURE:ok KV_RSV:2048MB KV_T2:512MB"
fn pool_brief_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(concat!(
            r"T1:(\d+)GB T2:(\d+)/(\d+)GB\((\d+)%\) T3:(\d+)/(\d+)GB ",
            r"PRESSURE:(\w+) KV_RSV:(\d+)MB KV_T2:(\d+)MB",
        ))
        .unwrap()
    })
}

/// Matches the MB-precision lines in `/sys/class/greenboost/greenboost/status`:
///   "  T2 allocated             : 0 MB  (0%)"
///   "  T2 available             : 41655 MB"
///   "  T3 allocated             : 0 MB"
fn status_mb_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?m)^\s*(T2 allocated|T2 available|T3 allocated)\s*:\s*(\d+)\s*MB").unwrap()
    })
}

/// Reads `/sys/class/greenboost/greenboost/status` for MB-precision T2/T3
/// figures. Returns `(t2_alloc_mb, t2_avail_mb, t3_alloc_mb)`, each `None`
/// individually if its line is absent , never panics, never an error, this
/// is a precision upgrade over `pool_brief`'s truncated GB, not a new
/// required dependency.
fn read_status_mb() -> (Option<u64>, Option<u64>, Option<u64>) {
    let Ok(text) = std::fs::read_to_string("/sys/class/greenboost/greenboost/status") else {
        return (None, None, None);
    };
    let mut t2_alloc = None;
    let mut t2_avail = None;
    let mut t3_alloc = None;
    for caps in status_mb_re().captures_iter(&text) {
        let val: u64 = match caps[2].parse() { Ok(v) => v, Err(_) => continue };
        match &caps[1] {
            "T2 allocated"  => t2_alloc = Some(val),
            "T2 available"  => t2_avail = Some(val),
            "T3 allocated"  => t3_alloc = Some(val),
            _ => {}
        }
    }
    (t2_alloc, t2_avail, t3_alloc)
}

/// Reads `/sys/class/greenboost/greenboost/pool_brief` and parses it.
/// Returns `None` (never an error/panic) when the kernel module isn't
/// loaded, the sysfs file is missing, or the line doesn't match the
/// expected format , this is supplementary telemetry, not load-bearing.
pub fn get_pool_brief_impl() -> Option<PoolBrief> {
    let text = std::fs::read_to_string("/sys/class/greenboost/greenboost/pool_brief").ok()?;
    let line = text.trim();
    let caps = pool_brief_re().captures(line)?;
    let g = |i: usize| caps.get(i).unwrap().as_str();
    let t2_max_gb: u64 = g(3).parse().ok()?;
    let (t2_alloc_mb, t2_avail_mb, t3_alloc_mb) = read_status_mb();
    let t2_fill_pct = t2_alloc_mb.map(|mb| (mb as f32 / (t2_max_gb.max(1) as f32 * 1024.0)) * 100.0);
    Some(PoolBrief {
        t1_gb:        g(1).parse().ok()?,
        t2_alloc_gb:  g(2).parse().ok()?,
        t2_max_gb,
        t2_pct:       g(4).parse().ok()?,
        t3_alloc_gb:  g(5).parse().ok()?,
        t3_max_gb:    g(6).parse().ok()?,
        t3_pressure:  g(7).to_string(),
        kv_reserve_mb: g(8).parse().ok()?,
        kv_t2_mb:      g(9).parse().ok()?,
        t2_alloc_mb,
        t2_avail_mb,
        t3_alloc_mb,
        t2_fill_pct,
    })
}

/// Evaluate the governed GB-Semantics segment `gaming_inference_contention`
/// (main repo: `gb_semantics.py`/`semantics/segments.yaml`) via the shared
/// `py_bootstrap` bridge , same shell-out pattern as `sources.rs`'s
/// `python_query`. Returns the raw evaluate_segment() JSON dict
/// (`segment`, `doc`, `severity`, `matched`, `evidence`) so the frontend
/// banner text can come straight from the governed segment definition
/// instead of being duplicated in Rust/TS.
///
/// Never panics: a Python/import failure or malformed JSON surfaces as
/// `Err`, which callers treat the same as "no contention" (banner hidden).
pub fn get_gaming_inference_contention_impl() -> Result<serde_json::Value, String> {
    let script = format!(
        "{bootstrap}import json\nimport gb_semantics\nprint(json.dumps(gb_semantics.evaluate_segment(\"gaming_inference_contention\")))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python3 exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    serde_json::from_str(&String::from_utf8_lossy(&out.stdout))
        .map_err(|e| format!("bad JSON from python: {e}"))
}
