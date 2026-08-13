import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { CachedDll } from "../types";

const DISCLAIMER = "GreenBoost is an independent open-source project and is not affiliated with, endorsed by, or sponsored by NVIDIA Corporation. NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.";

const DLL_META: Record<string, { label: string; type: string; repo: string }> = {
  "nvngx_dlss.dll":   { label: "nvngx_dlss.dll",   type: "DLSS Super Resolution",   repo: "NVIDIA/DLSS" },
  "nvngx_dlssg.dll":  { label: "nvngx_dlssg.dll",  type: "DLSS Frame Generation",   repo: "NVIDIA/DLSS" },
  "nvngx_dlssd.dll":  { label: "nvngx_dlssd.dll",  type: "DLSS Ray Reconstruction", repo: "NVIDIA/DLSS" },
  "sl.dlss.dll":      { label: "sl.dlss.dll",      type: "Streamline DLSS SR",      repo: "NVIDIAGameWorks/Streamline" },
  "sl.dlss_g.dll":    { label: "sl.dlss_g.dll",    type: "Streamline DLSS FG",      repo: "NVIDIAGameWorks/Streamline" },
  "sl.dlss_d.dll":    { label: "sl.dlss_d.dll",    type: "Streamline DLSS RR",      repo: "NVIDIAGameWorks/Streamline" },
  "sl.reflex.dll":    { label: "sl.reflex.dll",    type: "Streamline Reflex",       repo: "NVIDIAGameWorks/Streamline" },
  "sl.common.dll":    { label: "sl.common.dll",    type: "Streamline Core",         repo: "NVIDIAGameWorks/Streamline" },
  "sl.interposer.dll":{ label: "sl.interposer.dll",type: "Streamline Interposer",   repo: "NVIDIAGameWorks/Streamline" },
  "sl.nis.dll":       { label: "sl.nis.dll",       type: "Streamline NIS",          repo: "NVIDIAGameWorks/Streamline" },
  "sl.pcl.dll":       { label: "sl.pcl.dll",       type: "Streamline PCL",          repo: "NVIDIAGameWorks/Streamline" },
};

const DLL_ORDER = [
  "nvngx_dlss.dll",
  "nvngx_dlssg.dll",
  "nvngx_dlssd.dll",
  "sl.dlss.dll",
  "sl.dlss_g.dll",
  "sl.dlss_d.dll",
  "sl.reflex.dll",
  "sl.common.dll",
  "sl.interposer.dll",
  "sl.nis.dll",
  "sl.pcl.dll",
];

export function AboutView() {
  const [perfMode, setPerfMode] = useState(false);
  const [perfMsg, setPerfMsg]   = useState<string | null>(null);
  const [tab, setTab] = useState<"about" | "preferences">("about");

  const [cachedDlls, setCachedDlls] = useState<CachedDll[]>([]);
  const [dlssLoading, setDlssLoading] = useState(false);
  const [dlssMsg, setDlssMsg] = useState<string | null>(null);

  type DlssTag = { tag: string; name: string; date: string };
  type DlssVersionData = {
    nvngx: DlssTag[];
    streamline: DlssTag[];
    nvngx_pinned: string;
    streamline_pinned: string;
  };
  const [dlssVersions, setDlssVersions] = useState<DlssVersionData | null>(null);
  const [dlssVerLoading, setDlssVerLoading] = useState(false);
  const [dlssVerMsg, setDlssVerMsg] = useState<string | null>(null);
  const [pinNvngx, setPinNvngx] = useState("");
  const [pinStreamline, setPinStreamline] = useState("");

  const loadDlls = useCallback(async () => {
    setDlssLoading(true);
    try {
      const dlls: CachedDll[] = await invoke("list_cached_dlls");
      setCachedDlls(dlls);
    } catch (e: any) {
      setDlssMsg("Could not read DLL cache: " + (e?.message ?? e));
    }
    setDlssLoading(false);
  }, []);

  useEffect(() => {
    invoke<{ perf_mode: boolean }>("get_global_settings")
      .then(s => setPerfMode(s.perf_mode))
      .catch(() => {
        invoke<{ cpu_governor: string }>("get_status")
          .then(st => setPerfMode(st.cpu_governor === "performance"))
          .catch(() => {});
      });
    loadDlls();
  }, [loadDlls]);

  const togglePerf = async () => {
    const next = !perfMode;
    try {
      const m: string = await invoke("set_perf_mode", { enabled: next });
      setPerfMode(next);
      setPerfMsg(m);
      const settings = await invoke<Record<string, unknown>>("get_global_settings");
      await invoke("save_global_settings", { settings: { ...settings, perf_mode: next } });
    } catch (e: any) { setPerfMsg(e?.message ?? "Failed (sudo required)"); }
  };

  const fetchDlssVersions = async () => {
    setDlssVerLoading(true);
    setDlssVerMsg(null);
    try {
      const data = await invoke<DlssVersionData>("list_dlss_versions");
      setDlssVersions(data);
      setPinNvngx(data.nvngx_pinned ?? "");
      setPinStreamline(data.streamline_pinned ?? "");
    } catch (e: any) {
      setDlssVerMsg("Failed to fetch versions: " + (e?.message ?? String(e)));
    }
    setDlssVerLoading(false);
  };

  const applyDlssPins = async () => {
    setDlssVerMsg(null);
    try {
      await invoke("set_dlss_pinned_tags", { nvngxTag: pinNvngx, streamlineTag: pinStreamline });
      setDlssVerMsg(
        (pinNvngx || pinStreamline)
          ? "Pinned versions saved. Next DLSS sync will use these tags."
          : "Pins cleared , next sync will use the latest releases."
      );
    } catch (e: any) {
      setDlssVerMsg("Failed to save pins: " + (e?.message ?? String(e)));
    }
  };

  const dllByName = (name: string): CachedDll | undefined =>
    cachedDlls.find(d => d.name.toLowerCase() === name.toLowerCase());

  return (
    <>
      <div className="sub-nav">
        <button className={`sub-nav-tab${tab === "about" ? " active" : ""}`}
                onClick={() => setTab("about")}>About</button>
        <button className={`sub-nav-tab${tab === "preferences" ? " active" : ""}`}
                onClick={() => setTab("preferences")}>Preferences</button>
      </div>
      <div className="content-scroll">
        <div style={{ maxWidth: 760, margin: "0 auto" }}>

          {tab === "about" && (
            <>
              <p className="section-title">About GreenBoost Gaming Suite</p>
              <div className="section-card">
                <p style={{ fontSize: 13, color: "#e6e6e6", lineHeight: 1.7, margin: "0 0 6px" }}>
                  Version 26.04.26 , GreenBoost Gaming Suite
                </p>
                <p style={{ fontSize: 13, color: "#9a9a9a", lineHeight: 1.7, margin: "0 0 12px" }}>
                  Copyright 2026 Ferran Duarri.
                  Released under the GNU General Public License v2.
                </p>
                <p className="disclaimer">{DISCLAIMER}</p>
                <div style={{ marginTop: 16 }}>
                  <a
                    href="https://github.com/sponsors/Hyphaed"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <img
                      src="https://img.shields.io/badge/Sponsor_this_project-EA4AAA?style=for-the-badge&logo=github-sponsors&logoColor=white"
                      alt="Sponsor this project"
                      style={{ height: 28 }}
                    />
                  </a>
                </div>
              </div>

              <p className="section-title" style={{ marginTop: 24 }}>
                DLSS DLL provenance
              </p>
              <div className="section-card">
                <p style={{ fontSize: 13, color: "#9a9a9a", lineHeight: 1.7, margin: 0 }}>
                  Streamline DLLs (<code>sl.*.dll</code>) are downloaded from{" "}
                  <b>NVIDIAGameWorks/Streamline</b> on GitHub.
                  DLSS DLLs (<code>nvngx_*.dll</code>) are downloaded from{" "}
                  <b>NVIDIA/DLSS</b> on GitHub.
                  <br /><br />
                  All downloads are verified against NVIDIA's official release
                  manifests. Full chain-of-custody is documented in{" "}
                  <code>DLSS_UPDATER.md</code> shipped with this app.
                </p>
              </div>
            </>
          )}

          {tab === "preferences" && (
            <>
              <p className="section-title">Performance</p>
              <div className="section-card">
                <div className="toggle-row" onClick={togglePerf}
                     style={{ cursor: "pointer" }}>
                  <div>
                    <div className="toggle-label">Performance Mode</div>
                    <div className="toggle-desc">
                      Sets CPU governor to performance and GPU power mode
                      to maximum
                    </div>
                  </div>
                  <div className={`toggle${perfMode ? " on" : ""}`}>
                    <div className="toggle-track" />
                    <div className="toggle-thumb" />
                  </div>
                </div>
                {perfMsg && (
                  <p style={{ fontSize: 11, color: "#8a9ab0", margin: "10px 0 0" }}>
                    {perfMsg}
                  </p>
                )}
              </div>

              <p className="section-title" style={{ marginTop: 24 }}>
                DLSS Libraries
              </p>

              <div className="section-card" style={{ padding: 0, overflow: "hidden" }}>
                {dlssMsg && (
                  <p style={{ fontSize: 12, color: "#e05252",
                               padding: "10px 16px", margin: 0 }}>
                    {dlssMsg}
                  </p>
                )}

                <table style={{
                  width: "100%", borderCollapse: "collapse",
                  fontSize: 12, color: "#e6e6e6",
                }}>
                  <thead>
                    <tr style={{ background: "#141414" }}>
                      <th style={thStyle}>Library</th>
                      <th style={thStyle}>Type</th>
                      <th style={thStyle}>Source</th>
                      <th style={{ ...thStyle, textAlign: "right" }}>Version</th>
                    </tr>
                  </thead>
                  <tbody>
                    {DLL_ORDER.map((name, i) => {
                      const meta = DLL_META[name];
                      const dll  = dllByName(name);
                      return (
                        <tr key={name}
                            style={{ borderTop: i > 0 ? "1px solid #222" : undefined }}>
                          <td style={tdStyle}>
                            <code style={{ fontSize: 11 }}>{meta.label}</code>
                          </td>
                          <td style={{ ...tdStyle, color: "#9a9a9a" }}>
                            {meta.type}
                          </td>
                          <td style={{ ...tdStyle, color: "#5a7a9a", fontSize: 11 }}>
                            {meta.repo}
                          </td>
                          <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                            {dlssLoading
                              ? <span style={{ color: "#8a9ab0" }}>…</span>
                              : dll
                                ? <span style={{ color: "#76b900" }}>{dll.version}</span>
                                : <span style={{ color: "#8a9ab0" }}>not cached</span>
                            }
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>

                <div style={{
                  padding: "10px 16px",
                  borderTop: "1px solid #1e1e1e",
                  fontSize: 11, color: "#8a9ab0",
                }}>
                  Versions shown are from the local cache (
                  <code>~/.local/share/greenboost-gaming/libraries/</code>).
                  Use <b>Update DLSS</b> in Game Settings to fetch newest releases
                  from NVIDIA's official GitHub repositories.
                </div>
              </div>

              <p className="section-title" style={{ marginTop: 24 }}>
                DLSS Version Pinning
              </p>
              <div className="section-card">
                <p style={{ fontSize: 12, color: "#8a9ab0", margin: "0 0 14px" }}>
                  Pin a specific release tag for DLSS or Streamline DLLs.
                  Leave blank to always use the latest release when syncing.
                </p>

                {!dlssVersions ? (
                  <button
                    className="btn-primary"
                    onClick={fetchDlssVersions}
                    disabled={dlssVerLoading}
                    style={{ fontSize: 12 }}
                  >
                    {dlssVerLoading ? "Fetching…" : "Fetch Available Versions"}
                  </button>
                ) : (
                  <>
                    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ fontSize: 12, color: "#9a9ab0", minWidth: 140, flexShrink: 0 }}>
                          DLSS <span style={{ color: "#5a7a9a", fontSize: 11 }}>nvngx_*.dll</span>
                        </span>
                        <select
                          value={pinNvngx}
                          onChange={e => setPinNvngx(e.target.value)}
                          className="setting-select"
                          style={{ fontSize: 12, flex: 1 }}
                        >
                          <option value="">Latest</option>
                          {dlssVersions.nvngx.map(t => (
                            <option key={t.tag} value={t.tag}>{t.tag}</option>
                          ))}
                          {dlssVersions.nvngx.length === 0 && (
                            <option disabled>No tags fetched</option>
                          )}
                        </select>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ fontSize: 12, color: "#9a9ab0", minWidth: 140, flexShrink: 0 }}>
                          Streamline <span style={{ color: "#5a7a9a", fontSize: 11 }}>sl.*.dll</span>
                        </span>
                        <select
                          value={pinStreamline}
                          onChange={e => setPinStreamline(e.target.value)}
                          className="setting-select"
                          style={{ fontSize: 12, flex: 1 }}
                        >
                          <option value="">Latest</option>
                          {dlssVersions.streamline.map(t => (
                            <option key={t.tag} value={t.tag}>
                              {t.tag}{t.date ? ` , ${t.date}` : ""}
                            </option>
                          ))}
                          {dlssVersions.streamline.length === 0 && (
                            <option disabled>No releases fetched</option>
                          )}
                        </select>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <button
                        className="btn-primary"
                        onClick={applyDlssPins}
                        style={{ fontSize: 12 }}
                      >
                        Apply
                      </button>
                      <button
                        className="btn-secondary"
                        onClick={fetchDlssVersions}
                        disabled={dlssVerLoading}
                        style={{ fontSize: 12 }}
                      >
                        {dlssVerLoading ? "…" : "Refresh"}
                      </button>
                    </div>
                  </>
                )}

                {dlssVerMsg && (
                  <p style={{ fontSize: 11, color: "#8a9ab0", margin: "10px 0 0" }}>
                    {dlssVerMsg}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </div>

    </>
  );
}

const thStyle: React.CSSProperties = {
  padding: "8px 16px",
  textAlign: "left",
  fontWeight: 600,
  fontSize: 11,
  color: "#8a9ab0",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

const tdStyle: React.CSSProperties = {
  padding: "10px 16px",
  verticalAlign: "middle",
};
