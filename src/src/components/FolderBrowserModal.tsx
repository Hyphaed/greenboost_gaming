import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { BrowseResult } from "../types";

// In-app folder browser for picking an external DLL folder. No native
// dialog plugin , the app has none installed and this avoids adding one
// (no new cargo/npm dependency, no Tauri capability change). Backed by the
// browse_folder Tauri command (src-tauri/src/dll_folders.rs), which lists
// subdirectories plus a shallow .dll count so the right folder is visible
// before you enter it.

export function FolderBrowserModal({
  initialPath, onPick, onClose,
}: {
  initialPath?: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [cwd, setCwd]       = useState<string | undefined>(initialPath);

  const load = useCallback((path: string | undefined) => {
    setError(null);
    invoke<BrowseResult>("browse_folder", { path: path ?? null })
      .then(setResult)
      .catch(e => setError(e?.message ?? String(e)));
  }, []);

  useEffect(() => { load(cwd); }, [cwd, load]);

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 560, maxWidth: "94vw", maxHeight: "80vh",
          background: "#1a1a1a",
          border: "1px solid #2a2a2a",
          borderRadius: 10,
          boxShadow: "0 14px 40px rgba(0,0,0,0.6)",
          display: "flex", flexDirection: "column", overflow: "hidden",
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid #2a2a2a",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e6e6e6" }}>
            Choose a DLL folder
          </span>
          <button
            className="btn-revert"
            style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 11 }}
            onClick={() => setCwd(result?.home)}
          >
            Home
          </button>
          <button
            className="btn-revert"
            style={{ padding: "4px 10px", fontSize: 11 }}
            disabled={!result?.parent}
            onClick={() => setCwd(result?.parent ?? undefined)}
          >
            Up
          </button>
        </div>

        <div style={{
          padding: "8px 20px", fontSize: 11, color: "#8a9ab0",
          borderBottom: "1px solid #2a2a2a", wordBreak: "break-all",
        }}>
          {result?.path ?? "…"}
          {result && result.dll_count > 0 && (
            <span style={{ color: "#76b900", marginLeft: 8 }}>
              {result.dll_count} .dll file{result.dll_count === 1 ? "" : "s"} here
            </span>
          )}
        </div>

        <div style={{ flex: 1, overflowY: "auto", minHeight: 240, maxHeight: "50vh" }}>
          {error && (
            <p style={{ padding: "12px 20px", fontSize: 12, color: "#e05252" }}>
              {error}
            </p>
          )}
          {!error && result?.entries.length === 0 && (
            <p style={{ padding: "12px 20px", fontSize: 12, color: "#8a9ab0" }}>
              No subfolders here.
            </p>
          )}
          {result?.entries.map(entry => (
            <div
              key={entry.path}
              className="gs-row"
              style={{ padding: "8px 20px", cursor: "pointer" }}
              onClick={() => setCwd(entry.path)}
            >
              <div className="gs-row-label">
                <div className="gs-row-title">{entry.name}</div>
              </div>
              <div className="gs-row-control">
                {entry.dll_count > 0 && (
                  <span style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 4,
                    background: "rgba(118,185,0,0.12)", color: "#76b900",
                    border: "1px solid rgba(118,185,0,0.3)",
                  }}>
                    {entry.dll_count} DLL{entry.dll_count === 1 ? "" : "s"}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>

        <div style={{
          padding: "12px 20px", borderTop: "1px solid #2a2a2a",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          <button className="btn-revert" onClick={onClose}>Cancel</button>
          <button
            className="btn-component"
            disabled={!result}
            title={result && result.dll_count === 0
              ? "This folder contains no .dll files , the game will launch normally with no overlay"
              : undefined}
            onClick={() => result && onPick(result.path)}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
