import { useState, useEffect, useCallback, useRef } from "react";
import { invoke, Channel } from "@tauri-apps/api/core";
import type { InstallStreamProps } from "../types";

function lineColor(line: string): string {
  if (line.startsWith("[UPDATED]"))   return "#f59e0b"; // amber , version changed
  if (line.startsWith("[NEW]"))       return "#76b900"; // green , freshly added
  if (line.startsWith("[updated]"))   return "#86efac"; // light green , game DLL applied
  if (line.startsWith("[error]") || line.startsWith("[py-err]")) return "#e05252";
  if (line.startsWith("[skipped]") || line.startsWith("[unchanged]")) return "#7a8a9a";
  if (line.startsWith("=== Step"))    return "#e6e6e6"; // bright for step headers
  if (line.startsWith("─"))           return "#3a3a3a"; // dim separator
  if (line.startsWith("Done ,") || line.includes("::summary::")) return "#76b900";
  return "#cccccc";
}

export function InstallStreamModal({
  title, command, args, destructive, confirm, onDone
}: InstallStreamProps) {
  const [phase, setPhase] = useState<"confirm"|"running"|"done"|"failed">(
    destructive ? "confirm" : "running"
  );
  const [lines, setLines] = useState<string[]>([]);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  const start = useCallback(() => {
    setPhase("running");
    setLines([]);
    const ch = new Channel<string>();
    ch.onmessage = (line) => {
      setLines(prev => [...prev, line]);
    };
    invoke<number>(command, { ...(args ?? {}), channel: ch })
      .then(code => {
        setExitCode(code);
        setPhase(code === 0 ? "done" : "failed");
      })
      .catch((e: any) => {
        setLines(prev => [...prev, `[error] ${e?.message ?? e}`]);
        setPhase("failed");
      });
  }, [command, args]);

  useEffect(() => {
    if (phase === "running" && lines.length === 0 && exitCode === null) {
      if (!destructive) start();
    }
  }, [phase, lines.length, exitCode, destructive, start]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines.length]);

  const close = () => onDone(phase === "done");

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 100,
    }}>
      <div style={{
        width: 720, maxWidth: "94vw", maxHeight: "86vh",
        background: "#1a1a1a",
        border: "1px solid #2a2a2a",
        borderRadius: 10,
        boxShadow: "0 14px 40px rgba(0,0,0,0.6)",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid #2a2a2a",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e6e6e6" }}>
            {title}
          </span>
          <span style={{ marginLeft: "auto", fontSize: 11,
                          color: phase === "done"   ? "#76b900"
                               : phase === "failed" ? "#e05252"
                               : phase === "running"? "#e8a000"
                               :                       "#9a9a9a" }}>
            {phase === "confirm" ? "Confirm action"
              : phase === "running" ? "Running…"
              : phase === "done"    ? "Completed"
              :                       `Failed${exitCode != null ? ` (exit ${exitCode})` : ""}`}
          </span>
        </div>

        {phase === "confirm" && (
          <div style={{ padding: "20px 20px 10px", fontSize: 13,
                        color: "#e6e6e6", lineHeight: 1.55 }}>
            {confirm ?? "Are you sure?"}
            {destructive && (
              <p style={{ marginTop: 12, fontSize: 12, color: "#e05252" }}>
                This action will remove files from your system.
              </p>
            )}
          </div>
        )}

        {phase !== "confirm" && (
          <pre ref={logRef} style={{
            flex: 1, margin: 0, padding: "12px 16px",
            background: "#0d0d0d",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12, lineHeight: 1.45,
            overflowY: "auto", whiteSpace: "pre-wrap",
            minHeight: 240, maxHeight: "60vh",
          }}>
            {lines.length === 0
              ? <span style={{ color: "#8a9ab0" }}>Starting…</span>
              : lines.map((line, i) => (
                  <span key={i} style={{ color: lineColor(line), display: "block" }}>
                    {line}
                  </span>
                ))}
          </pre>
        )}

        <div style={{
          padding: "12px 16px", borderTop: "1px solid #2a2a2a",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          {phase === "confirm" && (
            <>
              <button className="btn-component" onClick={() => onDone(false)}>
                Cancel
              </button>
              <button className="btn-component"
                      style={{ borderColor: "#e05252", color: "#e05252" }}
                      onClick={start}>
                {destructive ? "Uninstall" : "Continue"}
              </button>
            </>
          )}
          {phase === "running" && !cancelConfirm && (
            <button className="btn-component" onClick={() => setCancelConfirm(true)}>
              Cancel
            </button>
          )}
          {phase === "running" && cancelConfirm && (
            <>
              <span style={{ fontSize: 12, color: "#9a9a9a",
                             alignSelf: "center", marginRight: 8 }}>
                Cancel the sync?
              </span>
              <button className="btn-component"
                      onClick={() => setCancelConfirm(false)}>
                No, continue
              </button>
              <button className="btn-component"
                      style={{ borderColor: "#e05252", color: "#e05252" }}
                      onClick={() => onDone(false)}>
                Yes, cancel
              </button>
            </>
          )}
          {(phase === "done" || phase === "failed") && (
            <button className="btn-component" onClick={close}>Close</button>
          )}
        </div>
      </div>
    </div>
  );
}
