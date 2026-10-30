import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Dlss5AssetList } from "../types";
import { FolderBrowserModal } from "./FolderBrowserModal";

// Shown before "Enable DLSS 5" is ever pressed, not just referenced in an
// (i) tooltip: GreenBoost never fetches, bundles, or redistributes
// nvngx_dlssnr.dll , NVIDIA's own model DLL , so the user has to place it
// by hand, and needs to know exactly where. The DLSS5 runtime itself ships
// with the Suite (GreenBoost's own build , see libraries/dlssnr/runtime/),
// so there's nothing else to place here. Backed by dlss5_list_assets
// (gb_gaming.dlss5.list_assets()), so the checklist reflects what's
// ACTUALLY on disk, not a static list. The "Select DLSS5 folder" button
// calls dlss5_set_asset_dir (gb_gaming.dlss5.set_asset_dir_override()) to
// point that check at a different folder than the default.

export function Dlss5FilesModal({
  onConfirm, onClose,
}: {
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [assets, setAssets] = useState<Dlss5AssetList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);

  const load = useCallback(() => {
    setError(null);
    invoke<Dlss5AssetList>("dlss5_list_assets")
      .then(setAssets)
      .catch(e => setError(e?.message ?? String(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  const pickFolder = useCallback(async (path: string) => {
    setBrowsing(false);
    try {
      await invoke("dlss5_set_asset_dir", { path });
      load();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  }, [load]);

  const requiredMissing = (assets?.files ?? []).filter(f => f.required && !f.present);
  const canEnable = !!assets && requiredMissing.length === 0;

  // FolderBrowserModal renders below as a SIBLING of the scrim div, not a
  // child of it , its own scrim click calls onClose without stopping
  // propagation, and this modal's own scrim div ALSO closes on any click
  // that reaches it. Nesting them would bubble a folder-browser scrim click
  // straight into this modal's onClose and dismiss both at once.
  return (
    <>
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
          width: 560, maxWidth: "94vw", maxHeight: "84vh",
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
        }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e6e6e6" }}>
            Files needed for DLSS 5 (Neural Rendering)
          </span>
        </div>

        <div style={{ padding: "12px 20px 0", fontSize: 12, color: "#c8d0da", lineHeight: 1.5 }}>
          GreenBoost never downloads, bundles, or redistributes nvngx_dlssnr.dll , NVIDIA's
          own model DLL , place it yourself in the folder below. The DLSS 5 runtime itself
          ships with the Suite, so there's nothing else to place here.
        </div>

        <div style={{
          margin: "10px 20px 4px", padding: "8px 10px", fontSize: 11, color: "#8a9ab0",
          background: "#111", border: "1px solid #2a2a2a", borderRadius: 6, wordBreak: "break-all",
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
        }}>
          <span style={{ wordBreak: "break-all" }}>{assets?.asset_dir ?? "…"}</span>
          <button className="btn-revert" style={{ flexShrink: 0, padding: "3px 8px", fontSize: 11 }}
                  onClick={() => setBrowsing(true)}>
            Select DLSS5 folder
          </button>
        </div>

        <div style={{ flex: 1, overflowY: "auto", minHeight: 200, maxHeight: "44vh", padding: "0 20px" }}>
          {error && (
            <p style={{ fontSize: 12, color: "#e05252" }}>{error}</p>
          )}
          {!error && !assets && (
            <p style={{ fontSize: 12, color: "#8a9ab0" }}>Loading…</p>
          )}
          {assets?.files.map(f => (
            <div key={f.name} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "5px 0", fontSize: 12,
            }}>
              <span style={{
                width: 14, flexShrink: 0,
                color: f.present ? "#76b900" : f.required ? "#e05252" : "#8a9ab0",
              }}>
                {f.present ? "✓" : "✗"}
              </span>
              <code style={{ color: f.present ? "#e6e6e6" : "#9a9ab0" }}>{f.name}</code>
              {f.required && !f.present && (
                <span style={{ fontSize: 10, color: "#e05252", marginLeft: "auto" }}>required</span>
              )}
              {!f.required && (
                <span style={{ fontSize: 10, color: "#8a9ab0", marginLeft: "auto" }}>
                  optional , used if present
                </span>
              )}
            </div>
          ))}
        </div>

        {requiredMissing.length > 0 && assets && (
          <p style={{ fontSize: 11, color: "#e05252", padding: "8px 20px 0" }}>
            Missing: {requiredMissing.map(f => f.name).join(", ")}
          </p>
        )}

        <div style={{
          padding: "14px 20px", borderTop: "1px solid #2a2a2a",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          <button className="btn-revert" onClick={onClose}>Cancel</button>
          <button
            className="btn-component"
            disabled={!canEnable}
            title={!canEnable ? "Place the required files above first" : undefined}
            onClick={onConfirm}
          >
            Enable DLSS 5
          </button>
        </div>
      </div>
    </div>

    {browsing && (
      <FolderBrowserModal
        initialPath={assets?.asset_dir}
        onClose={() => setBrowsing(false)}
        onPick={pickFolder}
      />
    )}
    </>
  );
}
