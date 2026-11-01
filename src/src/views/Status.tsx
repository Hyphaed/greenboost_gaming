import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { SystemStatus, GpuInfo, GpuMetrics, InstallStreamProps, NvidiaUpdateStatus, PoolBrief, ControllerInfo } from "../types";
import { Icon } from "../icons";
import { InstallStreamModal } from "../components/InstallStreamModal";
import { UpdateBanner } from "../components/UpdateBanner";
import { StatusHero, type StatusCheck } from "../components/StatusHero";
import { LiveGauges } from "../components/LiveGauges";

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
  const [controllers, setControllers] = useState<ControllerInfo[]>([]);

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

  // Connected gamepads , polled independently of GPU state since a
  // controller can be plugged/unplugged or drop its Bluetooth link at any
  // time. get_controllers() is a read-only sysfs scan (see controllers.rs).
  useEffect(() => {
    const tick = () => invoke<ControllerInfo[]>("get_controllers").then(setControllers).catch(() => {});
    tick();
    const t = setInterval(tick, 3000);
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

  // Updates card , actually take the update, rather than describing how to.
  // Both paths fetch the newer sources and run that project's own installer,
  // which is what a human would have done by hand.
  const onUpgrade = (key: string) => {
    if (key === "core") {
      openModal({
        title:   "Upgrade GreenBoost core",
        command: "upgrade_core_streaming",
        confirm: "Pull the latest GreenBoost core sources, run its installer, "
               + "and reload the kernel module so the new build is the one "
               + "running? This needs administrator authorization and takes a "
               + "few minutes.",
      }, "GreenBoost core upgraded.", setModuleMsg);
    } else {
      openModal({
        title:   "Upgrade GreenBoost Gaming Suite",
        command: "upgrade_suite_streaming",
        confirm: "Pull the latest Gaming Suite sources and re-run install.sh? "
               + "This rebuilds the app and can take several minutes. You'll "
               + "need to close and reopen the app afterwards to run the new "
               + "version.",
      }, "Suite upgraded , close and reopen the app to run the new version.",
         setModuleMsg);
    }
  };

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

  // What the hero reports. Order = order of importance; only "warn" changes the headline.
  const checks: StatusCheck[] = [];
  if (status) {
    if (status.module === "Loaded") checks.push({ id: "module", level: "ok", title: `GreenBoost module${status.module_version ? " v" + status.module_version : ""}` });
    else checks.push({ id: "module", level: "warn", title: "GreenBoost kernel module isn't loaded", hint: "Memory spill and the per-game extras need it.", action: { label: "Install module", onClick: onInstallModule } });
    if (layersInstalled) checks.push({ id: "layers", level: "ok", title: "Graphics layers" });
    else checks.push({ id: "layers", level: "warn", title: "GreenBoost graphics layers aren't installed", hint: "The overlay and shader tools run through them.", action: { label: "Install layers", onClick: onInstallLayers } });
    if (status.nvidia_mismatch) checks.push({ id: "reboot", level: "warn", title: "Restart to finish the driver update", hint: `The new driver (${status.nvidia_mismatch.on_disk}) is installed but not running yet.`, action: { label: "Restart now", onClick: onRebootNow } });
    else if (nvUpdate?.checked && nvUpdate.update_available) checks.push({ id: "driver", level: "warn", title: `NVIDIA driver ${nvUpdate.available_version ?? ""} is available`.replace("  ", " "), hint: "Updating needs a restart afterwards.", action: { label: "Update driver", onClick: onUpgradeNvidia } });
    else checks.push({ id: "driver", level: "ok", title: `Driver ${status.nvidia_driver}` });
    if (status.cpu_governor !== "performance") checks.push({ id: "gov", level: "note", title: `CPU in ${status.cpu_governor} mode`, hint: "Games run fastest with the CPU in performance mode. Turn on 'Performance lock' in Games → All Games." });
  }
  if (liveMetrics?.throttle_known && liveMetrics.throttle_reasons.length > 0) checks.push({ id: "throttle", level: "warn", title: "The GPU is slowing itself down", hint: (r => r.charAt(0).toUpperCase() + r.slice(1))(liveMetrics.throttle_reasons.join(", ")) + ". Lower the in-game settings, or raise the power limit in Profile." });
  if ((liveMetrics?.temp_c ?? 0) > 85) checks.push({ id: "hot", level: "warn", title: `The GPU is hot (${liveMetrics!.temp_c} °C)`, hint: "Check airflow, or raise the fan curve in Profile." });

  return (
    <div className="content-scroll">
      <div className="status-page">
        {/* Renders nothing unless something is actually out of date. */}
        <UpdateBanner compact onUpgrade={onUpgrade} />
        <StatusHero
          checks={checks}
          subtitle={[gpu?.name, status?.nvidia_driver ? `driver ${status.nvidia_driver}` : null, status?.session_type ? `${status.session_type} session` : null].filter(Boolean).join(" · ") || "Reading your system…"}
          banner={(moduleMsg || layersMsg || rebootMsg) ? <p className="status-msg" role="status">{moduleMsg || layersMsg || rebootMsg}</p> : undefined} />
        <LiveGauges m={liveMetrics} pool={poolBrief} />
        <h2 className="section-title">Your system</h2>
        <div className="section-card info-grid">
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
                      fontSize: 12, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(255,165,0,0.15)", color: "#ffb347",
                      border: "1px solid rgba(255,165,0,0.35)", whiteSpace: "nowrap",
                    }}>
                      update pending → {status.nvidia_mismatch.on_disk}
                    </span>
                  )}
                  {nvUpdate === null && (
                    <span style={{
                      fontSize: 12, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(154,154,176,0.1)", color: "var(--color-text-muted)",
                      border: "1px solid rgba(154,154,176,0.25)", whiteSpace: "nowrap",
                    }}>
                      checking for updates…
                    </span>
                  )}
                  {nvUpdate && !nvUpdate.checked && (
                    <span
                      title={`No supported package manager detected (source: ${nvUpdate.source}) , install status unknown`}
                      style={{
                        fontSize: 12, padding: "2px 7px", borderRadius: 4,
                        background: "rgba(154,154,176,0.1)", color: "var(--color-text-muted)",
                        border: "1px solid rgba(154,154,176,0.25)", whiteSpace: "nowrap",
                      }}>
                      not checked for updates
                    </span>
                  )}
                  {nvUpdate && nvUpdate.checked && !nvUpdate.update_available && (
                    <span style={{
                      fontSize: 12, padding: "2px 7px", borderRadius: 4,
                      background: "rgba(118,185,0,0.1)", color: "var(--color-gb)",
                      border: "1px solid rgba(118,185,0,0.25)", whiteSpace: "nowrap",
                    }}>
                      latest from {nvUpdate.source}
                    </span>
                  )}
                  {nvUpdate && nvUpdate.checked && nvUpdate.update_available && (
                    <button
                      onClick={onUpgradeNvidia}
                      style={{
                        fontSize: 12, padding: "2px 10px", borderRadius: 4,
                        background: "rgba(232,160,0,0.15)", color: "var(--color-amber)",
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
                    <span style={{ marginLeft: 8, fontSize: 12, color: "var(--color-text-muted)" }}>
                      v{status.module_version}
                    </span>
                  )}
                  {status.greenboost_gaming_mode && (
                    <span style={{
                      marginLeft: 8, fontSize: 12, padding: "1px 6px", borderRadius: 4,
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
                  background: "rgba(118,185,0,0.12)", color: "var(--color-gb)",
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
                  fontSize: 12, padding: "2px 7px", borderRadius: 4,
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

        {controllers.length > 0 && (
          <>
            <p className="section-title" style={{ marginTop: 24 }}>Controllers</p>
            <div className="section-card">
              {controllers.map((c, i) => (
                <div key={c.js_node} style={{ padding: "10px 0", borderTop: i === 0 ? "none" : "1px solid #1e1e1e" }}>
                  <div className="info-row">
                    <div className="info-label">{c.name}</div>
                    <div className="info-value" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      <span>{c.driver}</span>
                      <span style={{
                        fontSize: 12, padding: "2px 7px", borderRadius: 4,
                        background: "rgba(154,154,176,0.1)", color: "var(--color-text-muted)",
                        border: "1px solid rgba(154,154,176,0.25)",
                      }}>
                        {c.connection}
                      </span>
                      {c.battery_percent != null && (
                        <span style={{
                          fontSize: 12, padding: "2px 7px", borderRadius: 4,
                          background: c.battery_percent > 20 ? "rgba(118,185,0,0.1)" : "rgba(232,160,0,0.15)",
                          color: c.battery_percent > 20 ? "#76b900" : "#e8a000",
                          border: `1px solid ${c.battery_percent > 20 ? "rgba(118,185,0,0.25)" : "rgba(232,160,0,0.4)"}`,
                        }}>
                          battery {c.battery_percent}%
                        </span>
                      )}
                    </div>
                  </div>
                  {c.hidraw_readable === false && (
                    <div className="info-row">
                      <div className="info-label" />
                      <div className="info-value" style={{ fontSize: 12, color: "var(--color-amber)" }}>
                        Steam can't read this pad's raw device file, so in-game button
                        remapping through Steam Input won't work , the pad itself still
                        works normally otherwise. This is a udev rule conflict between
                        the {c.driver} driver and Steam, not a GreenBoost setting.
                      </div>
                    </div>
                  )}
                </div>
              ))}
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
                        color: "var(--color-text-muted)", fontWeight: 600, fontSize: 12,
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
                      <td style={{ padding: "8px 14px", color: "var(--color-text-dim)" }}>
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
                          <span style={{ color: "var(--color-gb)", fontWeight: 700 }}>✓</span>
                        ) : (
                          <span style={{ color: "var(--color-text-error)", fontWeight: 700 }}>✗ ({s.rc})</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 14px", color: "var(--color-text-muted)" }}>
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
              <div style={{ fontSize: 12, color: "var(--color-text-dim)", lineHeight: 1.6, marginBottom: 10 }}>
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
              <div style={{ fontSize: 12, color: "var(--color-text-dim)", lineHeight: 1.6, marginBottom: 10 }}>
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
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
                Vulkan implicit layer + OpenGL LD_PRELOAD interposer for T2/T3 memory overflow
              </div>
              {/* Sub-rows: individual status for each layer */}
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                  <span style={{ color: "var(--color-text-muted)", minWidth: 90 }}>Vulkan layer</span>
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
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                  <span style={{ color: "var(--color-text-muted)", minWidth: 90 }}>OpenGL layer</span>
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
                        style={{ borderColor: "var(--color-red)", color: "var(--color-text-error)" }}
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
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
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
                        style={{ borderColor: "var(--color-red)", color: "var(--color-text-error)" }}
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
            <p className="component-msg" style={{ color: "var(--color-amber)" }}>
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
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
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
          <p style={{ fontSize: 13, color: "var(--color-text-dim)", lineHeight: 1.7, margin: 0 }}>
            GreenBoost Gaming Suite optimizes your Linux gaming experience through hardware-aware
            configuration tuning, RTX technology enablement, and Greenboost Proton integration.

          </p>
        </div>
      </div>
    </div>
  );
}
