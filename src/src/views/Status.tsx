import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { SystemStatus, GpuInfo, GpuMetrics, InstallStreamProps, NvidiaUpdateStatus, PoolBrief } from "../types";
import { Icon } from "../icons";
import { InstallStreamModal } from "../components/InstallStreamModal";

type SessionRecord = {
  appid: string;
  game_name: string;
  gpu: string;
  vram_mb: number;
  peak_vram_mb?: number;
  avg_vram_mb?: number;
  vram_samples?: number;
  vram_source?: string;
  peak_t2_mb?: number;
  duration_s: number;
  rc: number;
  ts: string;
};

function formatDuration(s: number): string {
  const totalSeconds = Math.round(s);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const sec = totalSeconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function formatVram(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function formatRelativeTime(ts: string): string {
  const then = new Date(ts).getTime();
  if (isNaN(then)) return ts;
  const diffMs = Date.now() - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffH = Math.floor(diffMin / 60);
  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin} minute${diffMin !== 1 ? "s" : ""} ago`;
  if (diffH   < 24) return `${diffH} hour${diffH !== 1 ? "s" : ""} ago`;
  // > 24 h: show the date
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function StatusView() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [gpu, setGpu]       = useState<GpuInfo | null>(null);
  const [layersMsg, setLayersMsg] = useState<string | null>(null);
  const [protonMsg, setProtonMsg] = useState<string | null>(null);
  const [modal, setModal] = useState<null | InstallStreamProps>(null);
  const [moduleMsg, setModuleMsg] = useState<string | null>(null);
  const [nvUpdate, setNvUpdate] = useState<NvidiaUpdateStatus | null>(null);
  const [rebootMsg, setRebootMsg] = useState<string | null>(null);
  const [liveMetrics, setLiveMetrics] = useState<GpuMetrics | null>(null);
  const [poolBrief, setPoolBrief] = useState<PoolBrief | null>(null);
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [sessionHistory, setSessionHistory] = useState<SessionRecord[]>([]);

  const refreshStatus = useCallback(() => {
    invoke<SystemStatus>("get_status").then(setStatus).catch(console.error);
  }, []);

  useEffect(() => {
    refreshStatus();
    invoke<GpuInfo>("get_gpu").then(setGpu).catch(console.error);
    invoke<NvidiaUpdateStatus>("check_nvidia_update").then(setNvUpdate).catch(console.error);
    invoke<string | null>("get_active_gpu_profile").then(setActiveProfile).catch(() => {});
    invoke<SessionRecord[]>("get_session_history").then(setSessionHistory).catch(() => {});
  }, [refreshStatus]);

  useEffect(() => {
    const tick = () => invoke<GpuMetrics>("poll_gpu_metrics").then(setLiveMetrics).catch(() => {});
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, []);

  // T2 DDR pool state , independent of NVML/liveMetrics: the kernel module
  // can be loaded (or not) regardless of GPU driver state, so this polls on
  // its own cadence and get_pool_brief already returns null gracefully
  // when the module isn't loaded (live_stats.rs::get_pool_brief_impl).
  useEffect(() => {
    const tick = () => invoke<PoolBrief | null>("get_pool_brief").then(setPoolBrief).catch(() => {});
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, []);

  const openModal = (m: Omit<InstallStreamProps, "onDone">,
                    successMsg: string,
                    setBanner: (s: string | null) => void) => {
    setBanner(null);
    setModal({
      ...m,
      onDone: (ok) => {
        setModal(null);
        refreshStatus();
        setBanner(ok ? successMsg : "Failed , see modal log for details.");
      },
    });
  };

  const onInstallModule = () => openModal({
    title:   "Install GreenBoost Kernel Module",
    command: "install_module_streaming",
  }, "GreenBoost kernel module installed. Reboot may be required.", setModuleMsg);

  const onInstallLayers   = () => openModal({
    title:   "Install GreenBoost Graphics Layers",
    command: "install_layers_streaming",
  }, "GreenBoost Vulkan + OpenGL layers installed.", setLayersMsg);

  const onUninstallLayers = () => openModal({
    title:       "Uninstall GreenBoost Graphics Layers",
    command:     "uninstall_layers_streaming",
    destructive: true,
    confirm: "Remove the GreenBoost Vulkan layer, manifest, and OpenGL "
           + "interposer (libgb_gl.so) from /usr/local/lib and "
           + "/usr/share/vulkan/implicit_layer.d/?",
  }, "GreenBoost layers removed.", setLayersMsg);

  const onInstallProton   = () => openModal({
    title:   "Install GreenBoost Proton",
    command: "install_proton_streaming",
  }, "GreenBoost Proton installed.  Restart Steam to see it.", setProtonMsg);

  const onUninstallProton = () => openModal({
    title:       "Uninstall GreenBoost Proton",
    command:     "uninstall_proton_streaming",
    destructive: true,
    confirm: "Remove the GreenBoost Proton compatibility tool from "
           + "every Steam root on this machine?  Steam must be "
           + "restarted afterwards.",
  }, "GreenBoost Proton removed.", setProtonMsg);

  const layersInstalled = status?.vulkan_layer === "Installed" || status?.gl_layer === "Installed";
  const protonInstalled = !!status?.proton_installed;

  const onUpgradeNvidia = () => openModal({
    title:   "Upgrade NVIDIA Driver",
    command: "upgrade_nvidia_streaming",
    confirm: "This will upgrade all packages via your system package manager. A reboot is required after the upgrade to activate the new driver.",
  }, "Driver upgraded. Reboot to activate.", () => {
    invoke<NvidiaUpdateStatus>("check_nvidia_update").then(setNvUpdate).catch(console.error);
  });

  const onRebootNow = () => {
    if (window.confirm(
      "Reboot your computer now to activate the updated NVIDIA driver?\n\n" +
      "All unsaved work will be lost."
    )) {
      setRebootMsg(null);
      // reboot_system asks systemctl to reboot and, on success, the whole
      // app (and this component) disappears with it , there's nothing more
      // to render. On failure (no polkit auth, systemctl refused) the app
      // stays up, and previously nothing told the user the click did
      // nothing: the button just sat there with the machine still on.
      invoke("reboot_system").catch((e) =>
        setRebootMsg(`Reboot failed: ${e}`));
    }
  };

  return (
    <div className="content-scroll">
      <div style={{ maxWidth: 700, margin: "0 auto" }}>
        <p className="section-title">My Gaming Rig</p>
        <div className="section-card">
          {status?.cpu_name && (
            <div className="info-row">
              <div className="info-label">CPU</div>
              <div className="info-value" style={{ maxWidth: 380, textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>
                {status.cpu_name}
              </div>
            </div>
          )}
          {status?.total_ram_gb != null && status.total_ram_gb > 0 && (
            <div className="info-row">
              <div className="info-label">RAM</div>
              <div className="info-value">{status.total_ram_gb.toFixed(0)} GB</div>
            </div>
          )}
          {gpu && (
            <div className="info-row">
              <div>
                <div className="info-label">Graphics Card</div>
              </div>
              <div className="info-value">{gpu.name}</div>
            </div>
          )}
          {status && (
            <>
              <div className="info-row">
                <div className="info-label">Driver</div>
                <div className="info-value" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span>{status.nvidia_driver}</span>
                  {status.nvidia_mismatch && (
                    <span style={{
                      fontSize: 11, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(255,165,0,0.15)", color: "#ffb347",
                      border: "1px solid rgba(255,165,0,0.35)", whiteSpace: "nowrap",
                    }}>
                      update pending → {status.nvidia_mismatch.on_disk}
                    </span>
                  )}
                  {nvUpdate === null && (
                    <span style={{
                      fontSize: 11, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(154,154,176,0.1)", color: "#8a9ab0",
                      border: "1px solid rgba(154,154,176,0.25)", whiteSpace: "nowrap",
                    }}>
                      checking for updates…
                    </span>
                  )}
                  {nvUpdate && !nvUpdate.checked && (
                    <span
                      title={`No supported package manager detected (source: ${nvUpdate.source}) , install status unknown`}
                      style={{
                        fontSize: 11, padding: "2px 7px", borderRadius: 4,
                        background: "rgba(154,154,176,0.1)", color: "#8a9ab0",
                        border: "1px solid rgba(154,154,176,0.25)", whiteSpace: "nowrap",
                      }}>
                      not checked for updates
                    </span>
                  )}
                  {nvUpdate && nvUpdate.checked && !nvUpdate.update_available && (
                    <span style={{
                      fontSize: 11, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(118,185,0,0.1)", color: "#76b900",
                      border: "1px solid rgba(118,185,0,0.25)", whiteSpace: "nowrap",
                    }}>
                      latest from {nvUpdate.source}
                    </span>
                  )}
                  {nvUpdate && nvUpdate.checked && nvUpdate.update_available && (
                    <button
                      onClick={onUpgradeNvidia}
                      style={{
                        fontSize: 11, padding: "2px 10px", borderRadius: 4,
                        background: "rgba(232,160,0,0.15)", color: "#e8a000",
                        border: "1px solid rgba(232,160,0,0.4)", cursor: "pointer",
                        whiteSpace: "nowrap", fontWeight: 600,
                      }}
                      title={`Update to ${nvUpdate.available_version ?? "new version"}`}
                    >
                      ↑ Update {nvUpdate.available_version ?? ""}
                    </button>
                  )}
                </div>
              </div>
              <div className="info-row">
                <div className="info-label">GreenBoost Module</div>
                <div className={`info-value ${status.module === "Loaded" ? "ok" : "warn"}`}>
                  {status.module}
                  {status.module_version && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: "#8a9ab0" }}>
                      v{status.module_version}
                    </span>
                  )}
                  {status.greenboost_gaming_mode && (
                    <span style={{
                      marginLeft: 8, fontSize: 11, padding: "1px 6px", borderRadius: 4,
                      background: "rgba(34,197,94,0.15)", border: "1px solid rgba(34,197,94,0.3)",
                      color: "#86efac",
                    }}>
                      gaming
                    </span>
                  )}
                </div>
              </div>
              <div className="info-row">
                <div className="info-label">Vulkan Layer</div>
                <div className={`info-value ${status.vulkan_layer === "Installed" ? "ok" : "warn"}`}>{status.vulkan_layer}</div>
              </div>
              <div className="info-row">
                <div className="info-label">CPU Governor</div>
                <div className={`info-value ${status.cpu_governor === "performance" ? "ok" : ""}`}>{status.cpu_governor}</div>
              </div>
            </>
          )}
          {gpu && (
            <>
              <div className="info-row">
                <div className="info-label">GPU Temperature</div>
                <div className="info-value">{gpu.temp}</div>
              </div>
              <div className="info-row">
                <div className="info-label">Power Usage</div>
                <div className="info-value">{gpu.power_usage}</div>
              </div>
              <div className="info-row">
                <div className="info-label">Fan Speed</div>
                <div className="info-value">{gpu.fan_speed}</div>
              </div>
            </>
          )}
          {activeProfile && (
            <div className="info-row">
              <div className="info-label">Active GPU Profile</div>
              <div className="info-value">
                <span style={{
                  background: "rgba(118,185,0,0.12)", color: "#76b900",
                  padding: "2px 8px", borderRadius: 4,
                  fontSize: 12, fontWeight: 600,
                }}>
                  {activeProfile}
                </span>
              </div>
            </div>
          )}
          {status?.kernel_version && (
            <div className="info-row">
              <div className="info-label">Kernel</div>
              <div className="info-value" style={{ fontFamily: "monospace", fontSize: 12 }}>
                {status.kernel_version}
              </div>
            </div>
          )}
          {status?.session_type && (
            <div className="info-row">
              <div className="info-label">Display Server</div>
              <div className="info-value" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{
                  fontSize: 11, padding: "2px 7px", borderRadius: 4,
                  background: status.session_type === "wayland"
                    ? "rgba(34,211,238,0.12)" : "rgba(118,185,0,0.12)",
                  color: status.session_type === "wayland" ? "#22d3ee" : "#76b900",
                  border: `1px solid ${status.session_type === "wayland"
                    ? "rgba(34,211,238,0.3)" : "rgba(118,185,0,0.25)"}`,
                  fontWeight: 600, textTransform: "capitalize",
                }}>
                  {status.session_type}
                </span>
              </div>
            </div>
          )}
        </div>

        {(liveMetrics || poolBrief) && (
          <>
            <p className="section-title" style={{ marginTop: 24 }}>Live GPU</p>
            <div className="section-card">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "2px 0" }}>
                {[
                  ...(liveMetrics ? [
                    { label: "Temperature", value: liveMetrics.temp_c != null ? `${liveMetrics.temp_c} °C` : ",", warn: (liveMetrics.temp_c ?? 0) > 85 },
                    { label: "GPU Load",    value: liveMetrics.gpu_util_pct != null ? `${liveMetrics.gpu_util_pct}%` : "," },
                    { label: "Fan",         value: liveMetrics.fan_pct != null ? `${liveMetrics.fan_pct}%` : "," },
                    { label: "Power",       value: liveMetrics.power_w != null
                      ? `${liveMetrics.power_w.toFixed(0)} / ${liveMetrics.power_limit_w?.toFixed(0) ?? "?"} W`
                      : "," },
                    { label: "GPU Clock",  value: liveMetrics.clock_gpu_mhz != null ? `${liveMetrics.clock_gpu_mhz} MHz` : "," },
                    { label: "Mem Clock",  value: liveMetrics.clock_mem_mhz != null ? `${liveMetrics.clock_mem_mhz} MHz` : "," },
                    { label: "VRAM Used",  value: liveMetrics.vram_used_mb != null
                      ? `${liveMetrics.vram_used_mb.toLocaleString()} / ${liveMetrics.vram_total_mb?.toLocaleString() ?? "?"} MB`
                      : "," },
                  ] : []),
                  // T2 DDR Used , the game-side counterpart to "VRAM Used" above:
                  // once VRAM fills, GreenBoost spills into this pool. Only shown
                  // when the kernel module is actually loaded (poolBrief != null);
                  // get_pool_brief_impl returns null otherwise rather than a
                  // misleading "0".
                  ...(poolBrief ? [{
                    label: "T2 DDR Used",
                    value: formatVram(poolBrief.t2_alloc_mb ?? poolBrief.t2_alloc_gb * 1024)
                      + ` / ${poolBrief.t2_max_gb} GB`,
                    warn: (poolBrief.t2_alloc_mb ?? poolBrief.t2_alloc_gb) > 0,
                  }] : []),
                ].map(({ label, value, warn }) => (
                  <div key={label} className="info-row" style={{ paddingTop: 8, paddingBottom: 8 }}>
                    <div className="info-label">{label}</div>
                    <div className="info-value" style={warn ? { color: "#f87171" } : undefined}>{value}</div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {sessionHistory.length > 0 && (
          <>
            <p className="section-title" style={{ marginTop: 24 }}>Recent Sessions</p>
            <div className="section-card" style={{ padding: 0, overflow: "hidden" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
                    {["Game", "Duration", "VRAM", "Result", "When"].map(h => (
                      <th key={h} style={{
                        padding: "8px 14px", textAlign: "left",
                        color: "#8a9ab0", fontWeight: 600, fontSize: 11,
                        textTransform: "uppercase", letterSpacing: "0.04em",
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sessionHistory.slice(0, 8).map((s, i) => (
                    <tr key={i} style={{
                      borderBottom: i < Math.min(sessionHistory.length, 8) - 1
                        ? "1px solid rgba(255,255,255,0.04)" : undefined,
                    }}>
                      <td style={{ padding: "8px 14px", color: "#ccc", fontWeight: 500 }}>
                        {s.game_name || s.appid}
                      </td>
                      <td style={{ padding: "8px 14px", color: "#9a9a9a" }}>
                        {formatDuration(s.duration_s)}
                      </td>
                      <td style={{ padding: "8px 14px" }}>
                        {(() => {
                          // s.vram_mb is the GPU's static total capacity, not
                          // usage , it can never be a valid "peak" fallback
                          // (that comparison, `peak_vram_mb > vram_mb`, is
                          // structurally impossible: used can't exceed
                          // total). Only render a real sample.
                          const hasSample = (s.vram_samples ?? (s.peak_vram_mb ? 1 : 0)) > 0
                            && !!s.peak_vram_mb;
                          if (!hasSample) {
                            return <span style={{ color: "#4b5563" }}>,</span>;
                          }
                          // Same 85%-of-card threshold as the Games view badge.
                          const cap = liveMetrics?.vram_total_mb
                            ? liveMetrics.vram_total_mb * 0.85 : 10240;
                          const warn = s.peak_vram_mb! > cap;
                          const otherApps = s.vram_source === "gpu_total";
                          return (
                            <span style={{ color: warn ? "#f87171" : "#9a9a9a" }}
                                  title={`Peak: ${formatVram(s.peak_vram_mb!)}`
                                    + (s.avg_vram_mb ? ` · Avg: ${formatVram(s.avg_vram_mb)}` : "")
                                    + (otherApps ? " · includes other GPU apps (per-process reading unavailable)" : "")}>
                              {warn ? "⚠ " : ""}{formatVram(s.peak_vram_mb!)}{otherApps ? "*" : ""}
                            </span>
                          );
                        })()}
                      </td>
                      <td style={{ padding: "8px 14px" }}>
                        {s.rc === 0 ? (
                          <span style={{ color: "#76b900", fontWeight: 700 }}>✓</span>
                        ) : (
                          <span style={{ color: "#e05252", fontWeight: 700 }}>✗ ({s.rc})</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 14px", color: "#8a9ab0" }}>
                        {formatRelativeTime(s.ts)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {status?.nvidia_mismatch && (
          <div style={{
            background: "rgba(255, 165, 0, 0.08)",
            border: "1px solid rgba(255, 165, 0, 0.35)",
            borderRadius: 8,
            padding: "14px 18px",
            marginBottom: 16,
            display: "flex",
            alignItems: "flex-start",
            gap: 14,
          }}>
            <div style={{ color: "#ffb347", marginTop: 1, flexShrink: 0 }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: "#ffb347", marginBottom: 4 }}>
                NVIDIA driver update pending , reboot required
              </div>
              <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.6, marginBottom: 10 }}>
                Driver <strong style={{ color: "#ccc" }}>{status.nvidia_mismatch.on_disk}</strong> was
                installed while module <strong style={{ color: "#ccc" }}>{status.nvidia_mismatch.loaded}</strong> is
                still running. GPU monitoring and overclocking are unavailable until you reboot.
              </div>
              <button className="btn-optimize" onClick={onRebootNow}
                      style={{ fontSize: 12 }}>
                Reboot Now
              </button>
              {rebootMsg && <p className="component-msg">{rebootMsg}</p>}
            </div>
          </div>
        )}

        {status && status.module !== "Loaded" && (
          <div style={{
            background: "rgba(255, 165, 0, 0.08)",
            border: "1px solid rgba(255, 165, 0, 0.35)",
            borderRadius: 8,
            padding: "14px 18px",
            marginBottom: 16,
            display: "flex",
            alignItems: "flex-start",
            gap: 14,
          }}>
            <div style={{ color: "#ffb347", marginTop: 1, flexShrink: 0 }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: "#ffb347", marginBottom: 4 }}>
                GreenBoost Kernel Module Not Loaded
              </div>
              <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.6, marginBottom: 10 }}>
                The GreenBoost kernel module provides GPU memory extensions (GVM) and low-level
                hardware tuning. Without it, VRAM extension, power management, and overclocking
                features are unavailable. Install it from the official GreenBoost sources on GitLab.
              </div>
              <button className="btn-optimize" onClick={onInstallModule}
                      style={{ fontSize: 12 }}>
                Install GreenBoost Module
              </button>
              {moduleMsg && (
                <div style={{ marginTop: 8, fontSize: 12,
                              color: moduleMsg.startsWith("Failed") ? "#e05252" : "#76b900" }}>
                  {moduleMsg}
                </div>
              )}
            </div>
          </div>
        )}

        <p className="section-title">GreenBoost Components</p>
        <div className="section-card">
          {/* Graphics layers group , Vulkan implicit layer + OpenGL LD_PRELOAD interposer.
              Both are installed/uninstalled in a single pass by install.sh. */}
          <div className="info-row">
            <div>
              <div className="info-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Icon.Layers /> Graphics Layers
              </div>
              <div style={{ fontSize: 11, color: "#8a9ab0", marginTop: 2 }}>
                Vulkan implicit layer + OpenGL LD_PRELOAD interposer for T2/T3 memory overflow
              </div>
              {/* Sub-rows: individual status for each layer */}
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                  <span style={{ color: "#8a9ab0", minWidth: 90 }}>Vulkan layer</span>
                  <span style={{
                    padding: "1px 7px", borderRadius: 4, fontWeight: 600,
                    background: status?.vulkan_layer === "Installed"
                      ? "rgba(118,185,0,0.12)" : "rgba(255,165,0,0.1)",
                    color: status?.vulkan_layer === "Installed" ? "#76b900" : "#ffb347",
                    border: `1px solid ${status?.vulkan_layer === "Installed"
                      ? "rgba(118,185,0,0.25)" : "rgba(255,165,0,0.3)"}`,
                  }}>
                    {status?.vulkan_layer ?? ","}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                  <span style={{ color: "#8a9ab0", minWidth: 90 }}>OpenGL layer</span>
                  <span style={{
                    padding: "1px 7px", borderRadius: 4, fontWeight: 600,
                    background: status?.gl_layer === "Installed"
                      ? "rgba(118,185,0,0.12)" : "rgba(255,165,0,0.1)",
                    color: status?.gl_layer === "Installed" ? "#76b900" : "#ffb347",
                    border: `1px solid ${status?.gl_layer === "Installed"
                      ? "rgba(118,185,0,0.25)" : "rgba(255,165,0,0.3)"}`,
                  }}>
                    {status?.gl_layer ?? ","}
                  </span>
                </div>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {layersInstalled ? (
                <button className="btn-component"
                        style={{ borderColor: "#e05252", color: "#e05252" }}
                        onClick={onUninstallLayers}>
                  Uninstall
                </button>
              ) : (
                <button className="btn-component" onClick={onInstallLayers}>
                  Install
                </button>
              )}
            </div>
          </div>
          {layersMsg && <p className="component-msg">{layersMsg}</p>}

          <div className="info-row" style={{ marginTop: 12 }}>
            <div>
              <div className="info-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Icon.Package /> GreenBoost Proton
              </div>
              <div style={{ fontSize: 11, color: "#8a9ab0", marginTop: 2 }}>
                Custom Proton build with vkd3d-proton, dxvk-nvapi, and NTSync support
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {status && (
                <span className={`info-value ${protonInstalled ? "ok" : "warn"}`}>
                  {protonInstalled ? "Installed" : "Missing"}
                </span>
              )}
              {protonInstalled ? (
                <button className="btn-component"
                        style={{ borderColor: "#e05252", color: "#e05252" }}
                        onClick={onUninstallProton}>
                  Uninstall
                </button>
              ) : (
                <button className="btn-component" onClick={onInstallProton}>
                  Install
                </button>
              )}
            </div>
          </div>
          {protonMsg && <p className="component-msg">{protonMsg}</p>}
          {status?.proton_wrapper_stale === true && (
            <p className="component-msg" style={{ color: "#e8a000" }}>
              Deployed copy differs from this checkout , re-run{" "}
              <code>sudo ./install.sh</code> to pick up recent fixes before
              relying on GreenBoost Proton.
            </p>
          )}

          <div className="info-row" style={{ marginTop: 12 }}>
            <div>
              <div className="info-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Icon.Cpu /> GreenBoost Kernel Module
              </div>
              <div style={{ fontSize: 11, color: "#8a9ab0", marginTop: 2 }}>
                Kernel module for GVM (GPU virtual memory extension) and hardware tuning
                {status?.module_version && <span> , v{status.module_version}</span>}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {status && (
                <span className={`info-value ${status.module === "Loaded" ? "ok" : "warn"}`}>
                  {status.module === "Loaded" ? "Loaded" : "Not Loaded"}
                </span>
              )}
              <button className="btn-component" onClick={onInstallModule}>
                {status?.module === "Loaded" ? "Reinstall" : "Install"}
              </button>
            </div>
          </div>
          {moduleMsg && <p className="component-msg">{moduleMsg}</p>}
        </div>

        {modal && <InstallStreamModal {...modal} />}

        <p className="section-title">GreenBoost Gaming Suite</p>
        <div className="section-card">
          <p style={{ fontSize: 13, color: "#9a9a9a", lineHeight: 1.7, margin: 0 }}>
            GreenBoost Gaming Suite optimizes your Linux gaming experience through hardware-aware
            configuration tuning, RTX technology enablement, and Greenboost Proton integration.

          </p>
        </div>
      </div>
    </div>
  );
}
