import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Game, DlssStatus } from "../types";
import { CollapsibleSection } from "./CollapsibleSection";
import { InfoTip } from "./InfoTip";
import { DLL_EXPLAIN } from "../dllInfo";

interface CachedDll {
  name: string; version: string; sha256: string;
  source: string; fetched_at: number; size_bytes: number; path: string;
}

const SHIPPED = "__shipped__";
const BACKUP_PREFIX = "__backup__:";

export function DllPicker({
  game, onApplied, refreshTrigger,
  dlssStatus, scanningDlss, onScanDlssStatus, onRestoreToShipped,
}: {
  game: Game; onApplied: () => void; refreshTrigger?: number;
  dlssStatus?: DlssStatus | null; scanningDlss?: boolean;
  onScanDlssStatus?: () => void; onRestoreToShipped?: () => void;
}) {
  const [cache, setCache] = useState<CachedDll[]>([]);
  const [busy, setBusy]   = useState<string | null>(null);
  const [msg, setMsg]     = useState<string | null>(null);
  // Per-DLL selected dropdown value: a cached version string, or the
  // SHIPPED sentinel. Only tracks entries the user has actually touched ,
  // everything else defaults to "newest cached version" at render time.
  const [picked, setPicked] = useState<Record<string, string>>({});

  useEffect(() => {
    invoke<CachedDll[]>("list_cached_dlls")
      .then(setCache)
      .catch(e => setMsg(`Cache read failed: ${e}`));
    // refreshTrigger bumps specifically after "Upgrade" (Games.tsx's
    // cacheRevision) completes , clear any manual
    // per-row picks so every select falls back to its default (newest
    // cached version, see `selected` below), matching what the update
    // just actually installed rather than a stale earlier manual choice.
    setPicked({});
  }, [refreshTrigger]);

  // Both the "Shipped: vX" and "Backup: vX (<date>)" options depend on
  // dlssStatus, which used to only populate after the user clicked "Scan
  // for updates" , so a game with a perfectly good .gdlss_original or
  // backup sitting on disk showed neither option until that extra click.
  // Fire the scan automatically once per selected game instead; Games.tsx
  // already resets dlssStatus to null on every game switch, so this only
  // re-fires when there's actually nothing to show yet.
  useEffect(() => {
    if (!dlssStatus && !scanningDlss && onScanDlssStatus) {
      onScanDlssStatus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game.path]);

  const apply = async (dllName: string, version: string) => {
    setBusy(dllName);
    setMsg(null);
    try {
      const out: string = version === SHIPPED
        ? await invoke("restore_dll_to_original", { dllName, gamePath: game.path })
        : version.startsWith(BACKUP_PREFIX)
          ? await invoke("restore_dll_from_backup", {
              dllName, gamePath: game.path,
              backupPath: version.slice(BACKUP_PREFIX.length),
            })
          : await invoke("install_cached_dll", { dllName, gamePath: game.path, version });
      setMsg(out);
      onApplied();
    } catch (e: any) {
      setMsg(`Apply failed: ${e?.message ?? e}`);
    }
    setBusy(null);
  };

  return (
    <CollapsibleSection title="DLSS SETTINGS" defaultOpen>
      {(onScanDlssStatus || onRestoreToShipped) && (
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "0 16px 10px", flexWrap: "wrap", gap: 8,
        }}>
          <span style={{ fontSize: 11, color: "#8a9ab0" }}>
            {dlssStatus
              ? `Checked ${new Date(dlssStatus.scanned_at * 1000).toLocaleTimeString()} · ${dlssStatus.out_of_date} of ${dlssStatus.scanned} out of date`
              : "Not checked for updates yet"}
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            {onScanDlssStatus && (
              <button className="btn-revert" style={{ padding: "4px 10px", fontSize: 11 }}
                      onClick={onScanDlssStatus} disabled={scanningDlss}>
                {scanningDlss ? "Scanning…" : dlssStatus ? "Re-scan" : "Scan for updates"}
              </button>
            )}
            {onRestoreToShipped && dlssStatus?.findings.some(f => f.can_restore_shipped) && (
              <button className="btn-revert" style={{ padding: "4px 10px", fontSize: 11 }}
                      onClick={onRestoreToShipped}
                      title="Puts back exactly what the game originally shipped with">
                Restore all to shipped version
              </button>
            )}
          </div>
        </div>
      )}
      {game.dlls.map(dll => {
        // Newest-fetched-first , list_cached_dlls() already sorts this way.
        const versions = cache.filter(c =>
          c.name.toLowerCase() === dll.name.toLowerCase());
        const finding = dlssStatus?.findings.find(f =>
          f.name.toLowerCase() === dll.name.toLowerCase());
        const canShip = !!finding?.can_restore_shipped;
        const restorePoints = finding?.restore_points ?? [];
        const hasAnyOption = versions.length > 0 || canShip || restorePoints.length > 0;
        const selected = picked[dll.name]
          ?? (versions[0]?.version
              ?? (canShip ? SHIPPED
              : (restorePoints[0] ? `${BACKUP_PREFIX}${restorePoints[0].path}` : "")));
        return (
          <div key={dll.path} className="gs-row"
               style={{ padding: "12px 16px" }}>
            <div className="gs-row-label">
              <div className="gs-row-title">
                {dll.name}
                {DLL_EXPLAIN[dll.name.toLowerCase()] && <InfoTip>{DLL_EXPLAIN[dll.name.toLowerCase()]}</InfoTip>}
              </div>
              <div className="gs-row-sub">
                installed: <code>{dll.version || "unknown"}</code>
                {" · "} type: {dll.tech_type || "unknown"}
                {finding?.upgraded && finding.shipped && (
                  <> · <span style={{ color: "#a5b4fc" }}>game shipped with v{finding.shipped}</span></>
                )}
              </div>
            </div>
            <div className="gs-row-control"
                 style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
              {finding?.upgraded && (
                <span style={{
                  fontSize: 11, padding: "2px 8px", borderRadius: 4, fontWeight: 600,
                  background: "rgba(99,102,241,0.12)", color: "#a5b4fc",
                  border: "1px solid rgba(99,102,241,0.3)", whiteSpace: "nowrap",
                }} title={`GreenBoost upgraded this from the shipped v${finding.shipped}`}>
                  ⬆ upgraded
                </span>
              )}
              {finding && (
                finding.needs_update ? (
                  <span style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 4, fontWeight: 600,
                    background: "rgba(232,160,0,0.15)", color: "#e8a000",
                    border: "1px solid rgba(232,160,0,0.4)", whiteSpace: "nowrap",
                  }} title={`Latest known: v${finding.latest}`}>
                    ↑ update to v{finding.latest}
                  </span>
                ) : (
                  <span style={{
                    fontSize: 11, padding: "2px 7px", borderRadius: 4,
                    background: "rgba(118,185,0,0.1)", color: "#76b900",
                    border: "1px solid rgba(118,185,0,0.25)", whiteSpace: "nowrap",
                  }}>
                    up to date
                  </span>
                )
              )}
              <select className="gs-select"
                      disabled={!hasAnyOption || busy !== null}
                      value={selected}
                      onChange={e => setPicked(prev => ({ ...prev, [dll.name]: e.target.value }))}>
                {!hasAnyOption && (
                  <option value="">(not in cache , Sync first)</option>
                )}
                {canShip && (
                  <option value={SHIPPED}>Shipped: v{finding!.shipped}</option>
                )}
                {versions.map(v => (
                  <option key={v.version} value={v.version}>
                    Cached: v{v.version}
                  </option>
                ))}
                {restorePoints.map(rp => (
                  <option key={rp.path} value={`${BACKUP_PREFIX}${rp.path}`}>
                    Backup: v{rp.label} ({new Date(rp.mtime * 1000).toLocaleDateString()})
                  </option>
                ))}
              </select>
              <button
                className="btn-component"
                disabled={!hasAnyOption || busy !== null}
                onClick={() => apply(dll.name, selected)}
                title={!hasAnyOption
                  ? "Run Sync DLSS library first to populate the cache"
                  : selected === SHIPPED
                    ? `Restore ${dll.name} to the version this game shipped with`
                    : `Replace in-game ${dll.name} with the selected cached version`}
              >
                {busy === dll.name ? "Applying…" : "Apply"}
              </button>
            </div>
          </div>
        );
      })}
      {msg && (
        <p style={{ fontSize: 12, color: "#76b900",
                    padding: "0 16px 12px", margin: 0 }}>{msg}</p>
      )}
    </CollapsibleSection>
  );
}
