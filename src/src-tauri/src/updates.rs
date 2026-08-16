//! Release-update checks against GitLab, for this Suite and for GreenBoost
//! core.
//!
//! Two components, deliberately checked and reported separately:
//!
//!   * **greenboost_gaming_suite** , this app. Its installed version is
//!     `CARGO_PKG_VERSION`, i.e. whatever the binary was built from, which
//!     is the only version number that can't drift from reality.
//!   * **greenboost** , the parent kernel module + CUDA shim. Independent
//!     repo, independent release cadence, and installed by a different
//!     installer, so its version has to be probed from the system rather
//!     than assumed to match.
//!
//! Ordering matters in the advice we give, not in the requests we make: a
//! newer Suite can expect a newer core (they share `greenboost_ioctl.h`),
//! so when both are behind, the Suite goes first and the core update is
//! presented as the follow-up. `UpdateReport::advice()` encodes that.
//!
//! Everything here fails soft. No network, GitLab down, rate-limited, a
//! project with no releases yet: all of it lands in `error` on the relevant
//! component and leaves `update_available` false. An update check must
//! never be the reason the Status view can't render.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// URL-encoded GitLab project paths (`/` -> `%2F`, as the API requires).
const SUITE_PROJECT: &str = "IsolatedOctopi%2Fgreenboost_gaming_suite";
const CORE_PROJECT: &str = "IsolatedOctopi%2Fgreenboost";

const SUITE_WEB: &str = "https://gitlab.com/IsolatedOctopi/greenboost_gaming_suite";
const CORE_WEB: &str = "https://gitlab.com/IsolatedOctopi/greenboost";

/// Don't hit GitLab on every app start. The cache on disk is served if it's
/// younger than this; the UI's "Check now" button always bypasses it.
const CACHE_TTL: Duration = Duration::from_secs(6 * 60 * 60);

const HTTP_TIMEOUT: Duration = Duration::from_secs(8);

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ComponentUpdate {
    /// Stable key for the UI: "suite" | "core".
    pub key: String,
    /// Human name, e.g. "GreenBoost Gaming Suite".
    pub name: String,
    /// What's on this machine. None when we genuinely can't tell , which is
    /// different from "not installed", and the UI must not conflate them.
    pub installed: Option<String>,
    /// Newest release tag on GitLab, normalised (no leading "v").
    pub latest: Option<String>,
    /// True only when both versions are known AND latest > installed.
    /// An unknown installed version never produces a nag.
    pub update_available: bool,
    /// Release page for the newest release, or the project page as fallback.
    pub release_url: String,
    /// Release notes/description, trimmed. Empty when the release has none.
    pub notes: String,
    /// ISO-8601 release date from GitLab, when present.
    pub released_at: Option<String>,
    /// Why this component couldn't be checked. None on success.
    pub error: Option<String>,
    /// True when the component isn't installed at all , the UI should offer
    /// "install" rather than "update", and never claim you're out of date.
    pub not_installed: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct UpdateReport {
    pub suite: ComponentUpdate,
    pub core: ComponentUpdate,
    /// Unix seconds when these results were actually fetched.
    pub checked_at: u64,
    /// True when served from disk cache rather than a fresh request.
    pub from_cache: bool,
    /// One sentence telling the user what, if anything, to do , sequenced so
    /// the Suite is updated before the core. Empty when everything is current.
    pub advice: String,
}

// ── GitLab release payload (only the fields we use) ────────────────────

#[derive(Deserialize)]
struct GlRelease {
    tag_name: Option<String>,
    name: Option<String>,
    description: Option<String>,
    released_at: Option<String>,
    #[serde(default)]
    _links: Option<GlLinks>,
}

#[derive(Deserialize)]
struct GlLinks {
    #[serde(rename = "self")]
    self_url: Option<String>,
}

// ── Version handling ───────────────────────────────────────────────────

/// Strip the decoration release tags pick up , a leading "v", and any
/// trailing pre-release/build metadata after `-` or `+`. "v1.2.3-rc1"
/// becomes "1.2.3".
fn normalise(tag: &str) -> String {
    let t = tag.trim();
    let t = t.strip_prefix('v').or_else(|| t.strip_prefix('V')).unwrap_or(t);
    t.split(['-', '+']).next().unwrap_or(t).trim().to_string()
}

/// Split into numeric components. Non-numeric segments become 0 rather than
/// aborting the parse, so a tag like "2026.08.16" or "1.2.3a" still orders
/// sensibly instead of silently disabling the check.
fn parts(v: &str) -> Vec<u64> {
    normalise(v)
        .split('.')
        .map(|s| {
            let digits: String = s.chars().take_while(|c| c.is_ascii_digit()).collect();
            digits.parse::<u64>().unwrap_or(0)
        })
        .collect()
}

/// True when `latest` is strictly newer than `installed`.
///
/// Compares component-wise, zero-padding the shorter side so "1.2" and
/// "1.2.0" compare equal rather than the longer one always winning.
fn is_newer(latest: &str, installed: &str) -> bool {
    let (a, b) = (parts(latest), parts(installed));
    let n = a.len().max(b.len());
    for i in 0..n {
        let x = a.get(i).copied().unwrap_or(0);
        let y = b.get(i).copied().unwrap_or(0);
        if x != y {
            return x > y;
        }
    }
    false
}

// ── Installed-version probes ───────────────────────────────────────────

/// This binary's version. Built in, so it cannot drift from what's running.
fn installed_suite_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Does this token actually look like a version ("3.2", "v1.4.2")? Required
/// because a CLI that doesn't understand `--version` may still print a
/// cheerful usage message and exit 0, and any of those words would otherwise
/// be accepted as a version number.
fn looks_like_version(tok: &str) -> bool {
    let t = normalise(tok);
    !t.is_empty()
        && t.contains('.')
        && t.starts_with(|c: char| c.is_ascii_digit())
        && t.chars().all(|c| c.is_ascii_digit() || c == '.')
}

/// GreenBoost core's version.
///
/// Returns `(version, installed_at_all)`. The second flag matters: a machine
/// with no GreenBoost core is a normal, supported configuration (most of the
/// Suite works without it), and must not be reported as "out of date".
///
/// Order is deliberate, and differs from `detect_greenboost()` in install.sh
/// on purpose. That function probes the CLI first because it only needs to
/// know whether core is *present*. We need a version, and as of core v3.2
/// `greenboost --version` is not a command , it prints
/// "Unknown command: '--version'" and still **exits 0**, so a naive reader
/// treats its usage text as a version string. sysfs and modinfo report the
/// module's real version ("3.2"), so they go first and the CLI is a strictly
/// validated fallback.
fn installed_core_version() -> (Option<String>, bool) {
    // 1. The loaded kernel module reports its own version via sysfs.
    if let Ok(v) = std::fs::read_to_string("/sys/module/greenboost/version") {
        let v = v.trim();
        if looks_like_version(v) {
            return (Some(normalise(v)), true);
        }
    }
    // 2. modinfo reads the .ko without needing it loaded.
    if let Ok(out) = std::process::Command::new("modinfo")
        .args(["-F", "version", "greenboost"]).output()
    {
        if out.status.success() {
            let v = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if looks_like_version(&v) {
                return (Some(normalise(&v)), true);
            }
        }
    }
    // 3. The CLI, if a future version grows a real --version. Exit status is
    //    not trustworthy here (see above), so the token itself must pass.
    if let Ok(out) = std::process::Command::new("greenboost").arg("--version").output() {
        let text = String::from_utf8_lossy(&out.stdout);
        if let Some(tok) = text.lines().next()
            .and_then(|l| l.split_whitespace().find(|t| looks_like_version(t)))
        {
            return (Some(normalise(tok)), true);
        }
        // Present on PATH but won't tell us a version. Installed, unknown.
        if out.status.success() {
            return (None, true);
        }
    }
    // 4. Shim present but unversioned still counts as installed.
    for p in ["/usr/local/lib/libgreenboost_cuda.so", "/usr/lib/libgreenboost_cuda.so"] {
        if std::path::Path::new(p).exists() {
            return (None, true);
        }
    }
    (None, false)
}

// ── GitLab query ───────────────────────────────────────────────────────

fn fetch_latest_release(project: &str, web_url: &str) -> Result<Option<GlRelease>, String> {
    let url = format!(
        "https://gitlab.com/api/v4/projects/{project}/releases?per_page=1"
    );
    let client = reqwest::blocking::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .user_agent(concat!("greenboost-gaming-suite/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|e| format!("HTTP client: {e}"))?;

    let resp = client.get(&url).send().map_err(|e| {
        // The overwhelmingly common case is "no internet", and saying that
        // is more use than echoing a reqwest error at someone.
        if e.is_timeout() { "timed out reaching gitlab.com".to_string() }
        else if e.is_connect() { "couldn't reach gitlab.com (offline?)".to_string() }
        else { format!("request failed: {e}") }
    })?;

    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Err(format!("project not found or private ({web_url})"));
    }
    if resp.status() == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return Err("rate-limited by GitLab, try again later".to_string());
    }
    if !resp.status().is_success() {
        return Err(format!("GitLab returned HTTP {}", resp.status().as_u16()));
    }

    let list: Vec<GlRelease> = resp.json().map_err(|e| format!("bad JSON from GitLab: {e}"))?;
    Ok(list.into_iter().next())
}

fn check_component(
    key: &str, name: &str, project: &str, web_url: &str,
    installed: Option<String>, present: bool,
) -> ComponentUpdate {
    let mut c = ComponentUpdate {
        key: key.to_string(),
        name: name.to_string(),
        installed: installed.clone(),
        release_url: web_url.to_string(),
        not_installed: !present,
        ..Default::default()
    };

    match fetch_latest_release(project, web_url) {
        Err(e) => { c.error = Some(e); }
        Ok(None) => {
            // Reachable and valid, just nothing published yet. Not an error
            // , don't show the user a failure for a project that simply
            // hasn't cut a release.
            c.latest = None;
        }
        Ok(Some(r)) => {
            let tag = r.tag_name.clone()
                .or_else(|| r.name.clone())
                .unwrap_or_default();
            if !tag.is_empty() {
                c.latest = Some(normalise(&tag));
            }
            c.notes = r.description.unwrap_or_default().trim().chars().take(600).collect();
            c.released_at = r.released_at;
            if let Some(l) = r._links.and_then(|l| l.self_url) {
                if !l.is_empty() { c.release_url = l; }
            }
        }
    }

    // Only claim an update when we know both sides and the component is
    // actually installed. Unknown installed version => stay quiet.
    c.update_available = match (present, &c.installed, &c.latest) {
        (true, Some(inst), Some(latest)) => is_newer(latest, inst),
        _ => false,
    };
    c
}

// ── Cache ──────────────────────────────────────────────────────────────

fn cache_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".config")
        .join("greenboost-gaming")
        .join("update_check.json")
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn read_cache() -> Option<UpdateReport> {
    let raw = std::fs::read_to_string(cache_path()).ok()?;
    serde_json::from_str::<UpdateReport>(&raw).ok()
}

fn write_cache(r: &UpdateReport) {
    let p = cache_path();
    if let Some(dir) = p.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(s) = serde_json::to_string_pretty(r) {
        let _ = std::fs::write(p, s);
    }
}

// ── Advice ─────────────────────────────────────────────────────────────

fn build_advice(suite: &ComponentUpdate, core: &ComponentUpdate) -> String {
    match (suite.update_available, core.update_available) {
        (true, true) =>
            "Update the Suite first (sudo ./install.sh in your checkout, after a \
             git pull), then update GreenBoost core , a newer Suite can expect a \
             newer core, since they share the same IOCTL interface.".to_string(),
        (true, false) =>
            "A new GreenBoost Gaming Suite release is available. Pull the repo and \
             re-run sudo ./install.sh , that also refreshes the Steam compatibility \
             tool, which a manual build does not.".to_string(),
        (false, true) =>
            "The Suite is current, but GreenBoost core has a newer release. Update \
             it from its own repo, then reload the kernel module (sudo greenboost \
             load) so the new module is actually the one running.".to_string(),
        (false, false) => String::new(),
    }
}

// ── Entry point ────────────────────────────────────────────────────────

/// Check both projects for newer releases.
///
/// `force = false` serves a cached result while it's younger than
/// `CACHE_TTL`, which is what the automatic check on app start uses.
/// `force = true` always goes to the network , the "Check now" button.
pub fn check_updates_impl(force: bool) -> UpdateReport {
    if !force {
        if let Some(cached) = read_cache() {
            if now_secs().saturating_sub(cached.checked_at) < CACHE_TTL.as_secs() {
                let mut c = cached;
                c.from_cache = true;
                // The installed versions are cheap to probe and can change
                // under a cached result (the user just ran the installer),
                // so re-derive them rather than serving a stale claim.
                c.suite.installed = Some(installed_suite_version());
                let (cv, present) = installed_core_version();
                c.core.installed = cv;
                c.core.not_installed = !present;
                c.suite.update_available = match (&c.suite.installed, &c.suite.latest) {
                    (Some(i), Some(l)) => is_newer(l, i),
                    _ => false,
                };
                c.core.update_available = match (present, &c.core.installed, &c.core.latest) {
                    (true, Some(i), Some(l)) => is_newer(l, i),
                    _ => false,
                };
                c.advice = build_advice(&c.suite, &c.core);
                return c;
            }
        }
    }

    let suite = check_component(
        "suite", "GreenBoost Gaming Suite", SUITE_PROJECT, SUITE_WEB,
        Some(installed_suite_version()), true,
    );
    let (core_ver, core_present) = installed_core_version();
    let core = check_component(
        "core", "GreenBoost core", CORE_PROJECT, CORE_WEB,
        core_ver, core_present,
    );

    let advice = build_advice(&suite, &core);
    let report = UpdateReport {
        suite, core, checked_at: now_secs(), from_cache: false, advice,
    };
    write_cache(&report);
    report
}

/// The Suite's own version, for the About panel , so the number shown there
/// is the binary's, not a string someone has to remember to bump.
pub fn suite_version() -> String {
    installed_suite_version()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Release tags arrive wearing decoration. Strip it, keep the number.
    #[test]
    fn normalise_strips_release_tag_decoration() {
        const CASES: &[(&str, &str, &str)] = &[
            ("1.2.3",        "1.2.3", "already clean"),
            ("v1.2.3",       "1.2.3", "conventional v prefix"),
            ("V1.2.3",       "1.2.3", "uppercase prefix"),
            (" v0.1.0 ",     "0.1.0", "padded by a free-text field"),
            ("1.2.3-rc1",    "1.2.3", "pre-release suffix"),
            ("1.2.3+build7", "1.2.3", "build metadata"),
        ];
        for (raw, want, why) in CASES {
            assert_eq!(&normalise(raw), want, "normalise({raw:?}) , {why}");
        }
    }

    /// Ordering has to be numeric and component-wise. String comparison gets
    /// 0.1.10 vs 0.1.9 wrong, and that is a release we would fail to offer.
    #[test]
    fn is_newer_orders_versions_numerically() {
        // (latest, installed, expected, why)
        const CASES: &[(&str, &str, bool, &str)] = &[
            ("0.2.0",      "0.1.0",      true,  "minor bump"),
            ("1.0.0",      "0.9.9",      true,  "major bump outranks lower components"),
            ("0.1.10",     "0.1.9",      true,  "numeric, not lexicographic"),
            ("2026.08.16", "2026.08.15", true,  "date-style tags still order"),
            ("3.2",        "3.2",        false, "identical is not newer"),
            ("0.1.0",      "0.2.0",      false, "older release must never nag"),
            ("1.2",        "1.2.0",      false, "missing components are zeros"),
            ("1.2.0",      "1.2",        false, "and symmetrically so"),
            ("1.2.1",      "1.2",        true,  "padding must not mask a real bump"),
            ("abc",        "1.0.0",      false, "unparseable latest stays quiet"),
            ("1.2.3a",     "1.2.2",      true,  "trailing junk on a real number"),
        ];
        for (latest, installed, want, why) in CASES {
            assert_eq!(
                is_newer(latest, installed), *want,
                "is_newer({latest:?}, {installed:?}) , {why}",
            );
        }
    }

    /// Guards the probe against a CLI that answers `--version` with prose.
    /// As of core v3.2 `greenboost --version` prints "Unknown command" and
    /// still exits 0, so exit status proves nothing and every token in that
    /// sentence gets offered to this function. Accept one and the app
    /// reports an error message as the installed version.
    #[test]
    fn looks_like_version_accepts_only_version_numbers() {
        // (token, expected, why)
        const CASES: &[(&str, bool, &str)] = &[
            ("3.2",         true,  "the real core version"),
            ("v1.4.2",      true,  "prefixed but valid"),
            ("2026.08.16",  true,  "date-style version"),
            ("Unknown",     false, "first word of the usage line"),
            ("command:",    false, "usage line"),
            ("'--version'", false, "the flag echoed back at us"),
            ("run:",        false, "usage line"),
            ("greenboost",  false, "the binary name"),
            ("help",        false, "the suggested command"),
            ("unknown",     false, "a literal placeholder some tools print"),
            ("-",           false, "punctuation"),
            ("v",           false, "prefix with no number"),
            ("..",          false, "separators with no digits"),
            ("1",           false, "no separator , could be anything"),
            ("",            false, "empty"),
        ];
        for (token, want, why) in CASES {
            assert_eq!(
                looks_like_version(token), *want,
                "looks_like_version({token:?}) , {why}",
            );
        }
    }
}
