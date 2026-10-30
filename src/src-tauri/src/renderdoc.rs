// Copyright 2026 Ferran Duarri , GPL v2
// RenderDoc capture integration: a real, human-driven GPU frame debugger,
// staged by greenboost_proton/install.sh into
// $HOME/.local/share/greenboost/renderdoc/current/ and registered as an
// opt-in Vulkan implicit layer (renderdoccmd vulkanlayer --register --user).
// The layer is inert unless ENABLE_VULKAN_RENDERDOC_CAPTURE=1 is set in the
// launched process's environment , see gb_proton_main.py's
// _renderdoc_capture_env(), which reads and consumes the marker file this
// module writes, so a normal Launch never captures anything.
//
// This module only launches the game with capture armed and opens whatever
// .rdc file RenderDoc wrote. It does not parse, script, or interpret
// captures , qrenderdoc is the GUI a human drives themselves.

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn renderdoc_dir() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    let dir = PathBuf::from(home)
        .join(".local/share/greenboost/renderdoc/current");
    if dir.join("bin/renderdoccmd").is_file() { Some(dir) } else { None }
}

pub fn renderdoc_available_impl() -> bool {
    renderdoc_dir().is_some()
}

/// Marker directory consumed once per launch by gb_proton_main.py's
/// _renderdoc_capture_env(). One empty file per pending appid; the wrapper
/// deletes it on read, so a later plain Launch never inherits capture mode.
fn marker_dir() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".config/greenboost-gaming/renderdoc-capture-pending"))
}

/// Arms capture for the next launch of `appid` and returns the Unix
/// timestamp (seconds) capture was armed at , the caller uses this to
/// filter `find_capture_since_impl` down to files RenderDoc wrote for THIS
/// launch, not a stale capture from an earlier session. No process is
/// spawned here; the actual launch is the existing launch_game_ext path,
/// called separately so this stays a thin arm-and-return step.
pub fn arm_capture_impl(appid: &str) -> Result<u64, String> {
    if !renderdoc_available_impl() {
        return Err("RenderDoc isn't installed , re-run install.sh".to_string());
    }
    let dir = marker_dir().ok_or("HOME not set")?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir: {e}"))?;
    std::fs::write(dir.join(appid), b"").map_err(|e| format!("write marker: {e}"))?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|e| format!("clock: {e}"))?.as_secs();
    Ok(now)
}

/// RenderDoc's own default capture directory on Linux (confirmed via
/// `strings` on librenderdoc.so: the compiled-in template is
/// "%s/RenderDoc/%s_%04d.%02d.%02d_%02d.%02d.rdc", base = $HOME). No env
/// var overrides this in the implicit-layer path (only the `renderdoccmd
/// capture <exe>` CLI form takes a --capture-file template, which doesn't
/// apply here since the game launches through Steam, not as our direct
/// child), so this scans RenderDoc's fixed default location instead of
/// trying to redirect it.
fn default_capture_dir() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join("RenderDoc"))
}

/// Newest .rdc file under RenderDoc's default capture directory with an
/// mtime at or after `since_unix`. Matching by time window, not by game
/// name , RenderDoc's filename template embeds the captured executable's
/// own basename, which varies unpredictably across engines (FF7 Rebirth's
/// real process is `ff7rebirth_.exe`, not the Steam-visible `ff7rebirth`),
/// so a name filter would silently miss real captures. `since_unix` (from
/// `arm_capture_impl`'s return value) is what keeps this from returning a
/// stale capture out of an unrelated earlier session.
pub fn find_capture_since_impl(since_unix: u64) -> Result<Option<String>, String> {
    let dir = default_capture_dir().ok_or("HOME not set")?;
    find_capture_since_in(&dir, since_unix)
}

/// Directory-injected core , kept separate from find_capture_since_impl so
/// tests can point it at a tmp dir directly instead of mutating the
/// process-global HOME env var (which races under cargo's default
/// parallel test threads: two tests setting HOME concurrently is exactly
/// the flakiness `std::env::set_var` in tests is known for).
fn find_capture_since_in(dir: &std::path::Path, since_unix: u64) -> Result<Option<String>, String> {
    if !dir.is_dir() {
        return Ok(None);
    }
    let since = UNIX_EPOCH + std::time::Duration::from_secs(since_unix.saturating_sub(2));
    let mut newest: Option<(SystemTime, PathBuf)> = None;
    let read = std::fs::read_dir(dir).map_err(|e| format!("read {}: {e}", dir.display()))?;
    for entry in read.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("rdc") {
            continue;
        }
        let Ok(meta) = entry.metadata() else { continue };
        let Ok(mtime) = meta.modified() else { continue };
        if mtime < since {
            continue;
        }
        if newest.as_ref().map(|(t, _)| mtime > *t).unwrap_or(true) {
            newest = Some((mtime, path));
        }
    }
    Ok(newest.map(|(_, p)| p.to_string_lossy().to_string()))
}

/// Opens a .rdc capture in qrenderdoc , a human drives the actual analysis
/// from there. Spawned detached: the Suite doesn't wait on or manage the
/// debugger's lifetime.
pub fn open_capture_impl(path: &str) -> Result<(), String> {
    let dir = renderdoc_dir().ok_or("RenderDoc isn't installed")?;
    std::process::Command::new(dir.join("bin/qrenderdoc"))
        .arg(path)
        .spawn()
        .map_err(|e| format!("failed to launch qrenderdoc: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn find_capture_since_ignores_files_before_the_window() {
        let tmp = std::env::temp_dir().join(format!("gb-test-rdoc-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let old = tmp.join("old_2026.08.20_10.00.rdc");
        fs::write(&old, b"x").unwrap();
        let old_time = SystemTime::now() - std::time::Duration::from_secs(3600);
        filetime_set(&old, filetime_from(old_time));

        let armed_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        std::thread::sleep(std::time::Duration::from_millis(1100));

        let new = tmp.join("ff7rebirth__2026.08.28_15.41.rdc");
        fs::write(&new, b"x").unwrap();

        let found = find_capture_since_in(&tmp, armed_at).unwrap();
        assert_eq!(found, Some(new.to_string_lossy().to_string()));

        fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn find_capture_since_returns_none_when_dir_missing() {
        let tmp = std::env::temp_dir().join(format!("gb-test-rdoc-none-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp); // deliberately never created
        let armed_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        assert_eq!(find_capture_since_in(&tmp, armed_at).unwrap(), None);
    }

    #[test]
    fn find_capture_since_picks_the_newest_of_several() {
        let tmp = std::env::temp_dir().join(format!("gb-test-rdoc-multi-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let armed_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();

        fs::write(tmp.join("a.rdc"), b"x").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(1100));
        fs::write(tmp.join("b.rdc"), b"x").unwrap();

        let found = find_capture_since_in(&tmp, armed_at).unwrap();
        assert_eq!(found, Some(tmp.join("b.rdc").to_string_lossy().to_string()));

        fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn find_capture_since_ignores_non_rdc_files() {
        let tmp = std::env::temp_dir().join(format!("gb-test-rdoc-nonrdc-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let armed_at = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        fs::write(tmp.join("readme.txt"), b"x").unwrap();
        assert_eq!(find_capture_since_in(&tmp, armed_at).unwrap(), None);
        fs::remove_dir_all(&tmp).ok();
    }

    // Minimal filetime helpers , avoids pulling in the `filetime` crate for
    // two tests. Sets mtime via utimensat through std's unstable-free path:
    // std::fs has no public mtime setter, so this shells out to `touch -d`.
    fn filetime_from(t: SystemTime) -> String {
        let secs = t.duration_since(UNIX_EPOCH).unwrap().as_secs();
        secs.to_string()
    }
    fn filetime_set(path: &std::path::Path, epoch_secs: String) {
        std::process::Command::new("touch")
            .arg("-d").arg(format!("@{epoch_secs}"))
            .arg(path)
            .status()
            .expect("touch");
    }
}
