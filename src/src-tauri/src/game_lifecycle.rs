// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost Gaming Suite , game process lifecycle bridge.
//
// The Suite launches a game and, until now, could never stop one: there was
// no signal delivery anywhere in the codebase, so closing the Suite left the
// game running and `gaming_mode` pinned at 1.
//
// The policy , which process owns the tree, what may be signalled, and in
// what order , lives in Python (`gb_gaming.game_lifecycle`), for the same
// reason DLSS source policy does (see sources.rs): one implementation, shared
// with the Proton wrapper that actually parents the tree, and one dataflux
// write path. This file is a thin bridge; do not re-implement the walk here.

use serde::{Deserialize, Serialize};
use std::process::Command;
use std::sync::Mutex;
use std::time::SystemTime;

/// The appid of the last game this Suite launched. Steam's URL handler gives
/// us no handle, so the appid is the only thing we can carry forward , and
/// without it a stop request cannot tell one prefix from another.
static LAUNCHED_APPID: Mutex<Option<String>> = Mutex::new(None);

pub fn note_launch(appid: &str) {
    if let Ok(mut g) = LAUNCHED_APPID.lock() {
        *g = Some(appid.to_string());
    }
}

/// How the most recent launch is actually going.
///
/// The Suite used to answer this with a sentence written the instant the
/// launch command returned , "Launching appid N , GreenBoost Proton mapping
/// verified." , which says nothing about the game, only that Steam accepted
/// the request. On 2026-08-20 a wrapper that could not even be parsed took
/// every launch down within one second, and the Games view still reported
/// that cheerful line, so the failure looked like the game "just not opening".
/// Reporting a launch as successful before anything has launched is worse
/// than reporting nothing.
#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum LaunchStatus {
    /// Nothing launched this session.
    Idle,
    /// Steam took the request; no game process yet. Normal for ~10-40 s while
    /// the runtime container and wine prefix come up.
    ///
    /// `phase` says WHAT is happening, read from the wrapper's own log rather
    /// than guessed from elapsed time, and `eta_s` is the remaining budget
    /// before this is called failed. A progress bar that only counts seconds
    /// is indistinguishable from a hang, which is exactly what the Suite
    /// looked like on 2026-08-21.
    Pending { appid: String, elapsed_s: u64, phase: String, eta_s: u64 },
    /// A wine/Proton process is running.
    Started { appid: String },
    /// No game process ever appeared. `log` carries the tail of the wrapper's
    /// own log so the reason is on screen instead of in journalctl.
    Failed  { appid: String, log: Vec<String> },
}

static LAUNCH_STATUS: Mutex<Option<LaunchStatus>> = Mutex::new(None);

fn set_status(s: LaunchStatus) {
    if let Ok(mut g) = LAUNCH_STATUS.lock() { *g = Some(s); }
}

/// When the current launch was requested. The wrapper log is append-only and
/// survives across sessions, so without this the tail of a PREVIOUS launch
/// gets read back as this one's progress , a phase line that is plausible,
/// specific, and about something that finished hours ago.
static LAUNCH_STARTED_AT: Mutex<Option<SystemTime>> = Mutex::new(None);

pub fn note_launch_pending(appid: &str) {
    if let Ok(mut g) = LAUNCH_STARTED_AT.lock() { *g = Some(SystemTime::now()); }
    note_launch_progress(appid, 0, LAUNCH_BUDGET_S);
}

/// Has the wrapper written anything for THIS launch yet?
///
/// mtime, not content: the wrapper appends, so the file's own timestamp is
/// the only thing that says whether any of those lines belong to this run.
fn wrapper_log_is_current(appid: &str) -> bool {
    let Some(started) = LAUNCH_STARTED_AT.lock().ok().and_then(|g| *g) else {
        return false;
    };
    let Ok(meta) = std::fs::metadata(wrapper_log_path(appid)) else { return false };
    let Ok(modified) = meta.modified() else { return false };
    // One second of slack: the launch instant and the wrapper's first write
    // can land in the same second with the filesystem rounding the wrong way.
    modified + std::time::Duration::from_secs(1) >= started
}

pub fn note_launch_progress(appid: &str, elapsed_s: u64, budget_s: u64) {
    set_status(LaunchStatus::Pending {
        appid: appid.to_string(),
        elapsed_s,
        phase: current_phase(appid, elapsed_s),
        eta_s: budget_s.saturating_sub(elapsed_s),
    });
}

/// How long a launch may take before we call it failed.
pub const LAUNCH_BUDGET_S: u64 = 180;

/// What the launch is doing right now, in the user's words.
///
/// Derived from the last line the Proton wrapper wrote, because that is the
/// only component that actually knows. The alternative , mapping elapsed
/// seconds onto invented stage names , would be a progress bar that lies
/// confidently, which is worse than the spinner it replaced.
///
/// Falls back to a time-shaped description only when the wrapper has written
/// nothing at all, and says so rather than inventing a stage.
fn current_phase(appid: &str, elapsed_s: u64) -> String {
    let tail = if wrapper_log_is_current(appid) {
        wrapper_log_tail(appid, 6)
    } else {
        Vec::new()
    };
    for line in tail.iter().rev() {
        let l = line.to_ascii_lowercase();
        // Ordered most-specific first; these are the wrapper's own markers.
        if l.contains("gplasync overlay") || l.contains("dlls staged") {
            return "staging DXVK/VKD3D libraries".into();
        }
        if l.contains("delegating to") {
            return "handing off to Proton , wine prefix starting".into();
        }
        if l.contains("upgrading prefix") || l.contains("prefix from") {
            return "upgrading the wine prefix (first run after a Proton change)".into();
        }
        if l.contains("nis shaders staged") {
            return "staging NIS shaders".into();
        }
        if l.contains("t2 ddr pool") {
            return "wiring up the GreenBoost memory pool".into();
        }
        if l.contains("per-game json profile") || l.contains("gpu:") || l.contains("cpu:") {
            return "reading the per-game profile".into();
        }
    }
    // Nothing from our wrapper yet. Before blaming it, check whether Steam is
    // still busy with its own pre-launch step , it blocks on those, and until
    // one finishes the game is never handed to GreenBoost at all.
    if let Some(started) = LAUNCH_STARTED_AT.lock().ok().and_then(|g| *g) {
        if let Some(helper) = steam_helper_activity(started) {
            return format!(
                "Steam is still running its own pre-launch step ({helper}) , \
                 the game has not been handed to GreenBoost yet");
        }
    }
    if elapsed_s < 10 {
        "waiting for Steam to accept the request".into()
    } else {
        "waiting for Proton , the wrapper has not logged anything yet".into()
    }
}

pub fn note_launch_started(appid: &str) {
    note_launch(appid);
    set_status(LaunchStatus::Started { appid: appid.to_string() });
}

pub fn note_launch_failed(appid: &str) {
    // Only this launch's lines. An older session's tail shown as the reason
    // sends the reader after a problem that was already over.
    let mut log = if wrapper_log_is_current(appid) {
        wrapper_log_tail(appid, 20)
    } else {
        Vec::new()
    };
    if log.is_empty() {
        if let Some(started) = LAUNCH_STARTED_AT.lock().ok().and_then(|g| *g) {
            if let Some(helper) = steam_helper_activity(started) {
                log.push(format!(
                    "GreenBoost was never asked to launch anything. Steam ran \
                     its own pre-launch helper ({helper}) through the compat \
                     tool and blocked on it, so the game was never started. \
                     The compatibility tool setting is fine , this is Steam's \
                     own step, and it runs on plain upstream Proton."));
                log.push(
                    "Clear it with:  pkill -f iscriptevaluator; pkill -f d3ddriverquery"
                        .to_string());
            }
        }
    }
    set_status(LaunchStatus::Failed { appid: appid.to_string(), log });
}

pub fn launch_status() -> LaunchStatus {
    LAUNCH_STATUS.lock().ok()
        .and_then(|g| g.clone())
        .unwrap_or(LaunchStatus::Idle)
}

/// Last `n` meaningful lines of the Proton wrapper's own log for this appid.
///
/// `greenboost_proton/proton` (the stub) writes here even when the wrapper
/// body could not be parsed or loaded at all , which is exactly the class of
/// failure that leaves no other trace the Suite can reach, since the body's
/// stderr tee never gets installed.
/// Steam's own pre-launch helpers, and when one last ran.
///
/// Steam runs `iscriptevaluator.exe`, `d3ddriverquery64.exe` and friends
/// through the compat tool BEFORE it launches the game, and it blocks on them.
/// The wrapper stands aside for those (see `_steam_internal_helper` in
/// gb_proton_main.py) and logs one line per delegation here, so this file is
/// the Suite's only view of a launch that has not reached GreenBoost yet.
///
/// Without it the Suite spends the whole budget on "waiting for Proton , the
/// wrapper has not logged anything yet" and then fails with an empty log,
/// which reads as "GreenBoost did nothing" when the truth is "Steam never got
/// far enough to ask". Confirmed live 2026-08-21: a `d3ddriverquery64.exe`
/// that would not finish held a launch at "Launching" indefinitely, with
/// nothing anywhere on screen naming it.
fn steam_helper_activity(since: SystemTime) -> Option<String> {
    let path = wrapper_log_dir().join("greenboost-proton-helpers.log");
    let meta = std::fs::metadata(&path).ok()?;
    let modified = meta.modified().ok()?;
    if modified + std::time::Duration::from_secs(1) < since {
        return None;                       // stale , from an earlier launch
    }
    let text = std::fs::read_to_string(&path).ok()?;
    // Each line is "<helper>.exe is Steam's own helper, not the game , ...".
    let last = text.lines().rev().find(|l| !l.trim().is_empty())?;
    let name = last.split_whitespace()
        .find(|w| w.to_ascii_lowercase().ends_with(".exe"))
        .unwrap_or("a pre-launch step");
    Some(name.to_string())
}

fn wrapper_log_dir() -> std::path::PathBuf {
    let base = std::env::var("XDG_DATA_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default())
                .join(".local/share")
        });
    base.join("greenboost/proton-logs")
}

fn wrapper_log_path(appid: &str) -> std::path::PathBuf {
    wrapper_log_dir().join(format!("greenboost-proton-{appid}.log"))
}

fn wrapper_log_tail(appid: &str, n: usize) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(wrapper_log_path(appid)) else {
        return Vec::new();
    };
    text.lines()
        .map(str::trim_end)
        .filter(|l| !l.is_empty())
        .rev().take(n).collect::<Vec<_>>()
        .into_iter().rev()
        .map(str::to_string)
        .collect()
}

pub fn launched_appid() -> Option<String> {
    LAUNCHED_APPID.lock().ok().and_then(|g| g.clone())
}

/// What a stop actually did. `method` matters: "wrapper" means GreenBoost's
/// own subreaper tore the tree down, "reaper" means Steam's did, "tree" means
/// we walked it ourselves (least reliable), "none" means nothing was running.
#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct StopReport {
    #[serde(default)] pub ok: bool,
    #[serde(default)] pub method: String,
    #[serde(default)] pub appid: Option<String>,
    #[serde(default)] pub root: Option<u32>,
    #[serde(default)] pub terminated: Vec<u32>,
    #[serde(default)] pub killed: Vec<u32>,
    #[serde(default)] pub orphans: Vec<u32>,
    #[serde(default)] pub detail: Option<String>,
}

impl StopReport {
    /// One line for a human, in the shape the project asks for: what
    /// happened, what it costs, what is not broken.
    pub fn summary(&self) -> String {
        match self.method.as_str() {
            "none" => "No running game to stop.".to_string(),
            _ if !self.orphans.is_empty() => format!(
                "Stopped the game, but {} process(es) ignored both signals and \
                 are still running. Nothing else is affected , Steam and the \
                 Suite are untouched. Run `gb_gaming.game_lifecycle stop` \
                 again, or reboot if they persist.",
                self.orphans.len()),
            m => format!(
                "Game stopped ({} process(es) asked to exit, {} force-killed) \
                 via the {} path.",
                self.terminated.len(), self.killed.len(),
                match m { "wrapper" => "GreenBoost Proton",
                          "reaper"  => "Steam reaper",
                          _         => "process-tree" }),
        }
    }
}

/// Restore power state left applied by a session that died hard.
///
/// Separate from `prune_stale_sessions` because they fix different damage: a
/// stale session record is bookkeeping, but a stale power baseline means the
/// CPU governor is still pinned, a power limit is still applied and
/// `gaming_mode` is still 1 , which parks inference memory at the eviction
/// queue's tail until something notices. Returns (restored, needing_root).
pub fn restore_stale_power() -> (usize, usize) {
    let Ok(stdout) = python_json_mod("power_baseline", &["restore"]) else {
        return (0, 0);
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&stdout) else {
        return (0, 0);
    };
    let rows = v.as_array().cloned().unwrap_or_default();
    let ok = rows.iter()
        .filter(|r| r.get("restored").and_then(|b| b.as_bool()).unwrap_or(false))
        .count();
    (ok, rows.len() - ok)
}

fn python_json(args: &[&str]) -> Result<String, String> {
    python_json_mod("game_lifecycle", args)
}

fn python_json_mod(module: &str, args: &[&str]) -> Result<String, String> {
    // -c rather than -m so the sys.path bootstrap (installed path first, dev
    // tree second) applies exactly as it does for every other bridge.
    // JSON is a valid Python literal for a list of strings, so the argv
    // crosses the boundary without any quoting of our own to get wrong.
    let argv = serde_json::to_string(args)
        .map_err(|e| format!("cannot encode argv: {e}"))?;
    let script = format!(
        "{bootstrap}import sys\n\
         from gb_gaming import {module} as _m\n\
         sys.exit(_m._main({argv}))\n",
        bootstrap = crate::py_bootstrap::py_bootstrap());
    let out = Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("python3 invoke failed: {e}"))?;
    if !out.status.success() {
        return Err(format!("game_lifecycle exited {}: {}",
                           out.status, String::from_utf8_lossy(&out.stderr)));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Stop the running game. `appid` defaults to whatever this Suite launched.
pub fn stop_game_impl(appid: Option<String>, grace: f64) -> Result<StopReport, String> {
    let id = appid.or_else(launched_appid);
    let grace_s = format!("{grace}");
    let mut args: Vec<&str> = vec!["stop", "--grace", &grace_s];
    if let Some(ref a) = id {
        args.push("--appid");
        args.push(a);
    }
    let stdout = python_json(&args)?;
    serde_json::from_str(&stdout)
        .map_err(|e| format!("bad JSON from game_lifecycle: {e} , got {stdout:?}"))
}

/// True when a session record exists whose owning wrapper is still alive.
/// Used to decide whether "Stop game" is worth offering in the tray menu.
pub fn has_live_session() -> bool {
    let Ok(stdout) = python_json(&["sessions"]) else { return false };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&stdout) else { return false };
    v.as_array()
        .map(|a| a.iter().any(|r| r.get("_alive").and_then(|b| b.as_bool()).unwrap_or(false)))
        .unwrap_or(false)
}

/// Drop session records whose owning process is gone, and report how many.
///
/// A stale record means a session died hard , which is also the state in
/// which `gaming_mode` can be left at 1, parking inference memory at the LRU
/// tail indefinitely. Called at startup so a crashed session is cleaned up
/// by the next launch rather than never.
pub fn prune_stale_sessions() -> usize {
    let Ok(stdout) = python_json(&["prune"]) else { return 0 };
    serde_json::from_str::<serde_json::Value>(&stdout)
        .ok()
        .and_then(|v| v.as_array().map(|a| a.len()))
        .unwrap_or(0)
}
