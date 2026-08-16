// Release-update surface, shared by Status (banner) and About (full panel).
//
// Reads the shared store, so both places show the same result from the one
// network check that ran on app start , navigating between them doesn't
// re-query GitLab.
//
// Deliberately quiet: when everything is current, `compact` renders nothing
// at all rather than a green "you're up to date" bar. An update checker that
// is visible when there is no update is just noise on every launch.

import { useEffect } from "react";
import { Icon } from "../icons";
import {
  useUpdateReport, useUpdateChecking, checkForUpdates, hasAnyUpdate,
  type ComponentUpdate,
} from "../store/updates";

function relDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function ComponentRow({ c }: { c: ComponentUpdate }) {
  const state: { text: string; color: string } =
    c.error            ? { text: "Couldn't check", color: "#e8a000" }
  : c.not_installed    ? { text: "Not installed",  color: "#8a9ab0" }
  : c.update_available ? { text: `Update available: ${c.latest}`, color: "#76b900" }
  : c.latest == null   ? { text: "No releases published", color: "#8a9ab0" }
  : c.installed == null? { text: `Latest is ${c.latest} , installed version unknown`, color: "#8a9ab0" }
  :                      { text: "Up to date", color: "#8a9ab0" };

  return (
    <div className="info-row">
      <div className="info-label">
        {c.name}
        {c.installed && (
          <span style={{ color: "#6b7280", fontWeight: 400 }}> , installed {c.installed}</span>
        )}
      </div>
      <div className="info-value" style={{ color: state.color, textAlign: "right" }}>
        {state.text}
        {c.update_available && (
          <>
            {" "}
            <a href={c.release_url} target="_blank" rel="noopener noreferrer"
               style={{ color: "#a5b4fc", fontSize: 12 }}>
              release notes
            </a>
            {c.released_at && (
              <span style={{ color: "#6b7280", fontSize: 11 }}> ({relDate(c.released_at)})</span>
            )}
          </>
        )}
        {c.error && (
          <div style={{ fontSize: 11, color: "#6b7280", fontWeight: 400 }}>{c.error}</div>
        )}
      </div>
    </div>
  );
}

/**
 * `compact` = the Status banner: shows only when something is actually out
 * of date. The full form (About) always renders, including the "Check now"
 * button and the up-to-date case, because that's a page you visit on purpose
 * to ask the question.
 */
export function UpdateBanner({ compact = false }: { compact?: boolean }) {
  const report = useUpdateReport();
  const checking = useUpdateChecking();

  // Fire the automatic check once. The store dedupes concurrent callers and
  // the backend serves a 6-hour disk cache, so mounting this in two places
  // costs at most one request.
  useEffect(() => { checkForUpdates(false); }, []);

  if (compact && !hasAnyUpdate(report)) return null;

  return (
    <>
      <p className="section-title" style={compact ? { marginTop: 0 } : { marginTop: 24 }}>
        Updates
      </p>
      <div className="section-card" style={hasAnyUpdate(report) ? {
        border: "1px solid rgba(118,185,0,0.35)",
        background: "rgba(118,185,0,0.05)",
      } : undefined}>
        {report ? (
          <>
            <ComponentRow c={report.suite} />
            <ComponentRow c={report.core} />

            {report.advice && (
              <p style={{ fontSize: 12.5, color: "#e6e6e6", lineHeight: 1.6,
                          margin: "10px 0 0", display: "flex", gap: 8 }}>
                <span style={{ color: "#76b900", flexShrink: 0 }}><Icon.AlertCircle /></span>
                <span>{report.advice}</span>
              </p>
            )}

            {!compact && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
                <button className="btn-optimize" disabled={checking}
                        onClick={() => checkForUpdates(true)}>
                  {checking ? "Checking…" : "Check now"}
                </button>
                <span style={{ fontSize: 11, color: "#6b7280" }}>
                  {report.checked_at
                    ? `Last checked ${new Date(report.checked_at * 1000).toLocaleString()}`
                      + (report.from_cache ? " (cached)" : "")
                    : "Not checked yet"}
                </span>
              </div>
            )}
          </>
        ) : (
          <p style={{ fontSize: 13, color: "#9a9a9a", margin: 0 }}>
            {checking ? "Checking for updates…" : "Update status unavailable."}
          </p>
        )}
      </div>
    </>
  );
}
