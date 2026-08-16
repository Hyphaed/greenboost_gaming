// "Here's what I just changed" , shown after Smart Defaults and after a
// per-game Optimize.
//
// Both of those apply a batch of writes from one click. Reporting that with
// a one-line "Applied." asks the user to trust a button that silently
// rewrote a dozen settings, and gives them nothing to undo from. This lists
// every field that actually moved, old value → new value, plus whatever the
// backend explained about *why* (the auto-tune notes carry the hardware
// reasoning: detected GPU, SM tier, core counts, NUMA, L3).

import type { Change } from "../changeSummary";

export function ChangeSummaryModal({
  title, subtitle, changes, notes, unchangedMessage, onClose,
}: {
  title: string;
  /** One line naming what this was tuned for, e.g. the detected GPU. */
  subtitle?: string;
  changes: Change[];
  /** Backend reasoning , AutoTuneResult.notes. Rendered verbatim. */
  notes?: string[];
  /** Shown when nothing changed, instead of an empty table. */
  unchangedMessage?: string;
  onClose: () => void;
}) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 2000,
        background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={e => e.stopPropagation()}
        style={{
          background: "#1b1b1b", border: "1px solid #333", borderRadius: 8,
          boxShadow: "0 12px 40px rgba(0,0,0,0.6)",
          width: "min(640px, 100%)", maxHeight: "min(80vh, 720px)",
          display: "flex", flexDirection: "column", overflow: "hidden",
        }}
      >
        <div style={{ padding: "16px 20px 12px", borderBottom: "1px solid #2a2a2a" }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: "#e6e6e6" }}>{title}</div>
          {subtitle && (
            <div style={{ fontSize: 12, color: "#76b900", marginTop: 4 }}>{subtitle}</div>
          )}
        </div>

        <div style={{ padding: "14px 20px", overflowY: "auto", flex: 1 }}>
          {changes.length === 0 ? (
            <p style={{ fontSize: 13, color: "#9a9a9a", margin: 0, lineHeight: 1.6 }}>
              {unchangedMessage ?? "Everything was already at its recommended value , nothing changed."}
            </p>
          ) : (
            <>
              <div style={{ fontSize: 11, color: "#8a9ab0", textTransform: "uppercase",
                            letterSpacing: "0.04em", marginBottom: 8 }}>
                {changes.length} setting{changes.length === 1 ? "" : "s"} changed
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <tbody>
                  {changes.map(c => (
                    <tr key={c.label} style={{ borderBottom: "1px solid #262626" }}>
                      <td style={{ padding: "7px 8px 7px 0", color: "#e6e6e6" }}>{c.label}</td>
                      <td style={{ padding: "7px 8px", color: "#6b7280", textAlign: "right",
                                   whiteSpace: "nowrap" }}>{c.from}</td>
                      <td style={{ padding: "7px 6px", color: "#6b7280" }}>→</td>
                      <td style={{ padding: "7px 0", color: "#76b900", fontWeight: 600,
                                   whiteSpace: "nowrap" }}>{c.to}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {notes && notes.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 11, color: "#8a9ab0", textTransform: "uppercase",
                            letterSpacing: "0.04em", marginBottom: 6 }}>
                Why these values
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "#b8c0cc",
                           lineHeight: 1.65 }}>
                {notes.map((n, i) => <li key={i} style={{ marginBottom: 4 }}>{n}</li>)}
              </ul>
            </div>
          )}
        </div>

        <div style={{ padding: "12px 20px", borderTop: "1px solid #2a2a2a",
                      display: "flex", justifyContent: "flex-end" }}>
          <button className="btn-optimize" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
