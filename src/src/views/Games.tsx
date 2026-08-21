import { useState, useEffect, useCallback, useRef, Children } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Game, SettingGroup, DlssSourceState, InstallStreamProps, GameOverrides, GameNisConfig, GameWrappers, DlssStatus, GlobalSettingsState, DirectStorageInfo, AutoTuneResult, LaunchStatus } from "../types";
import { Icon } from "../icons";
import { GameThumb } from "../components/GameThumb";
import { GameHeroBanner } from "../components/GameHeroBanner";
import { CollapsibleSection } from "../components/CollapsibleSection";
import { InstallStreamModal } from "../components/InstallStreamModal";
import { DllPicker } from "../components/DllPicker";
import { InfoTip } from "../components/InfoTip";
import { DLL_ORDER, DLL_TYPE, DLL_EXPLAIN } from "../dllInfo";
import { GS_INFO, GS_BENEFIT, GS_ADDED_BY_GB } from "../gsHelp";
import { OPTIMAL_OVERRIDES, diffFromOptimal, optimalPatch, countActiveOverrides } from "../gameOptimal";
import { GB_DETAIL, GB_AUTOMATIC } from "../gbFeatures";
import { ChangeSummaryModal } from "../components/ChangeSummaryModal";
import { summarizeGlobal, summarizeGame, type Change } from "../changeSummary";
import { globalSettings, useGlobalSettings, loadGlobalSettings,
         patchGlobalSettings, replaceGlobalSettings } from "../store/globalSettings";

const menuItemStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 8,
  width: "100%", padding: "10px 12px",
  background: "transparent", border: "none",
  color: "#e6e6e6", fontSize: 13, cursor: "pointer",
  textAlign: "left",
};
const menuItemHintStyle: React.CSSProperties = {
  marginLeft: "auto", color: "#8a9ab0", fontSize: 11,
};

/// Small clickable chip shown next to a GREENBOOST OVERRIDES row whose
/// current value differs from OPTIMAL_OVERRIDES (gameOptimal.ts) ,
/// applies just that one field without touching the rest.
function RecommendedChip({ label, onClick, disabled }: {
  label: string; onClick: () => void; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title="Apply the recommended value for this setting"
      style={{
        fontSize: 10, padding: "2px 7px", borderRadius: 4, cursor: "pointer",
        background: "rgba(118,185,0,0.10)", border: "1px solid rgba(118,185,0,0.35)",
        color: "#9fd45a", whiteSpace: "nowrap",
      }}
    >
      Recommended: {label}
    </button>
  );
}

/// Short display label for a raw dlss_preset value , mirrors the option
/// text in the DLSS Preset <select> below, for resolving "Use Global"/
/// OPTIMAL_OVERRIDES recommendations to something readable.
function dlssPresetLabel(v: string): string {
  switch (v) {
    case "render_preset_latest": return "Latest / Recommended";
    case "render_preset_m":      return "Preset M (RTX 40/50)";
    case "render_preset_k":      return "Preset K (RTX 20/30)";
    case "render_preset_l":      return "Preset L (Max sharpness)";
    case "default":              return "Default (game decides)";
    case "off":                  return "Off";
    default:                     return v || "Use Global";
  }
}

interface GameAnalytics {
  appid:              string;
  game_name:          string;
  session_count:      number;
  avg_vram_mb:        number;
  peak_vram_mb:       number;
  /** True when peak/avg VRAM above may include other GPU processes (a
   * sampled session fell back to whole-GPU NVML accounting). */
  vram_includes_other_apps: boolean;
  avg_duration_min:   number;
  total_play_hours:   number;
  gpu_total_vram_mb:  number;
  /** Max T2 DDR spill (MB) seen across recorded sessions; 0 if never spilled. */
  peak_t2_mb:         number;
}

interface DlssPresetChoice { id: string; label: string; }
interface CachedDllInfo { name: string; version: string; source: string; fetched_at: number; size_bytes: number; sha256: string; path: string; }

type DlssTag = { tag: string; name: string; date: string };
type DlssVersionData = {
  nvngx: DlssTag[];
  streamline: DlssTag[];
  nvngx_pinned: string;
  streamline_pinned: string;
};

function DlssLibrarySection() {
  const [dlls, setDlls] = useState<CachedDllInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [modal, setModal] = useState<InstallStreamProps | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // Version pinning , which release tag "Fetch Latest" actually pulls. This
  // used to live in About, which made it look like a credits/provenance note
  // instead of the control over the sync button directly above it.
  const [versions, setVersions] = useState<DlssVersionData | null>(null);
  const [verLoading, setVerLoading] = useState(false);
  const [verMsg, setVerMsg] = useState<string | null>(null);
  const [pinNvngx, setPinNvngx] = useState("");
  const [pinStreamline, setPinStreamline] = useState("");

  const reload = useCallback(async () => {
    try {
      const data: CachedDllInfo[] = await invoke("list_cached_dlls");
      setDlls(data);
    } catch (e: any) {
      setMsg("Cache read failed: " + (e?.message ?? e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  // list_cached_dlls() sorts newest-fetched-first, so index 0 here is
  // always "the latest cached version" for a given name.
  const dllVersionsByName = (n: string) => dlls.filter(d => d.name.toLowerCase() === n.toLowerCase());

  const fetchLatest = () => {
    setMsg(null);
    setLoading(true);
    setModal({
      title: "Fetch Latest DLSS Libraries from NVIDIA GitHub",
      command: "sync_dlss_library_streaming",
      onDone: (ok) => {
        setModal(null);
        setLoading(false);
        if (ok) { reload(); setMsg("DLSS libraries updated."); }
        else setMsg("Sync finished with errors , see log above.");
      },
    });
  };

  // Hits GitHub, so it stays an explicit action rather than firing on mount.
  const fetchVersions = async () => {
    setVerLoading(true);
    setVerMsg(null);
    try {
      const data = await invoke<DlssVersionData>("list_dlss_versions");
      setVersions(data);
      setPinNvngx(data.nvngx_pinned ?? "");
      setPinStreamline(data.streamline_pinned ?? "");
    } catch (e: any) {
      setVerMsg("Failed to fetch versions: " + (e?.message ?? String(e)));
    }
    setVerLoading(false);
  };

  const applyPins = async () => {
    setVerMsg(null);
    try {
      await invoke("set_dlss_pinned_tags", { nvngxTag: pinNvngx, streamlineTag: pinStreamline });
      setVerMsg(
        (pinNvngx || pinStreamline)
          ? "Pinned. Fetch Latest will use these tags instead of the newest release."
          : "Pins cleared , Fetch Latest will use the newest release."
      );
    } catch (e: any) {
      setVerMsg("Failed to save pins: " + (e?.message ?? String(e)));
    }
  };

  return (
    <>
      <CollapsibleSection title="DLSS LIBRARIES" defaultOpen={false}>
        <div style={{ padding: "0 16px 8px", display: "flex", alignItems: "center",
                      justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.4 }}>
            Source: <span style={{ color: "#76b900", fontWeight: 600 }}>NVIDIA GitHub (official)</span>
            <span style={{ marginLeft: 8, fontSize: 11, color: "#8a9ab0" }}>
              NVIDIA/DLSS · NVIDIAGameWorks/Streamline
            </span>
          </div>
          <button className="btn-component" onClick={fetchLatest} disabled={loading}
                  style={{ whiteSpace: "nowrap", fontSize: 12 }}>
            Fetch Latest
          </button>
        </div>
        {msg && <p style={{ fontSize: 12, color: "#76b900", margin: "0 16px 8px" }}>{msg}</p>}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#e6e6e6" }}>
          <thead>
            <tr style={{ background: "#141414" }}>
              <th style={{ padding: "6px 16px", textAlign: "left", fontWeight: 600, fontSize: 11,
                           color: "#9a9ab0", textTransform: "uppercase", letterSpacing: "0.04em" }}>Library</th>
              <th style={{ padding: "6px 16px", textAlign: "left", fontWeight: 600, fontSize: 11,
                           color: "#9a9ab0", textTransform: "uppercase", letterSpacing: "0.04em" }}>Type</th>
              <th style={{ padding: "6px 16px", textAlign: "right", fontWeight: 600, fontSize: 11,
                           color: "#9a9ab0", textTransform: "uppercase", letterSpacing: "0.04em" }}>Cached Version</th>
            </tr>
          </thead>
          <tbody>
            {DLL_ORDER.map((name, i) => {
              const versions = dllVersionsByName(name);
              const d = versions[0];
              return (
                <tr key={name} style={{ borderTop: i > 0 ? "1px solid #1e1e1e" : undefined }}>
                  <td style={{ padding: "8px 16px" }}>
                    <code style={{ fontSize: 11 }}>{name}</code>
                    {DLL_EXPLAIN[name] && <InfoTip>{DLL_EXPLAIN[name]}</InfoTip>}
                  </td>
                  <td style={{ padding: "8px 16px", color: "#9a9a9a" }}>{DLL_TYPE[name]}</td>
                  <td style={{ padding: "8px 16px", textAlign: "right", fontFamily: "monospace" }}>
                    {d ? (
                      <>
                        <span style={{ color: "#76b900" }}>{d.version}</span>
                        {versions.length > 1 && (
                          <span style={{ color: "#8a9ab0", fontFamily: "inherit", marginLeft: 6 }}>
                            (+{versions.length - 1} older cached , pick a version per-game)
                          </span>
                        )}
                      </>
                    ) : (
                      <span style={{ color: "#8a9ab0" }}>not cached</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {/* Version pinning , directly under the table it governs, because it
            decides which release "Fetch Latest" above actually pulls. */}
        <div style={{ padding: "12px 16px", borderTop: "1px solid #1e1e1e" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                        gap: 12, marginBottom: versions ? 12 : 0 }}>
            <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.4 }}>
              <span style={{ color: "#e6e6e6", fontWeight: 600 }}>Version pinning</span>
              <span style={{ marginLeft: 8, fontSize: 11, color: "#8a9ab0" }}>
                Stay on a specific release instead of always taking the newest.
              </span>
            </div>
            {!versions && (
              <button className="btn-component" onClick={fetchVersions} disabled={verLoading}
                      style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                {verLoading ? "Fetching…" : "Show Versions"}
              </button>
            )}
          </div>

          {versions && (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ fontSize: 12, color: "#9a9ab0", minWidth: 150, flexShrink: 0 }}>
                    DLSS <code style={{ fontSize: 11, color: "#5a7a9a" }}>nvngx_*.dll</code>
                  </span>
                  <select className="gb-select" style={{ flex: 1, fontSize: 12 }}
                          value={pinNvngx} onChange={e => setPinNvngx(e.target.value)}>
                    <option value="">Latest (no pin)</option>
                    {versions.nvngx.map(t => (
                      <option key={t.tag} value={t.tag}>{t.tag}{t.date ? ` , ${t.date}` : ""}</option>
                    ))}
                  </select>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ fontSize: 12, color: "#9a9ab0", minWidth: 150, flexShrink: 0 }}>
                    Streamline <code style={{ fontSize: 11, color: "#5a7a9a" }}>sl.*.dll</code>
                  </span>
                  <select className="gb-select" style={{ flex: 1, fontSize: 12 }}
                          value={pinStreamline} onChange={e => setPinStreamline(e.target.value)}>
                    <option value="">Latest (no pin)</option>
                    {versions.streamline.map(t => (
                      <option key={t.tag} value={t.tag}>{t.tag}{t.date ? ` , ${t.date}` : ""}</option>
                    ))}
                  </select>
                </div>
                {versions.nvngx.length === 0 && versions.streamline.length === 0 && (
                  <div style={{ fontSize: 11, color: "#8a9ab0" }}>
                    No release tags came back , GitHub may be unreachable or rate-limiting.
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button className="btn-component" onClick={applyPins} style={{ fontSize: 12 }}>
                  Apply Pins
                </button>
                <button className="btn-component" onClick={fetchVersions} disabled={verLoading}
                        style={{ fontSize: 12, borderColor: "#2e2e2e",
                                 background: "transparent", color: "#9a9ab0" }}>
                  {verLoading ? "…" : "Refresh"}
                </button>
              </div>
            </>
          )}

          {verMsg && (
            <p style={{ fontSize: 11, color: "#8a9ab0", margin: "10px 0 0" }}>{verMsg}</p>
          )}
        </div>

        <div style={{ padding: "8px 16px", borderTop: "1px solid #1e1e1e", fontSize: 11, color: "#8a9ab0" }}>
          Versions from <code>~/.local/share/greenboost-gaming/libraries/</code>. Click <b>Fetch Latest</b> to sync from NVIDIA GitHub.
        </div>
      </CollapsibleSection>
      {modal && <InstallStreamModal {...modal} />}
    </>
  );
}

function GlobalSettingsPanel() {
  // One shared copy for the whole app , see store/globalSettings.ts. This
  // panel used to hold its own, which is why About/Profile/the launch
  // handler could all disagree with it.
  const state = useGlobalSettings();
  const [presets, setPresets] = useState<DlssPresetChoice[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [gsProfiles, setGsProfiles] = useState<string[]>([]);
  const [selectedGsProfile, setSelectedGsProfile] = useState<string>("");
  const [gsProfileInput, setGsProfileInput] = useState<string>("");
  const [gsProfileMsg, setGsProfileMsg] = useState<string | null>(null);
  // Settings search + the "GreenBoost extras only" filter. These replace
  // the old "Added by GreenBoost" tab: rather than a second copy of every
  // toggle, the curated list is a view over the one real list.
  const [query, setQuery] = useState("");
  const [gbOnly, setGbOnly] = useState(false);
  const [summary, setSummary] = useState<{
    title: string; subtitle?: string; changes: Change[];
    notes?: string[]; unchanged?: string;
  } | null>(null);
  const q = query.trim().toLowerCase();
  const filtering = q.length > 0 || gbOnly;

  const reload = useCallback(() => { loadGlobalSettings(true); }, []);

  const reloadGsProfiles = useCallback(() => {
    invoke<string[]>("list_gs_profiles").then(setGsProfiles).catch(console.error);
  }, []);

  useEffect(() => {
    reload();
    reloadGsProfiles();
    invoke<DlssPresetChoice[]>("list_dlss_preset_choices")
      .then(setPresets).catch(console.error);
  }, [reload, reloadGsProfiles]);

  // Populated once , the answer only changes when the user installs a Proton.
  const [protonInstalls, setProtonInstalls] = useState<[string, string][]>([]);
  useEffect(() => {
    invoke<[string, string][]>("list_proton_installs")
      .then(setProtonInstalls)
      .catch(() => setProtonInstalls([]));
  }, []);

  const update = useCallback(async (patch: Partial<GlobalSettingsState>) => {
    try {
      await patchGlobalSettings(patch);
      setMsg("Saved.");
    } catch (e: any) {
      setMsg(`Save failed: ${e?.message ?? e}`);
    }
  }, []);

  const handleGsProfileSave = async () => {
    const name = gsProfileInput.trim();
    if (!name || !state) return;
    try {
      await invoke("save_gs_profile", { name, settings: state });
      setGsProfileMsg(`Saved as "${name}".`);
      setGsProfileInput("");
      reloadGsProfiles();
      setSelectedGsProfile(name);
    } catch (e: any) {
      setGsProfileMsg("Save failed: " + (e?.message ?? e));
    }
  };

  const handleGsProfileLoad = async () => {
    if (!selectedGsProfile) return;
    try {
      const loaded = await invoke<GlobalSettingsState | null>("load_gs_profile", { name: selectedGsProfile });
      if (!loaded) { setGsProfileMsg(`Profile "${selectedGsProfile}" not found.`); return; }
      await replaceGlobalSettings(loaded);
      setGsProfileMsg(`Loaded "${selectedGsProfile}".`);
    } catch (e: any) {
      setGsProfileMsg("Load failed: " + (e?.message ?? e));
    }
  };

  const handleGsProfileDelete = async () => {
    if (!selectedGsProfile) return;
    try {
      await invoke("delete_gs_profile", { name: selectedGsProfile });
      setGsProfileMsg(`Deleted "${selectedGsProfile}".`);
      setSelectedGsProfile("");
      reloadGsProfiles();
    } catch (e: any) {
      setGsProfileMsg("Delete failed: " + (e?.message ?? e));
    }
  };

  if (!state) {
    return (
      <div className="content-scroll">
        <p style={{ color: "#9a9a9a", padding: 24 }}>Loading global settings…</p>
      </div>
    );
  }

  const row = (
    label: string, sub: string | null, control: React.ReactNode,
    benefit?: string, locked?: string, info?: React.ReactNode,
  ) => {
    const gbSection = GS_ADDED_BY_GB[label];
    if (gbOnly && !gbSection) return null;
    if (q) {
      // Search the prose too, not just the label , someone typing
      // "stutter" or "VRAM" is describing a symptom, and the words that
      // match it live in the description and the deep-dive, not the name.
      const d = GB_DETAIL[label];
      const hay = [label, sub ?? "", benefit ?? "", gbSection ?? "",
                   d ? `${d.what} ${d.why} ${d.verify}` : ""]
        .join(" ").toLowerCase();
      if (!hay.includes(q)) return null;
    }

    const detail = GB_DETAIL[label];
    const tip = (info || detail) ? (
      <>
        {info}
        {detail && (
          <div style={{ marginTop: info ? 10 : 0 }}>
            {([["What it does", detail.what, "#d0d0d0"],
               ["Why this is a Linux/NVIDIA gap", detail.why, "#d0d0d0"],
               ["How to see it yourself", detail.verify, "#a5b4fc"]] as const)
              .filter(([, body]) => body)
              .map(([h, body, color]) => (
                <div key={h} style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 10, color: "#8a9ab0", textTransform: "uppercase",
                                letterSpacing: "0.04em", marginBottom: 2 }}>{h}</div>
                  <div style={{ color }}>{body}</div>
                </div>
              ))}
          </div>
        )}
      </>
    ) : null;

    return (
    <div className="gs-row" key={label} style={locked ? { opacity: 0.5 } : undefined}>
      <div className="gs-row-label">
        <div className="gs-row-title">
          {label}
          {tip && <InfoTip>{tip}</InfoTip>}
          {/* Badge is driven entirely by the label, so call sites don't
              have to opt in , see GS_ADDED_BY_GB for what qualifies. */}
          {gbSection && (
            <span
              onClick={() => setGbOnly(true)}
              title={"Added by GreenBoost , this does not exist in a stock "
                   + "Linux/NVIDIA install. Click to show only GreenBoost "
                   + "extras. Open (i) for the full explanation."}
              style={{
                cursor: "pointer",
                marginLeft: 6, padding: "1px 5px",
                fontSize: 10, fontWeight: 600, lineHeight: 1.6,
                color: "#76b900",
                background: "rgba(118,185,0,0.12)",
                border: "1px solid rgba(118,185,0,0.35)",
                borderRadius: 4,
                verticalAlign: "middle", whiteSpace: "nowrap",
              }}>
              GreenBoost
            </span>
          )}
        </div>
        {benefit && (
          <div style={{ fontSize: 11, color: "#76b900", fontWeight: 600, margin: "2px 0" }}>
            {benefit}
          </div>
        )}
        {sub && <div className="gs-row-sub">{sub}</div>}
        {locked && (
          <div style={{ fontSize: 11, color: "#e8a000", marginTop: 3 }}>
            {locked}
          </div>
        )}
      </div>
      <div className="gs-row-control">{control}</div>
    </div>
    );
  };

  // Drops itself when everything inside was filtered out, so a search never
  // leaves a trail of empty section headers. React.Children.toArray already
  // discards the nulls row() returns for non-matches.
  const FilterSection = ({ title, defaultOpen, note, children }: {
    title: string; defaultOpen?: boolean;
    /** Section preamble. A prop rather than a child on purpose: as a child
     *  it counted as a match, so a search that hit nothing in this section
     *  still rendered it, claiming "1 match" and showing only the blurb. */
    note?: string;
    children: React.ReactNode;
  }) => {
    const kids = Children.toArray(children).filter(Boolean);
    if (kids.length === 0) return null;
    return (
      <CollapsibleSection
        title={title}
        defaultOpen={defaultOpen}
        forceOpen={filtering ? true : undefined}
        subtitle={filtering ? `${kids.length} match${kids.length === 1 ? "" : "es"}` : undefined}
      >
        {note && !filtering && (
          <div style={{ padding: "0 16px 10px", fontSize: 11, color: "#8a9ab0" }}>{note}</div>
        )}
        {kids}
      </CollapsibleSection>
    );
  };

  const toggle = (on: boolean, onClick: () => void) => (
    <div onClick={onClick}
         role="switch" aria-checked={on}
         tabIndex={0}
         style={{
           display: "inline-flex", alignItems: "center",
           width: 44, height: 22, padding: 2,
           background: on ? "#76b900" : "#3a3a3a",
           borderRadius: 999, cursor: "pointer",
           transition: "background 120ms ease",
         }}>
      <div style={{
        width: 18, height: 18, borderRadius: 999,
        background: "#ffffff",
        transform: `translateX(${on ? 22 : 0}px)`,
        transition: "transform 120ms ease",
      }} />
    </div>
  );

  return (
    <div className="content-scroll">
      <div style={{ padding: "12px 0 14px", borderBottom: "1px solid #1e1e1e" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button
            className="btn-optimize"
            onClick={async () => {
              setMsg(null);
              try {
                const tune: AutoTuneResult = await invoke("gpu_auto_tune");
                // Everything card-specific has to come FROM the tune result.
                // This used to apply a fixed patch identical on every GPU ,
                // including a hardcoded dlss_preset , while discarding
                // tune.recommended_shader_threads entirely, so a button
                // advertised as "picks the right settings for your specific
                // graphics card" was card-aware only in the GPU clock/fan
                // half that gpu_auto_tune applies internally.
                const patch: Partial<GlobalSettingsState> = {
                  // "auto" resolves per-GPU-series (RTX 20/30 -> K,
                  // 40/50 -> M) instead of pinning one preset for all cards.
                  dlss_preset: "auto",
                  gplasync: true,
                  perf_lock: true,
                  compositor_suspend: true,
                  ddr_prewarm: true,
                  memlock_unlimited: true,
                  vk_pipeline_cache: true,
                  vk_queue_priority: true,
                  vk_memory_priority: true,
                  gl_layer_enabled: true,
                  ...(tune.recommended_shader_threads > 0
                      ? { shader_threads: tune.recommended_shader_threads }
                      : {}),
                  // Small L3 caches thrash a large shader cache , the tune
                  // already flags this in notes, so act on it too.
                  ...(tune.l3_cache_kb > 0 && tune.l3_cache_kb / 1024 < 8
                      ? { shader_cache_gb: 4 }
                      : {}),
                };
                const before = globalSettings.get();
                await patchGlobalSettings(patch);
                setSummary({
                  title: "Smart Defaults applied",
                  subtitle: `Tuned for ${tune.label} , ${tune.physical_cores}P/`
                          + `${tune.logical_cores}L cores`,
                  changes: summarizeGlobal(before, patch),
                  notes: tune.notes,
                  unchanged: "Every setting was already at the value recommended "
                           + "for this hardware , nothing needed changing.",
                });
              } catch (e: any) {
                setMsg(`Smart Defaults failed: ${e?.message ?? e}`);
              }
            }}
          >
            Smart Defaults
          </button>
          <span style={{ fontSize: 11, color: "#e6e6e6" }}>
            Picks the right settings automatically for your specific graphics card
          </span>
        </div>
      </div>

      {/* Settings search + GreenBoost-extras filter */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
        padding: "10px 0 12px",
      }}>
        <input
          type="text"
          className="gs-input-text"
          placeholder="Search settings… (try &quot;stutter&quot;, &quot;VRAM&quot;, &quot;overlay&quot;)"
          style={{ flex: 1, minWidth: 200 }}
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <button
          onClick={() => setGbOnly(v => !v)}
          title={"Show only settings that don't exist in a stock Linux/NVIDIA "
               + "install. Each one's (i) explains what it does, why the gap "
               + "exists on Linux, and how to check it yourself."}
          style={{
            fontSize: 11, fontWeight: 600, padding: "5px 10px", borderRadius: 4,
            cursor: "pointer", whiteSpace: "nowrap",
            background: gbOnly ? "rgba(118,185,0,0.18)" : "rgba(255,255,255,0.05)",
            border: `1px solid ${gbOnly ? "rgba(118,185,0,0.5)" : "#333"}`,
            color: gbOnly ? "#76b900" : "#8a9ab0",
          }}
        >
          GreenBoost extras only
        </button>
        {filtering && (
          <button
            onClick={() => { setQuery(""); setGbOnly(false); }}
            style={{
              fontSize: 11, padding: "5px 10px", borderRadius: 4, cursor: "pointer",
              background: "transparent", border: "1px solid #333", color: "#8a9ab0",
            }}
          >
            Clear
          </button>
        )}
      </div>

      {gbOnly && (
        <p style={{ fontSize: 12, color: "#b8c0cc", lineHeight: 1.6,
                    margin: "0 0 12px", padding: "10px 12px",
                    background: "rgba(118,185,0,0.06)",
                    border: "1px solid rgba(118,185,0,0.2)", borderRadius: 6 }}>
          Showing only what GreenBoost adds on top of a stock Linux install ,
          in most cases things NVIDIA doesn't offer on Linux at all, since
          NVIDIA App / GeForce Experience has never shipped for it. Open the
          (i) on any row for what it does, why the gap exists on Linux
          specifically, and how to check the effect on your own machine , no
          invented numbers, just a method you can run yourself.
        </p>
      )}

      {/* Global Settings Profile bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
        padding: "10px 0 12px", borderBottom: "1px solid #1e1e1e",
      }}>
        <span style={{ fontSize: 11, color: "#8a9ab0", flexShrink: 0 }}>Profile:</span>
        <select
          className="gs-select"
          style={{ minWidth: 140, flex: "0 1 160px" }}
          value={selectedGsProfile}
          onChange={e => setSelectedGsProfile(e.target.value)}
        >
          <option value="">, select ,</option>
          {gsProfiles.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <button className="btn-revert" style={{ padding: "5px 10px", fontSize: 12 }}
                disabled={!selectedGsProfile} onClick={handleGsProfileLoad}>
          Load
        </button>
        <button className="btn-revert" style={{ padding: "5px 10px", fontSize: 12,
                                                borderColor: "#e05252", color: "#e05252" }}
                disabled={!selectedGsProfile} onClick={handleGsProfileDelete}>
          Delete
        </button>
        <div style={{ display: "flex", gap: 6, flex: 1, minWidth: 160 }}>
          <input
            type="text"
            className="gs-input-text"
            placeholder="Save current as…"
            style={{ flex: 1, minWidth: 0 }}
            value={gsProfileInput}
            onChange={e => setGsProfileInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") handleGsProfileSave(); }}
          />
          <button className="btn-optimize" style={{ padding: "5px 12px", fontSize: 12 }}
                  disabled={!gsProfileInput.trim()} onClick={handleGsProfileSave}>
            Save
          </button>
        </div>
        {gsProfileMsg && (
          <span style={{ fontSize: 11, color: "#9a9ab0", width: "100%" }}>{gsProfileMsg}</span>
        )}
      </div>

      <FilterSection title="PERFORMANCE & STUTTER" defaultOpen>
        {row("Background shader compiling",
             "Compiles shaders in the background instead of freezing the "
           + "game while it happens. The single biggest fix for the "
           + "\"brief freeze when something new appears on screen\" "
           + "problem in Proton games.",
             toggle(state.gplasync,
                    () => update({ gplasync: !state.gplasync })),
             GS_BENEFIT["Background shader compiling"], undefined, GS_INFO["Background shader compiling"])}

        {row("Remember compiled shaders",
             "Saves the graphics driver's compiled shader work to disk and "
           + "reuses it next time you play. Returning to a shader-heavy "
           + "game loads faster and stutters less the second time around.",
             toggle(state.vk_pipeline_cache,
                    () => update({ vk_pipeline_cache: !state.vk_pipeline_cache })),
             GS_BENEFIT["Remember compiled shaders"], undefined, GS_INFO["Remember compiled shaders"])}

        {row("Performance lock (CPU + GPU)",
             "While you're playing, forces your CPU and graphics card to "
           + "run at maximum performance instead of power-saving mode, "
           + "then puts everything back to normal the moment you quit. "
           + "Uses more power and runs hotter while active.",
             toggle(state.perf_lock,
                    () => update({ perf_lock: !state.perf_lock })),
             GS_BENEFIT["Performance lock (CPU + GPU)"], undefined, GS_INFO["Performance lock (CPU + GPU)"])}

        {row("Pause desktop effects while playing",
             "Turns off your desktop's visual effects (transparency, "
           + "animations) for the duration of the game, freeing up a bit "
           + "of GPU time and reducing stutter. Everything looks normal "
           + "again the instant you quit.",
             toggle(state.compositor_suspend,
                    () => update({ compositor_suspend: !state.compositor_suspend })),
             GS_BENEFIT["Pause desktop effects while playing"], undefined, GS_INFO["Pause desktop effects while playing"])}

        {row("Give the game GPU priority",
             "Tells the graphics driver the game's rendering work matters "
           + "more than background apps (your desktop effects, browser, "
           + "etc.), so the game gets first access to the GPU.",
             toggle(state.vk_queue_priority,
                    () => update({ vk_queue_priority: !state.vk_queue_priority })),
             GS_BENEFIT["Give the game GPU priority"], undefined, GS_INFO["Give the game GPU priority"])}

        {row("Shader compile threads",
             "How many CPU threads to use for background shader "
           + "compiling. 0 lets GreenBoost pick automatically based on "
           + "your CPU.",
             <input
               type="number" min={0} max={64} step={1}
               className="gs-input-num"
               value={state.shader_threads}
               onChange={e =>
                 update({ shader_threads: parseInt(e.target.value) || 0 })}
             />,
             GS_BENEFIT["Shader compile threads"], undefined, GS_INFO["Shader compile threads"])}

        {row("Shader cache size limit (GB)",
             "Maximum disk space the shader cache (see \"Background "
           + "shader compiling\" above) is allowed to use before old "
           + "entries get cleared out. Default 8.",
             <input
               type="number" min={1} max={128} step={1}
               className="gs-input-num"
               value={state.shader_cache_gb}
               onChange={e =>
                 update({ shader_cache_gb: parseInt(e.target.value) || 8 })}
             />,
             GS_BENEFIT["Shader cache size limit (GB)"], undefined, GS_INFO["Shader cache size limit (GB)"])}
      </FilterSection>

      <FilterSection title="IMAGE QUALITY & UPSCALING" defaultOpen>
        {row("DLSS Model Version",
             `Which version of NVIDIA's DLSS upscaling model games use. `
             + `Recommended for your ${state.detected_series || "GPU"}: ${state.recommended_preset}. `
             + `"Auto" always picks that recommendation for you.`,
             <select
               className="gs-select"
               value={state.dlss_preset}
               onChange={e => update({ dlss_preset: e.target.value })}
             >
               {presets.map(p => (
                 <option key={p.id} value={p.id}>{p.label}</option>
               ))}
             </select>,
             GS_BENEFIT["DLSS Model Version"], undefined, GS_INFO["DLSS Model Version"])}

        {row("Always use newest DLSS files",
             "Games sometimes ship with an older DLSS version baked in. "
           + "This makes Proton swap in the newest one it has instead , "
           + "usually a free image-quality upgrade.",
             toggle(state.dlss_upgrade,
                    () => update({ dlss_upgrade: !state.dlss_upgrade })),
             GS_BENEFIT["Always use newest DLSS files"], undefined, GS_INFO["Always use newest DLSS files"])}

        {row("DLSS indicator overlay",
             "Shows a small on-screen label confirming which DLSS mode is "
           + "actually active while you play. Handy for checking a setting "
           + "took effect , safe to turn off once you've confirmed it.",
             toggle(state.dlss_indicator,
                    () => update({ dlss_indicator: !state.dlss_indicator })),
             GS_BENEFIT["DLSS indicator overlay"], undefined, GS_INFO["DLSS indicator overlay"])}

        {row("HDR (High Dynamic Range)",
             "Brighter highlights and deeper blacks in games that support it. "
           + "Only turn this on if your monitor is actually HDR-capable , "
           + "otherwise colors can look washed out or too dark.",
             toggle(state.hdr, () => update({ hdr: !state.hdr })),
             GS_BENEFIT["HDR (High Dynamic Range)"], undefined, GS_INFO["HDR (High Dynamic Range)"])}

        {row("NIS sharpening , ready to use",
             "Prepares NVIDIA Image Scaling so it's ready to go, without "
           + "turning the effect on yet. Turn this on first if you plan to "
           + "actually use NIS sharpening below.",
             toggle(state.nis_enable,
                    () => update({ nis_enable: !state.nis_enable })),
             GS_BENEFIT["NIS sharpening , ready to use"], undefined, GS_INFO["NIS sharpening , ready to use"])}

        {row("NIS sharpening , actually apply it",
             "Turns the NIS sharpening effect on for real, every frame. "
           + "Uses a small amount of extra graphics memory , turn off if "
           + "you notice stutter.",
             state.nis_enable
               ? toggle(state.nis_dispatch,
                        () => update({ nis_dispatch: !state.nis_dispatch }))
               : toggle(false, () => {}),
             state.nis_enable && !state.nis_dispatch
               ? "Turn this on for a visibly sharper image." : undefined,
             state.nis_enable
               ? undefined
               : "Needs \"NIS sharpening , ready to use\" turned on above first , has no effect until then.",
             GS_INFO["NIS sharpening , actually apply it"])}

        {row("Sharpness",
             `How strong the sharpening effect looks (currently ${state.nis_sharpness.toFixed(2)}, `
           + "default 0.50). Higher looks crisper but can add visible "
           + "edges around objects. Takes effect next time you launch a "
           + "game , it won't change anything in a game that's already "
           + "running.",
             <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
               <input
                 type="range" min={0} max={1} step={0.05}
                 style={{ flex: 1 }}
                 value={state.nis_sharpness}
                 onChange={e => update({ nis_sharpness: parseFloat(e.target.value) })}
               />
               <span style={{ minWidth: 32, fontSize: 12, textAlign: "right" }}>
                 {state.nis_sharpness.toFixed(2)}
               </span>
             </div>,
             GS_BENEFIT["Sharpness"], undefined, GS_INFO["Sharpness"])}

        {row("Upscale ratio",
             "How much smaller NIS renders the image before scaling it "
           + "back up to your screen , smaller renders faster but softer. "
           + "\"Off\" (100%) only sharpens, with no upscaling. Roughly: "
           + "77% ≈ Quality, 67% ≈ Balanced, 50% ≈ Performance.",
             <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
               <input
                 type="range" min={0.5} max={1.0} step={0.01}
                 style={{ flex: 1 }}
                 value={state.nis_scale}
                 onChange={e => update({ nis_scale: parseFloat(e.target.value) })}
               />
               <span style={{ minWidth: 42, fontSize: 12, textAlign: "right" }}>
                 {state.nis_scale === 1.0
                   ? "Off"
                   : `${(state.nis_scale * 100).toFixed(0)}%`}
               </span>
             </div>,
             GS_BENEFIT["Upscale ratio"], undefined, GS_INFO["Upscale ratio"])}
      </FilterSection>

      <FilterSection title="LATENCY & FRAME PACING" defaultOpen={false}>
        {row("NVIDIA Reflex (lower input lag)",
             "Reduces the delay between your mouse/controller input and "
           + "what you see on screen. Needs a fairly recent NVIDIA driver "
           + "(version 545 or newer) , safe to leave on otherwise.",
             toggle(state.reflex_enable,
                    () => update({ reflex_enable: !state.reflex_enable })),
             GS_BENEFIT["NVIDIA Reflex (lower input lag)"], undefined, GS_INFO["NVIDIA Reflex (lower input lag)"])}

        {row("FPS cap",
             "Hard limit on frames per second. 0 means uncapped. Setting "
           + "this a few FPS below your monitor's refresh rate, combined "
           + "with Reflex above, usually gives the smoothest, lowest-lag "
           + "feel.",
             <input
               type="number" min={0} max={360} step={5}
               className="gs-input-num"
               value={state.fps_cap}
               onChange={e => update({ fps_cap: parseInt(e.target.value) || 0 })}
             />,
             GS_BENEFIT["FPS cap"], undefined, GS_INFO["FPS cap"])}
      </FilterSection>

      <FilterSection title="MEMORY & VRAM OVERFLOW" defaultOpen={false}>
        {row("Pre-warm overflow memory",
             "Primes GreenBoost's system-memory overflow pool the moment "
           + "the game starts, instead of waiting until it's actually "
           + "needed , avoids a brief stutter the first time a scene needs "
           + "more memory than your graphics card has.",
             toggle(state.ddr_prewarm,
                    () => update({ ddr_prewarm: !state.ddr_prewarm })),
             GS_BENEFIT["Pre-warm overflow memory"], undefined, GS_INFO["Pre-warm overflow memory"])}

        {row("Remove memory-locking limit",
             "Lifts a system limit on how much memory Proton can lock in "
           + "place for the game , needed for some of GreenBoost's memory "
           + "tricks to work. If your system doesn't allow it, this is "
           + "silently skipped, no harm done.",
             toggle(state.memlock_unlimited,
                    () => update({ memlock_unlimited: !state.memlock_unlimited })),
             GS_BENEFIT["Remove memory-locking limit"], undefined, GS_INFO["Remove memory-locking limit"])}

        {row("VRAM headroom before overflow (MB)",
             "How much free graphics-card memory to keep in reserve "
           + "before GreenBoost starts moving things to system memory "
           + "instead. Default 32 MB works for most games.",
             <input
               type="number" min={8} max={512} step={8}
               className="gs-input-num"
               value={state.vk_overflow_min_mb}
               onChange={e =>
                 update({ vk_overflow_min_mb: parseInt(e.target.value) || 32 })}
             />,
             GS_BENEFIT["VRAM headroom before overflow (MB)"], undefined, GS_INFO["VRAM headroom before overflow (MB)"])}

        {row("Minimum reserved disk space (MB)",
             "Reserves at least this much space on your SSD as a final "
           + "overflow tier, beyond both your graphics card and system "
           + "memory. 0 means no fixed reservation.",
             <input
               type="number" min={0} max={65536} step={256}
               className="gs-input-num"
               value={state.vk_t3_min_mb}
               onChange={e =>
                 update({ vk_t3_min_mb: parseInt(e.target.value) || 0 })}
             />,
             GS_BENEFIT["Minimum reserved disk space (MB)"], undefined, GS_INFO["Minimum reserved disk space (MB)"])}

        {row("Enable OpenGL support",
             "Extends the same memory and performance tricks used for "
           + "Vulkan/DirectX games to older OpenGL games too. Leave this "
           + "on unless you're troubleshooting a specific OpenGL game.",
             toggle(state.gl_layer_enabled,
                    () => update({ gl_layer_enabled: !state.gl_layer_enabled })),
             GS_BENEFIT["Enable OpenGL support"], undefined, GS_INFO["Enable OpenGL support"])}

        {row("Overflow threshold (MB)",
             "How large a texture or graphics buffer has to be before "
           + "GreenBoost considers moving it to system memory instead of "
           + "your graphics card's memory. Lower catches more, but adds a "
           + "little overhead on small, frequently-updated items , the "
           + "default works well for most games.",
             <input
               type="number" min={1} max={512} step={1}
               className="gs-input-num"
               value={state.gl_overflow_min_mb}
               onChange={e => update({ gl_overflow_min_mb: parseInt(e.target.value) || 32 })}
             />,
             GS_BENEFIT["Overflow threshold (MB)"], undefined, GS_INFO["Overflow threshold (MB)"])}
      </FilterSection>

      <FilterSection title="OVERLAYS & VISIBILITY" defaultOpen={false}>
        {row("Performance overlay (GPU + FPS)",
             "Shows a live on-screen overlay with FPS, frametime graph, GPU "
           + "temperature/power/clocks, CPU load, and VRAM/RAM usage while "
           + "you play , works for any game regardless of whether it's "
           + "DirectX 9-12 or native Vulkan. Requires the \"mangohud\" "
           + "package installed on your system.",
             toggle(state.mangohud_enabled,
                    () => update({ mangohud_enabled: !state.mangohud_enabled })),
             GS_BENEFIT["Performance overlay (GPU + FPS)"], undefined, GS_INFO["Performance overlay (GPU + FPS)"])}

        {row("Show NVIDIA feature status overlay",
             "Adds DLSS mode, Frame Generation, and Reflex status text to "
           + "the game's own built-in overlay. Only works for games using "
           + "DXVK (DirectX 9/10/11) or native Vulkan , has no effect on "
           + "DirectX 12 games (those use vkd3d-proton, which doesn't have "
           + "this feature). Turn on the Performance overlay above instead "
           + "for a HUD that works on every game.",
             toggle(state.nvapi_hud,
                    () => update({ nvapi_hud: !state.nvapi_hud })),
             GS_BENEFIT["Show NVIDIA feature status overlay"], undefined, GS_INFO["Show NVIDIA feature status overlay"])}
      </FilterSection>

      <FilterSection title="DISPLAY & SESSION" defaultOpen={false}>
        {row("Detected GPU",
             state.detected_series ? `Series: ${state.detected_series}` : null,
             <span className="gs-value-readonly">{state.detected_gpu || ","}</span>)}

        {row("Session",
             "Auto-detected from your desktop environment",
             <span className="gs-value-readonly">{state.detected_session || ","}</span>)}

        {row("Wayland",
             "Runs games through Wayland (the modern Linux display system) "
           + "instead of the older X11. Auto-detected from how you're "
           + "currently logged in , leave this alone unless you have a "
           + "specific reason to override it.",
             toggle(state.wayland, () => update({ wayland: !state.wayland })),
             GS_BENEFIT["Wayland"], undefined, GS_INFO["Wayland"])}

        {row("Close to system tray",
             "Closing the window keeps the Suite running in the tray instead "
           + "of quitting, so it can still stop the game for you. Turn this "
           + "off to make the close button exit the Suite.",
             toggle(state.close_action !== "quit",
                    () => update({
                      close_action: state.close_action === "quit" ? "tray" : "quit"
                    })),
             GS_BENEFIT["Close to system tray"], undefined, GS_INFO["Close to system tray"])}

        {row("Stop the game when you quit",
             "When you quit the Suite, the game it launched is closed too , "
           + "the same thing Steam does. The game is asked to exit first and "
           + "only forced if it refuses.",
             toggle(state.stop_game_on_quit,
                    () => update({ stop_game_on_quit: !state.stop_game_on_quit })),
             GS_BENEFIT["Stop the game when you quit"], undefined, GS_INFO["Stop the game when you quit"])}

        {row("Upstream Proton",
             "Which Proton GreenBoost builds on top of. GreenBoost is a "
           + "wrapper, not a Proton of its own , it always needs a real one "
           + "underneath. \"Automatic\" picks Proton Experimental, or the "
           + "newest one you have if that isn't installed.",
             <select
               className="gs-select"
               value={state.proton_upstream}
               onChange={e => update({ proton_upstream: e.target.value })}
             >
               <option value="">Automatic</option>
               {protonInstalls.map(([name, path]) => (
                 <option key={path} value={path}>{name}</option>
               ))}
             </select>,
             GS_BENEFIT["Upstream Proton"], undefined, GS_INFO["Upstream Proton"])}

        {row("Keep Steam out of the way",
             "Starts Steam straight into the system tray when it isn't "
           + "already running, and launches your game without bringing the "
           + "Steam window to the front. Steam's tray icon always stays , "
           + "there's no setting anywhere that removes it.",
             toggle(state.steam_silent_launch,
                    () => update({ steam_silent_launch: !state.steam_silent_launch })),
             GS_BENEFIT["Keep Steam out of the way"], undefined,
             GS_INFO["Keep Steam out of the way"])}

        {row("Cinema mode on launch",
             "Turns off every monitor except your main one the moment you "
           + "click Launch, so games don't get confused by extra screens. "
           + "Turn your other monitors back on yourself from the Displays "
           + "page when you're done playing.",
             toggle(state.auto_disable_secondary_on_launch,
                    () => update({
                      auto_disable_secondary_on_launch:
                        !state.auto_disable_secondary_on_launch
                    })),
             GS_BENEFIT["Cinema mode on launch"], undefined, GS_INFO["Cinema mode on launch"])}
      </FilterSection>

      <FilterSection title="GAMING ALONGSIDE LOCAL AI" defaultOpen={false}>
        {row("Protect game memory under pressure",
             "When graphics-card memory runs low, frees up GreenBoost's own "
           + "overflow memory before touching memory the game is actively "
           + "using , helps protect your framerate when memory gets tight.",
             toggle(state.vk_memory_priority,
                    () => update({ vk_memory_priority: !state.vk_memory_priority })),
             GS_BENEFIT["Protect game memory under pressure"], undefined, GS_INFO["Protect game memory under pressure"])}

</FilterSection>

      <FilterSection title="ADVANCED & DIAGNOSTICS" defaultOpen={false}
        note={"Fine-tuning and troubleshooting knobs. The defaults work well "
            + "for almost everyone , you'd only change these if you're "
            + "diagnosing a specific problem."}>

        {row("Verbose Vulkan logging",
             "Writes detailed frame-by-frame diagnostic logs. Generates a "
           + "lot of data very fast , only turn on while actively "
           + "troubleshooting a problem, then turn it back off.",
             toggle(state.vk_debug,
                    () => update({ vk_debug: !state.vk_debug })),
             GS_BENEFIT["Verbose Vulkan logging"], undefined, GS_INFO["Verbose Vulkan logging"])}

        {row("Keep session logs for (days)",
             "How many days of session logs GreenBoost keeps before "
           + "automatically deleting old ones. Default 14.",
             <input
               type="number" min={1} max={365} step={1}
               className="gs-input-num"
               value={state.log_ttl_days}
               onChange={e =>
                 update({ log_ttl_days: parseInt(e.target.value) || 14 })}
             />,
             GS_BENEFIT["Keep session logs for (days)"], undefined, GS_INFO["Keep session logs for (days)"])}

        {row("Pin a specific shader-compiler version",
             "Locks the background shader compiler to one specific "
           + "release (e.g. \"2.4.1\") instead of always using the newest "
           + "one you've downloaded. Leave as \"current\" unless you're "
           + "chasing down a regression.",
             <input
               type="text"
               className="gs-input-text"
               placeholder="current"
               value={state.gplasync_version}
               onChange={e =>
                 update({ gplasync_version: e.target.value || "current" })}
             />,
             GS_BENEFIT["Pin a specific shader-compiler version"], undefined, GS_INFO["Pin a specific shader-compiler version"])}

        {row("DirectX 12 feature flags",
             "Advanced feature switches for DirectX 12 games specifically. "
           + "DXR turns on ray tracing support, Pipeline cache and "
           + "Descriptor buffer reduce stutter. Leave blank unless you "
           + "have a specific reason to change these.",
             <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
               <input
                 type="text"
                 className="gs-input-text"
                 placeholder="e.g. dxr,pipeline_library_app_cache"
                 value={state.vkd3d_config}
                 onChange={e => update({ vkd3d_config: e.target.value })}
               />
               <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                 {[
                   { flag: "dxr",                        label: "DXR" },
                   { flag: "pipeline_library_app_cache", label: "Pipeline cache" },
                   { flag: "descriptor_buffer",          label: "Descriptor buffer" },
                   { flag: "no_upload_hvv",              label: "No upload HVV" },
                 ].map(({ flag, label }) => {
                   const flags = state.vkd3d_config
                     .split(",").map(s => s.trim()).filter(Boolean);
                   const active = flags.includes(flag);
                   return (
                     <button
                       key={flag}
                       onClick={() => {
                         const next = active
                           ? flags.filter(f => f !== flag)
                           : [...flags, flag];
                         update({ vkd3d_config: next.join(",") });
                       }}
                       style={{
                         fontSize: 11, padding: "2px 8px", borderRadius: 4,
                         cursor: "pointer",
                         background: active ? "rgba(118,185,0,0.15)" : "rgba(255,255,255,0.05)",
                         border: `1px solid ${active ? "rgba(118,185,0,0.4)" : "#333"}`,
                         color: active ? "#76b900" : "#6b7280",
                         fontWeight: active ? 600 : 400,
                       }}
                     >
                       {label}
                     </button>
                   );
                 })}
               </div>
             </div>,
             GS_BENEFIT["DirectX 12 feature flags"], undefined, GS_INFO["DirectX 12 feature flags"])}
      </FilterSection>

      {/* GreenBoost behaviors with no honest on/off state. They were entries
          in the old "Added by GreenBoost" tab; giving them fake switches to
          keep them there would have been worse than saying so plainly. */}
      <FilterSection title="ALWAYS ON , NOTHING TO SWITCH" defaultOpen={false}>
        {GB_AUTOMATIC.map(f => row(
          f.title,
          f.what,
          <span style={{ fontSize: 11, color: "#8a9ab0", textAlign: "right",
                         display: "block", maxWidth: 190 }}>
            {f.noSwitch}
          </span>,
          f.tagline,
        ))}
      </FilterSection>

      <DlssLibrarySection />

      {msg && <p style={{ fontSize: 12, color: "#76b900",
                          margin: "10px 0 0" }}>{msg}</p>}

      {summary && (
        <ChangeSummaryModal
          title={summary.title}
          subtitle={summary.subtitle}
          changes={summary.changes}
          notes={summary.notes}
          unchangedMessage={summary.unchanged}
          onClose={() => setSummary(null)}
        />
      )}
    </div>
  );
}

export function GamesView() {
  const [games, setGames]           = useState<Game[]>([]);
  const [filter, setFilter]         = useState("");
  const [selected, setSelected]     = useState<Game | null>(null);
  const [groups, setGroups]         = useState<SettingGroup[]>([]);
  const [gameOverrides, setGameOverrides] = useState<GameOverrides | null>(null);
  const [savingOverrides, setSavingOverrides] = useState(false);
  const [overridesMsg, setOverridesMsg] = useState<string | null>(null);
  const [savedProfiles, setSavedProfiles] = useState<string[]>([]);
  const [loadingGames, setLoadingGames]     = useState(true);
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [applying, setApplying]     = useState(false);
  const [reverting, setReverting]   = useState(false);
  const [updatingDlss] = useState(false);
  const [msg, setMsg]               = useState<string | null>(null);
  const [dlssMsg, setDlssMsg]       = useState<string | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey]   = useState<string | null>(null);
  const selectedRef = useRef<Game | null>(null);

  const [dlssSourceState, setDlssSourceState] =
    useState<DlssSourceState | null>(null);
  const [gameAnalytics, setGameAnalytics] = useState<GameAnalytics | null>(null);

  const [gameSettingProfiles, setGameSettingProfiles] = useState<string[]>([]);
  const [selectedGspName, setSelectedGspName] = useState<string>("");
  const [gspInput, setGspInput] = useState<string>("");
  const [gspMsg, setGspMsg] = useState<string | null>(null);

  // Per-game DLSS/Streamline DLL version status (not fetched until the
  // user asks , "not checked yet" is a real, distinct state from "up to
  // date").
  const [dlssStatus, setDlssStatus] = useState<DlssStatus | null>(null);
  const [scanningDlss, setScanningDlss] = useState(false);

  // DirectStorage diagnostic , cheap (local DLL scan + block-device lookup,
  // no network), so unlike DLSS status this auto-fetches on game select
  // instead of waiting for a manual "scan" click.
  const [directStorageInfo, setDirectStorageInfo] = useState<DirectStorageInfo | null>(null);

  // Global Settings' "Live Overlay" (mangohud_enabled) toggle, fetched once
  // so every fallback wrappers-object literal below can seed `mangohud`
  // from it instead of a hardcoded `false`. Confirmed live 2026-08-08: with
  // the hardcoded default, clicking Optimize (or GameMode, or editing
  // Gamescope args) on ANY game that had no per-game override file yet
  // silently wrote `wrappers.mangohud: false` into it , permanently
  // overriding the global toggle for that one game from then on, with no
  // visible sign anything had changed (the global toggle stayed showing
  // ON the whole time). GameWrappers.mangohud is a plain non-optional
  // bool on both sides (types.ts, game_overrides.rs) , there's no
  // "unset, inherit global" representation once a wrappers object exists
  // at all, so the seed value on FIRST creation is the only thing that
  // can keep behavior matching what the global toggle promises.
  // Both of these are now derived from the shared store rather than copied
  // into local state. That's the whole point: flip a runtime toggle in All
  // Games and the "Global (ON)" hints in this tab update immediately,
  // instead of after a remount.
  const [summary, setSummary] = useState<{
    title: string; subtitle?: string; changes: Change[];
    notes?: string[]; unchanged?: string;
  } | null>(null);
  const gs = useGlobalSettings();
  const globalMangohudDefault = !!gs?.mangohud_enabled;
  // Subset of GlobalSettingsState used to resolve what a per-game tri-state
  // "Global" pick actually resolves to right now, shown in parentheses next
  // to the Global button (Runtime Overrides section) instead of leaving the
  // user to guess or go check the All Games tab.
  const globalRuntimeDefaults: Pick<
    GlobalSettingsState, "gplasync" | "perf_lock" | "compositor_suspend" | "vk_pipeline_cache" | "dlss_preset"
  > | null = gs ? {
    gplasync: gs.gplasync, perf_lock: gs.perf_lock,
    compositor_suspend: gs.compositor_suspend, vk_pipeline_cache: gs.vk_pipeline_cache,
    dlss_preset: gs.dlss_preset,
  } : null;

  useEffect(() => {
    invoke<DlssSourceState>("get_dlss_sources")
      .then(setDlssSourceState)
      .catch(console.error);
    invoke<string[]>("list_gpu_profiles")
      .then(setSavedProfiles)
      .catch(() => setSavedProfiles([]));
    loadGlobalSettings();
  }, []);

  const loadGames = useCallback(async () => {
    setLoadingGames(true);
    try {
      const list: Game[] = await invoke("get_games");
      const deduped = Array.from(new Map(list.map(g => [g.path, g])).values());
      setGames(deduped);
      if (deduped.length > 0 && !selectedRef.current) {
        setSelected(deduped[0]);
        selectedRef.current = deduped[0];
      }
    } catch (e) { console.error(e); }
    setLoadingGames(false);
  }, []);

  const [hiddenGames, setHiddenGames] = useState<{ path: string; name: string }[]>([]);
  const [showHidden, setShowHidden] = useState(false);
  const [hideMsg, setHideMsg] = useState<string | null>(null);

  const loadHidden = useCallback(() => {
    invoke<{ path: string; name: string }[]>("list_hidden_games")
      .then(setHiddenGames).catch(() => {});
  }, []);

  useEffect(() => { loadHidden(); }, [loadHidden]);

  const handleRemoveGame = useCallback(async (e: React.MouseEvent, game: Game) => {
    e.stopPropagation();
    setHideMsg(null);
    try {
      await invoke("hide_game", { path: game.path, name: game.name });
      setGames(prev => prev.filter(g => g.path !== game.path));
      if (selectedRef.current?.path === game.path) setSelected(null);
      loadHidden();
    } catch (err) {
      // Previously console.error-only: the user clicks the hide button,
      // nothing visibly happens on failure (the game correctly stays in
      // the list since the state update above is skipped), but there was
      // no indication the click didn't work at all.
      setHideMsg(`Couldn't hide "${game.name}": ${err}`);
    }
  }, [loadHidden]);

  const handleRestoreGame = useCallback(async (path: string) => {
    setHideMsg(null);
    try {
      await invoke("unhide_game", { path });
      loadHidden();
      loadGames();
    } catch (err) {
      setHideMsg(`Couldn't restore game: ${err}`);
    }
  }, [loadHidden, loadGames]);

  const loadSettings = useCallback(async (game: Game) => {
    setLoadingSettings(true);
    setGroups([]);
    setMsg(null);
    setDlssMsg(null);
    try {
      const g: SettingGroup[] = await invoke("get_game_settings", { path: game.path });
      setGroups(g);
    } catch (e) { console.error(e); }
    setLoadingSettings(false);
  }, []);

  useEffect(() => { loadGames(); }, [loadGames]);

  useEffect(() => {
    invoke<string[]>("list_game_setting_profiles").then(setGameSettingProfiles).catch(console.error);
  }, []);

  const reloadGsp = useCallback(() => {
    invoke<string[]>("list_game_setting_profiles").then(setGameSettingProfiles).catch(console.error);
  }, []);

  const handleGspSave = useCallback(async () => {
    const name = gspInput.trim();
    if (!name || !gameOverrides || !selected?.appid) return;
    try {
      await invoke("save_game_setting_profile", { name, overrides: gameOverrides });
      setGspMsg(`Saved as "${name}".`);
      setGspInput("");
      reloadGsp();
      setSelectedGspName(name);
    } catch (e: any) { setGspMsg("Save failed: " + (e?.message ?? e)); }
  }, [gspInput, gameOverrides, selected, reloadGsp]);

  const handleGspLoad = useCallback(async () => {
    if (!selectedGspName || !selected?.appid) return;
    try {
      const loaded = await invoke<GameOverrides | null>("load_game_setting_profile", { name: selectedGspName });
      if (!loaded) { setGspMsg(`Profile "${selectedGspName}" not found.`); return; }
      await invoke("save_game_overrides", { appid: selected.appid, overrides: loaded });
      setGameOverrides(loaded as GameOverrides);
      setGspMsg(`Loaded "${selectedGspName}".`);
    } catch (e: any) { setGspMsg("Load failed: " + (e?.message ?? e)); }
  }, [selectedGspName, selected]);

  const handleGspDelete = useCallback(async () => {
    if (!selectedGspName) return;
    try {
      await invoke("delete_game_setting_profile", { name: selectedGspName });
      setGspMsg(`Deleted "${selectedGspName}".`);
      setSelectedGspName("");
      reloadGsp();
    } catch (e: any) { setGspMsg("Delete failed: " + (e?.message ?? e)); }
  }, [selectedGspName, reloadGsp]);

  useEffect(() => {
    if (selected) {
      selectedRef.current = selected;
      loadSettings(selected);
      invoke<DirectStorageInfo>("get_directstorage_info", { path: selected.path })
        .then(setDirectStorageInfo)
        .catch(() => setDirectStorageInfo(null));
    } else {
      setDirectStorageInfo(null);
    }
    if (selected?.appid) {
      invoke<GameOverrides>("get_game_overrides", { appid: selected.appid })
        .then(setGameOverrides)
        .catch(() => setGameOverrides(null));
      invoke<GameAnalytics>("analyze_game_sessions", { appid: selected.appid })
        .then(a => setGameAnalytics(a.session_count > 0 ? a : null))
        .catch(() => setGameAnalytics(null));
    } else {
      setGameOverrides(null);
      setGameAnalytics(null);
    }
    // Switching games invalidates any prior scan , go back to "not checked".
    setDlssStatus(null);
  }, [selected, loadSettings]);

  const handleScanDlssStatus = useCallback(async (): Promise<DlssStatus | null> => {
    if (!selected) return null;
    setScanningDlss(true);
    let status: DlssStatus | null = null;
    try {
      status = await invoke<DlssStatus>("get_dlss_status", { path: selected.path });
      setDlssStatus(status);
    } catch (e) { console.error(e); }
    setScanningDlss(false);
    return status;
  }, [selected]);

  // Base object for any spot that needs to construct a wrappers value from
  // scratch (no per-game override existed yet). mangohud seeds from the
  // real global default, not a hardcoded false , see globalMangohudDefault
  // above for why that matters.
  const defaultWrappers = (): GameWrappers =>
    ({ gamemode: false, mangohud: globalMangohudDefault, gamescope: [] });

  const handleOptimize = async () => {
    if (!selected) return;
    setApplying(true);
    try {
      const ok: boolean = await invoke("optimize_game", { path: selected.path });

      // Apply optimal GreenBoost overrides BEFORE refreshing `selected`.
      // The selection effect ([selected, loadSettings], ~line 1009) fires
      // on every setSelected() and re-reads get_game_overrides from disk ,
      // if that refresh ran before this save landed, its read could race
      // ahead of save_game_overrides and clobber setGameOverrides(next)
      // back to the pre-save file contents. Confirmed live 2026-08-08:
      // this intermittently left the panel showing "Not optimized" right
      // after a successful Optimize click, even though the write behind
      // it had actually succeeded.
      let overridesApplied = false;
      let overrideChanges: Change[] = [];
      // Captured BEFORE optimize_game rewrites the game's own config file ,
      // afterwards needs_change is false everywhere and there is nothing
      // left to report. Lets the summary name which in-game settings moved
      // instead of just claiming success.
      const inGameChanges: Change[] = groups.flatMap(g =>
        g.settings.filter(st => st.needs_change).map(st => ({
          label: `${g.title} , ${st.display}`,
          from: st.current || "not set",
          to: st.recommended,
        })));
      if (selected.appid && gameOverrides) {
        // gameOptimal.ts is the single source of truth for "what's
        // recommended" , anyNeedsChange (the "Not optimized" badge) reads
        // the same diffFromOptimal(), so the two can no longer drift.
        const seeded = { ...gameOverrides, wrappers: gameOverrides.wrappers ?? defaultWrappers() };
        if (diffFromOptimal(seeded).length > 0) {
          const patch = optimalPatch(seeded);
          const next = { ...seeded, ...patch };
          await invoke("save_game_overrides", { appid: selected.appid, overrides: next });
          setGameOverrides(next);
          overridesApplied = true;
          overrideChanges = summarizeGame(seeded, patch);
        }
      }

      // Single refresh, now that the save (if any) has landed on disk.
      // setSelected() alone is enough , the selection effect re-runs
      // loadSettings() and get_game_overrides() for the new object, so a
      // separate manual loadSettings() call here would just duplicate it.
      if (ok || overridesApplied) {
        const list: Game[] = await invoke("get_games");
        const deduped = Array.from(new Map(list.map(g => [g.path, g])).values());
        setGames(deduped);
        const updated = deduped.find(g => g.path === selected.path) ?? selected;
        setSelected(updated);
      }

      if (!ok && !overridesApplied) {
        setMsg("No writable config file found.");
      } else {
        setMsg(null);
        setSummary({
          title: `Optimized ${selected.name}`,
          subtitle: ok
            ? "GreenBoost overrides and the game's own config file"
            : "GreenBoost overrides only , no writable game config file found",
          changes: [...overrideChanges, ...(ok ? inGameChanges : [])],
          unchanged: "This game was already at every recommended value.",
        });
      }
    } catch { setMsg("Error applying settings."); }
    setApplying(false);
  };

  const handleRevert = async () => {
    if (!selected) return;
    setReverting(true);
    try {
      const ok: boolean = await invoke("revert_game", { path: selected.path });
      setMsg(ok ? "Settings reverted to original." : "No backup found to restore.");
      if (ok) {
        const list: Game[] = await invoke("get_games");
        const deduped = Array.from(new Map(list.map(g => [g.path, g])).values());
        setGames(deduped);
        const updated = deduped.find(g => g.path === selected.path) ?? selected;
        setSelected(updated);
        loadSettings(updated);
      }
    } catch { setMsg("Error reverting settings."); }
    setReverting(false);
  };

  const saveOverrides = useCallback(async (patch: Partial<GameOverrides>) => {
    if (!selected?.appid || !gameOverrides) return;
    const next = { ...gameOverrides, ...patch };
    setGameOverrides(next);
    setSavingOverrides(true);
    setOverridesMsg(null);
    try {
      await invoke("save_game_overrides", { appid: selected.appid, overrides: next });
      setOverridesMsg("Saved.");
    } catch (e: any) {
      setOverridesMsg("Save failed: " + (e?.message ?? e));
    }
    setSavingOverrides(false);
  }, [selected, gameOverrides]);

  const [dlssModal, setDlssModal] = useState<null | InstallStreamProps>(null);
  const [cacheRevision, setCacheRevision] = useState(0);

  const handleUpdateDlss = () => {
    if (!selected) return;
    setDlssMsg(null);
    setDlssModal({
      title:   `Update DLSS , ${selected.name}`,
      command: "sync_and_update_dlss_streaming",
      args:    { path: selected.path },
      onDone:  async (ok) => {
        setDlssModal(null);
        setCacheRevision(r => r + 1);
        if (ok) {
          // Re-scan so the DLSS SETTINGS panel below stops saying "Not
          // checked for updates yet" and reflects what was just applied ,
          // and so the success message can state a real N-of-N count
          // instead of a generic "updated" that doesn't confirm anything.
          const status = await handleScanDlssStatus();
          const total = status?.findings.length ?? 0;
          const atLatest = total - (status?.out_of_date ?? total);
          setDlssMsg(total > 0
            ? `Libraries updated and latest versions selected for the game , ${atLatest} of ${total} DLLs at latest.`
            : "Libraries updated and latest versions selected for the game.");
        } else {
          setDlssMsg("Update finished with errors , see modal log.");
        }
        loadGames();
      },
    });
  };

  const handleRestoreDlss = () => {
    if (!selected) return;
    setDlssMsg(null);
    setDlssModal({
      title:       `Restore DLSS backup , ${selected.name}`,
      command:     "restore_dlss_streaming",
      args:        { path: selected.path },
      destructive: true,
      confirm:     "Roll back this game's DLSS DLLs to the previously "
                 + "saved .bak files?  Any current versions will be replaced.",
      onDone:      (ok) => {
        setDlssModal(null);
        setDlssMsg(ok ? "DLSS DLLs restored from backup."
                      : "Restore finished with errors , see modal log.");
        loadGames();
      },
    });
  };

  const handleRestoreDlssToOriginal = () => {
    if (!selected) return;
    setDlssMsg(null);
    setDlssModal({
      title:       `Restore to shipped version , ${selected.name}`,
      command:     "restore_dlss_to_original_streaming",
      args:        { path: selected.path },
      destructive: true,
      confirm:     "Put back exactly what this game originally shipped "
                 + "with, undoing every GreenBoost update ever applied to "
                 + "it? Any files with no shipped-version snapshot on "
                 + "record are left untouched.",
      onDone:      (ok) => {
        setDlssModal(null);
        setDlssMsg(ok ? "DLSS DLLs restored to the shipped version."
                      : "Restore finished with errors , see modal log.");
        loadGames();
        setDlssStatus(null);
      },
    });
  };

  const [dlssMenuOpen, setDlssMenuOpen] = useState(false);

  const [launching, setLaunching] = useState(false);

  /** Watch a launch until a game process appears, or until it's clear none will.
   *
   *  `launch_game` returning Ok only means Steam accepted the request. The
   *  view used to print that as success and stop looking, so a wrapper that
   *  died in the first second (2026-08-20: the Proton wrapper could not even
   *  be parsed by Steam's runtime Python) read as "the game just didn't
   *  open". The backend already knew; nobody asked it. */
  const watchLaunch = async (appid: string) => {
    // 100 polls x 2 s = 200 s, comfortably past the backend's 180 s budget.
    // At 32 the view stopped watching after 64 s and simply went quiet, so a
    // launch that was still legitimately working looked abandoned.
    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 2000));
      let st: LaunchStatus;
      try {
        st = await invoke<LaunchStatus>("get_launch_status");
      } catch {
        return;                         // backend gone; leave the last message
      }
      if (st.state === "started" && st.appid === appid) {
        setMsg("Running.");
        return;
      }
      if (st.state === "failed" && st.appid === appid) {
        const log = st.log.length
          ? "\n\nWhat the Proton wrapper said:\n" + st.log.join("\n")
          : "\n\nThe Proton wrapper wrote no log at all, which usually means " +
            "Steam never reached it , check the game's compatibility tool is " +
            "set to GreenBoost Proton.";
        setMsg(
          "No game process ever started. Steam accepted the launch, but nothing " +
          "came up in 60 seconds.\n\nNothing is broken and no settings changed " +
          ", you can launch again, or pick a different Proton under Global " +
          "Settings \u2192 Upstream Proton." + log);
        return;
      }
      if (st.state === "pending" && st.appid === appid) {
        // Say WHAT is happening, not just that time is passing. A counter on
        // its own is indistinguishable from a hang, which is precisely how
        // this looked on 2026-08-21.
        const mins = Math.floor(st.eta_s / 60), secs = st.eta_s % 60;
        const left = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        setMsg(
          `Starting\u2026 ${st.phase}.\n` +
          `${st.elapsed_s}s elapsed , still normal for up to another ${left}. ` +
          `First runs are the slow ones: the wine prefix is built and shader ` +
          `pipelines are compiled once, then cached.`);
      }
    }
  };

  const handleLaunch = async () => {
    if (!selected?.appid) {
      setMsg("This entry has no Steam appid , can't launch.");
      return;
    }
    const appid = selected.appid;
    setLaunching(true);
    setMsg(null);
    try {
      // Reads the shared store instead of re-invoking the backend. The old
      // defensive re-fetch existed because this handler sat next to two
      // other private copies of GlobalSettings and could not trust either.
      const cinema = !!globalSettings.get()?.auto_disable_secondary_on_launch;
      const out: string = await invoke("launch_game", {
        appid,
        disableSecondaryDisplays: cinema,
      });
      setMsg(out);
      void watchLaunch(appid);   // resolves the message to running / failed
    } catch (e: any) {
      setMsg("Launch failed: " + (e?.message ?? e));
    }
    setLaunching(false);
  };

  const overrideDiffKeys = gameOverrides ? diffFromOptimal(gameOverrides) : [];
  const overrideActiveCount = gameOverrides ? countActiveOverrides(gameOverrides) : 0;
  const anyNeedsChange = groups.some(g => g.settings.some(s => s.needs_change))
    || overrideDiffKeys.length > 0;

  const applyAllRecommended = () => {
    if (!selected?.appid || !gameOverrides) return;
    const seeded = { ...gameOverrides, wrappers: gameOverrides.wrappers ?? defaultWrappers() };
    saveOverrides(optimalPatch(seeded));
  };

  const [gamesTab, setGamesTab] = useState<"program" | "global">("program");

  return (
    <>
      <div className="sub-nav">
        <button
          className={`sub-nav-tab${gamesTab === "program" ? " active" : ""}`}
          onClick={() => setGamesTab("program")}>This Game</button>
        <button
          className={`sub-nav-tab${gamesTab === "global" ? " active" : ""}`}
          onClick={() => setGamesTab("global")}>All Games</button>
      </div>

      {gamesTab === "global" && <GlobalSettingsPanel />}

      {gamesTab === "program" && (
    <div className="games-view">
      <div className="games-list">
        <div className="games-list-header">
          <span className="games-list-count">
            {loadingGames
              ? "Scanning…"
              : `${games.filter(g => !filter.trim() || g.name.toLowerCase().includes(filter.toLowerCase())).length}/${games.length} ${games.length === 1 ? "Game" : "Games"}`}
          </span>
          <div className="games-list-actions">
            <button
              className="icon-btn"
              title="Refresh"
              onClick={loadGames}
              style={loadingGames ? { animation: "spin 1s linear infinite" } : {}}
            >
              <Icon.Refresh />
            </button>
          </div>
        </div>
        <div style={{ padding: "6px 8px 4px" }}>
          <input
            type="text"
            placeholder="Filter games…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            style={{
              width: "100%", padding: "5px 10px", fontSize: 12,
              background: "#141414", border: "1px solid #2a2a2a",
              borderRadius: 4, color: "#e6e6e6", outline: "none",
            }}
          />
        </div>

        <div className="games-list-scroll">
          {loadingGames ? (
            <div className="spinner-row">
              <span className="animate-spin" style={{ display: "inline-block" }}><Icon.Refresh /></span>
              Scanning Steam library…
            </div>
          ) : games.length === 0 ? (
            <div className="settings-empty">No games found in Steam library.</div>
          ) : (
            games.filter(g =>
              !filter.trim() ||
              g.name.toLowerCase().includes(filter.toLowerCase())
            ).map(game => (
              <div
                key={game.path}
                className={`game-row${selected?.path === game.path ? " selected" : ""}`}
                onClick={() => setSelected(game)}
              >
                <button
                  className="game-status-dot"
                  title="Remove from list"
                  onClick={e => handleRemoveGame(e, game)}
                  style={{ background: "none", border: "none", padding: 0,
                           cursor: "pointer", display: "inline-flex", color: "inherit" }}
                >
                  <Icon.MinusCircle />
                </button>
                <GameThumb image={game.image} name={game.name} size={38} />
                <span className="game-name">{game.name}</span>
              </div>
            ))
          )}
        </div>

        {hiddenGames.length > 0 && (
          <div style={{ padding: "6px 12px 8px", borderTop: "1px solid #1e1e1e" }}>
            <button
              onClick={() => setShowHidden(v => !v)}
              style={{ background: "none", border: "none", color: "#8a9ab0",
                       fontSize: 11, cursor: "pointer", padding: 0 }}
            >
              {showHidden ? "Hide" : `${hiddenGames.length} removed , show`}
            </button>
            {showHidden && (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                {hiddenGames.map(hg => (
                  <div key={hg.path} style={{ display: "flex", justifyContent: "space-between",
                                               alignItems: "center", fontSize: 11, color: "#9a9a9a", gap: 8 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {hg.name}
                    </span>
                    <button
                      onClick={() => handleRestoreGame(hg.path)}
                      style={{ background: "none", border: "none", color: "#76b900",
                               fontSize: 11, cursor: "pointer", padding: 0, flexShrink: 0 }}
                    >
                      Restore
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {hideMsg && (
          <p style={{ fontSize: 11, color: "#ff6b6b", margin: "6px 12px 0" }}>
            {hideMsg}
          </p>
        )}
      </div>

      <div className="game-detail">
        {!selected ? (
          <div className="placeholder-view">
            <Icon.Gamepad />
            <p>Select a program from the list</p>
          </div>
        ) : (
          <>
            <div className="game-hero">
              <GameHeroBanner image={selected.image} name={selected.name} />
              <div className="game-hero-overlay">
                <div className="game-hero-name">{selected.name}</div>
                {selected.dlls.length > 0 && (
                  <div className="game-hero-sub">
                    {selected.dlls.length} RTX {selected.dlls.length === 1 ? "library" : "libraries"} detected
                  </div>
                )}
              </div>
            </div>

            <div className="game-action-bar">
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                {selected.appid && (
                  <button
                    className="btn-optimize"
                    onClick={handleLaunch}
                    disabled={launching}
                    title="Launch via Steam , uses your per-game Proton selection"
                  >
                    <Icon.Gamepad /> {launching ? "Launching…" : "Launch"}
                  </button>
                )}
                {!loadingSettings && (
                  <button
                    className="btn-optimize"
                    onClick={handleOptimize}
                    disabled={applying || !anyNeedsChange}
                  >
                    <Icon.Zap /> {applying ? "Applying…" : "Optimize"}
                  </button>
                )}
                {selected.has_backup && (
                  <button
                    className="btn-revert"
                    onClick={handleRevert}
                    disabled={reverting}
                  >
                    <Icon.Undo /> {reverting ? "Reverting…" : "Revert"}
                  </button>
                )}
                {selected.dlls.length > 0 && (
                  <>
                    <div className="dlss-split-btn"
                         style={{ position: "relative", display: "flex", alignItems: "stretch" }}>
                      <button
                        className="btn-optimize"
                        onClick={handleUpdateDlss}
                        disabled={updatingDlss}
                        style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
                      >
                        <Icon.Download />{" "}
                        {updatingDlss ? "Working…" : "Upgrade"}
                      </button>
                      <button
                        className="btn-optimize"
                        onClick={() => setDlssMenuOpen(o => !o)}
                        disabled={updatingDlss}
                        aria-haspopup="menu"
                        aria-expanded={dlssMenuOpen}
                        title="More DLSS actions"
                        style={{
                          borderTopLeftRadius: 0,
                          borderBottomLeftRadius: 0,
                          borderLeft: "1px solid rgba(0,0,0,0.35)",
                          padding: "7px 10px",
                          minWidth: 32,
                        }}
                      >
                        <Icon.ChevronDown />
                      </button>

                      {dlssMenuOpen && (
                        <div
                          role="menu"
                          onMouseLeave={() => setDlssMenuOpen(false)}
                          style={{
                            position: "absolute", top: "calc(100% + 4px)",
                            right: 0, minWidth: 220,
                            background: "#232323",
                            border: "1px solid #2a2a2a",
                            borderRadius: 6,
                            boxShadow: "0 6px 18px rgba(0,0,0,0.4)",
                            padding: 6, zIndex: 50,
                          }}
                        >
                          <button
                            role="menuitem"
                            onClick={() => { setDlssMenuOpen(false); handleUpdateDlss(); }}
                            style={menuItemStyle}
                          >
                            <Icon.Download />{" "}
                            Upgrade
                            <span style={menuItemHintStyle}>
                              From {dlssSourceState?.active ?? "bundle"}
                            </span>
                          </button>
                          <button
                            role="menuitem"
                            onClick={() => { setDlssMenuOpen(false); handleRestoreDlss(); }}
                            style={menuItemStyle}
                          >
                            <Icon.Undo />{" "}
                            Restore DLSS DLLs from backup
                            <span style={menuItemHintStyle}>
                              Undoes just the last update
                            </span>
                          </button>
                          <button
                            role="menuitem"
                            onClick={() => { setDlssMenuOpen(false); handleRestoreDlssToOriginal(); }}
                            style={menuItemStyle}
                          >
                            <Icon.Undo />{" "}
                            Restore to shipped version
                            <span style={menuItemHintStyle}>
                              Undoes every update, back to what the game shipped
                            </span>
                          </button>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {!loadingSettings && anyNeedsChange ? (
                  <span className="status-badge-warning">
                    <Icon.AlertCircle /> Not optimized
                  </span>
                ) : !loadingSettings && groups.length > 0 ? (
                  <span className="status-badge-ok">
                    <Icon.CheckCircle /> Optimized
                  </span>
                ) : null}

                {/* Per-game intelligence badges from session telemetry */}
                {gameAnalytics && (() => {
                  const hasVramData = gameAnalytics.peak_vram_mb > 0;
                  const fmtMb = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
                  const hours   = gameAnalytics.total_play_hours.toFixed(1);
                  const avgFmt  = gameAnalytics.avg_duration_min >= 60
                    ? `${(gameAnalytics.avg_duration_min / 60).toFixed(1)}h`
                    : `${gameAnalytics.avg_duration_min.toFixed(0)}m`;
                  // VRAM risk: peak > 85% of actual GPU VRAM , the point
                  // GreenBoost's T1 heap starts spilling into T2 DDR.
                  // Falls back to 10 GB when NVML total is unavailable.
                  const vramCap = gameAnalytics.gpu_total_vram_mb > 0
                    ? gameAnalytics.gpu_total_vram_mb * 0.85
                    : 10240;
                  const vramRisk = hasVramData && gameAnalytics.peak_vram_mb > vramCap;
                  const vramExplain = hasVramData
                    ? `This is the highest VRAM this game's own process reached across recorded sessions`
                      + `${gameAnalytics.vram_includes_other_apps ? " (some sessions could only measure whole-GPU usage, so this may include other apps)" : ""}`
                      + `, sampled every 5s while playing. Average across sessions: ${fmtMb(gameAnalytics.avg_vram_mb)}. `
                      + (vramRisk
                        ? `⚠ crossed 85% of your ${(gameAnalytics.gpu_total_vram_mb / 1024).toFixed(0)} GB card , GreenBoost starts spilling into T2 DDR past this point.`
                        : `Below the 85% spill threshold for your ${(gameAnalytics.gpu_total_vram_mb / 1024).toFixed(0)} GB card.`)
                    : "No VRAM samples recorded yet , sessions under a few seconds don't have time to sample.";
                  return (
                    <>
                      <span style={{
                        fontSize: 11, padding: "2px 7px", borderRadius: 4,
                        background: "rgba(99,102,241,0.12)",
                        border: "1px solid rgba(99,102,241,0.3)", color: "#a5b4fc",
                      }} title={`${gameAnalytics.session_count} sessions · ${hours}h total · avg ${avgFmt}/session`}>
                        {gameAnalytics.session_count}× played · {hours}h
                      </span>
                      <span style={{
                        display: "inline-flex", alignItems: "center",
                        fontSize: 11, padding: "2px 7px", borderRadius: 4,
                        background: vramRisk ? "rgba(248,113,113,0.12)" : "rgba(34,211,238,0.08)",
                        border: `1px solid ${vramRisk ? "rgba(248,113,113,0.4)" : "rgba(34,211,238,0.25)"}`,
                        color: vramRisk ? "#f87171" : "#67e8f9",
                      }}>
                        {vramRisk ? "⚠ " : ""}
                        {hasVramData ? `VRAM ${fmtMb(gameAnalytics.peak_vram_mb)} peak` : "VRAM: no data yet"}
                        <InfoTip>{vramExplain}</InfoTip>
                      </span>
                      {gameAnalytics.peak_t2_mb > 0 && (
                        <span style={{
                          display: "inline-flex", alignItems: "center",
                          fontSize: 11, padding: "2px 7px", borderRadius: 4,
                          background: "rgba(251,191,36,0.10)",
                          border: "1px solid rgba(251,191,36,0.3)", color: "#fbbf24",
                        }}>
                          T2 spill {fmtMb(gameAnalytics.peak_t2_mb)}
                          <InfoTip>
                            This game's textures/allocations overflowed your GPU's VRAM
                            and spilled into GreenBoost's system-RAM pool (T2 DDR) , the
                            highest amount seen across recorded sessions. Some performance
                            impact is expected while spilling; lowering texture quality or
                            enabling DLSS reduces it.
                          </InfoTip>
                        </span>
                      )}
                      {vramRisk && gameOverrides && selected?.appid && gameOverrides.dlss_preset !== "render_preset_latest" && (
                        <button
                          onClick={() => saveOverrides({ dlss_preset: "render_preset_latest" })}
                          disabled={savingOverrides}
                          style={{
                            fontSize: 11, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                            background: "rgba(248,113,113,0.18)",
                            border: "1px solid rgba(248,113,113,0.5)",
                            color: "#fca5a5",
                          }}
                          title="Enable DLSS Latest preset to reduce VRAM pressure below safe threshold"
                        >
                          Fix: Enable DLSS
                        </button>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>

            {(msg || dlssMsg) && (
              <div className="game-msg-bar">
                {msg && <span className="status-msg">{msg}</span>}
                {dlssMsg && <span className="status-msg" style={{ color: "#76b900" }}>{dlssMsg}</span>}
              </div>
            )}

            <div className="game-detail-body">
            {selected.dlls.length > 0 && (
              <DllPicker game={selected}
                         onApplied={() => loadGames()}
                         refreshTrigger={cacheRevision}
                         dlssStatus={dlssStatus}
                         scanningDlss={scanningDlss}
                         onScanDlssStatus={handleScanDlssStatus}
                         onRestoreToShipped={handleRestoreDlssToOriginal} />
            )}

            {directStorageInfo?.detected && (
              <div className="gs-row" style={{ padding: "10px 16px", fontSize: 12, color: "#8a9ab0" }}>
                <span style={{ color: "#e6e6e6", fontWeight: 600 }}>DirectStorage detected</span>
                {" "}({directStorageInfo.dlls_found.join(", ")}) ·{" "}
                Proton:{" "}
                {directStorageInfo.proton_capable === true
                  ? <span style={{ color: "#76b900" }}>{directStorageInfo.proton_build || "capable build"} (capable)</span>
                  : <span style={{ color: "#8a9ab0" }}>
                      {directStorageInfo.proton_build || "build"} (not confirmed capable)
                    </span>}
                {" · "}Storage:{" "}
                {directStorageInfo.nvme_storage === true
                  ? <span style={{ color: "#76b900" }}>NVMe</span>
                  : directStorageInfo.nvme_storage === false
                    ? <span style={{ color: "#e8a000" }}>not NVMe , limited benefit</span>
                    : <span style={{ color: "#8a9ab0" }}>unknown</span>}
              </div>
            )}

            {selected?.appid && gameOverrides && (
              <>
              {/* Game Setting Profile bar */}
              <div style={{
                display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
                padding: "8px 16px 10px", borderBottom: "1px solid #1e1e1e",
              }}>
                <span style={{ fontSize: 11, color: "#8a9ab0", flexShrink: 0 }}>Game profile:</span>
                <select
                  className="gs-select"
                  style={{ minWidth: 120, flex: "0 1 140px" }}
                  value={selectedGspName}
                  onChange={e => setSelectedGspName(e.target.value)}
                >
                  <option value="">, select ,</option>
                  {gameSettingProfiles.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <button className="btn-revert" style={{ padding: "4px 9px", fontSize: 11 }}
                        disabled={!selectedGspName} onClick={handleGspLoad}>Load</button>
                <button className="btn-revert" style={{ padding: "4px 9px", fontSize: 11,
                                                        borderColor: "#e05252", color: "#e05252" }}
                        disabled={!selectedGspName} onClick={handleGspDelete}>Delete</button>
                <div style={{ display: "flex", gap: 5, flex: 1, minWidth: 140 }}>
                  <input
                    type="text"
                    className="gs-input-text"
                    placeholder="Save as…"
                    style={{ flex: 1, minWidth: 0 }}
                    value={gspInput}
                    onChange={e => setGspInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") handleGspSave(); }}
                  />
                  <button className="btn-optimize" style={{ padding: "4px 10px", fontSize: 11 }}
                          disabled={!gspInput.trim()} onClick={handleGspSave}>Save</button>
                </div>
                {gspMsg && (
                  <span style={{ fontSize: 11, color: "#9a9ab0", width: "100%" }}>{gspMsg}</span>
                )}
              </div>
              <CollapsibleSection title="GREENBOOST OVERRIDES" defaultOpen
                subtitle={`${overrideActiveCount} active · ${overrideDiffKeys.length} differ from recommended`}>
                <div style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  gap: 10, padding: "0 16px 10px", flexWrap: "wrap",
                }}>
                  <span style={{ fontSize: 11, color: "#8a9ab0" }}>
                    {overrideActiveCount} override{overrideActiveCount === 1 ? "" : "s"} active
                    {" · "}{overrideDiffKeys.length} differ{overrideDiffKeys.length === 1 ? "s" : ""} from recommended
                  </span>
                  <button
                    className="btn-optimize"
                    style={{ padding: "4px 10px", fontSize: 11 }}
                    disabled={savingOverrides || overrideDiffKeys.length === 0}
                    onClick={applyAllRecommended}
                    title="Set every recommended field (DLSS Latest, gplasync, Performance Lock, Compositor Suspend, VK Pipeline Cache, GameMode) in one write"
                  >
                    Apply all recommended
                  </button>
                </div>

                <CollapsibleSection title="Rendering"
                  defaultOpen={overrideDiffKeys.includes("dlss_preset")}>
                  {/* DLSS Preset */}
                  <div className="gs-row" style={{ padding: "10px 16px" }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">DLSS Model Version{GS_INFO["DLSS Preset"] && <InfoTip>{GS_INFO["DLSS Preset"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Override global DLSS model preset for this game</div>
                    </div>
                    <div className="gs-row-control" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      {overrideDiffKeys.includes("dlss_preset") && (
                        <RecommendedChip label={dlssPresetLabel(OPTIMAL_OVERRIDES.dlss_preset)}
                          disabled={savingOverrides}
                          onClick={() => saveOverrides({ dlss_preset: OPTIMAL_OVERRIDES.dlss_preset })} />
                      )}
                      <select className="gs-select" value={gameOverrides.dlss_preset}
                              disabled={savingOverrides}
                              onChange={e => saveOverrides({ dlss_preset: e.target.value })}>
                        <option value="">Use Global{globalRuntimeDefaults ? ` (${dlssPresetLabel(globalRuntimeDefaults.dlss_preset)})` : ""}</option>
                        <option value="render_preset_latest">Latest / Recommended (NVIDIA pick)</option>
                        <option value="render_preset_m">Preset M , RTX 40 / 50</option>
                        <option value="render_preset_k">Preset K , RTX 20 / 30</option>
                        <option value="render_preset_l">Preset L , Max sharpness</option>
                        <option value="default">Default (game decides)</option>
                        <option value="off">Off</option>
                      </select>
                    </div>
                  </div>

                  {/* NIS */}
                  <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                       onClick={() => {
                         const nis: GameNisConfig | null = gameOverrides.nis
                           ? null
                           : { enabled: true, sharpness: 0.5, scale: 1.0 };
                         saveOverrides({ nis });
                       }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">NIS (NVIDIA Image Scaling){GS_INFO["NIS (NVIDIA Image Scaling)"] && <InfoTip>{GS_INFO["NIS (NVIDIA Image Scaling)"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Sharpening + upscaling via Vulkan layer</div>
                    </div>
                    <div className="gs-row-control">
                      <div className={`toggle${gameOverrides.nis?.enabled ? " on" : ""}`}>
                        <div className="toggle-track" /><div className="toggle-thumb" />
                      </div>
                    </div>
                  </div>
                  {gameOverrides.nis && (
                    <div style={{ padding: "0 16px 10px 32px", display: "flex", gap: 20 }}>
                      <div>
                        <div className="gs-row-sub" style={{ marginBottom: 4 }}>Sharpness</div>
                        <input type="range" min={0} max={1} step={0.05}
                          value={gameOverrides.nis.sharpness}
                          onChange={e => saveOverrides({ nis: { ...gameOverrides.nis!, sharpness: parseFloat(e.target.value) } })}
                          style={{ width: 140 }} />
                        <span style={{ fontSize: 11, color: "#9a9a9a", marginLeft: 8 }}>
                          {gameOverrides.nis.sharpness.toFixed(2)}
                          {gameOverrides.nis.sharpness === 0 && " (no sharpening)"}
                        </span>
                      </div>
                      <div>
                        <div className="gs-row-sub" style={{ marginBottom: 4 }}>Scale</div>
                        <input type="range" min={0.5} max={1} step={0.05}
                          value={gameOverrides.nis.scale}
                          onChange={e => saveOverrides({ nis: { ...gameOverrides.nis!, scale: parseFloat(e.target.value) } })}
                          style={{ width: 140 }} />
                        <span style={{ fontSize: 11, color: "#9a9a9a", marginLeft: 8 }}>
                          {gameOverrides.nis.scale.toFixed(2)}
                          {gameOverrides.nis.scale >= 1 ? " (no upscale, sharpen only)" : ` (render at ${(gameOverrides.nis.scale * 100).toFixed(0)}%, upscaled back up)`}
                        </span>
                      </div>
                    </div>
                  )}

                  {/* HDR */}
                  <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                       onClick={() => saveOverrides({ hdr: !gameOverrides.hdr })}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">HDR{GS_INFO["HDR"] && <InfoTip>{GS_INFO["HDR"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Enable HDR via ENABLE_HDR_WSI for this game</div>
                    </div>
                    <div className="gs-row-control">
                      <div className={`toggle${gameOverrides.hdr ? " on" : ""}`}>
                        <div className="toggle-track" /><div className="toggle-thumb" />
                      </div>
                    </div>
                  </div>

                  {/* FPS Cap */}
                  <div className="gs-row" style={{ padding: "10px 16px" }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">FPS Cap{GS_INFO["FPS Cap (per-game)"] && <InfoTip>{GS_INFO["FPS Cap (per-game)"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Overrides global FPS cap for this game only</div>
                    </div>
                    <div className="gs-row-control" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <input type="number" className="settings-current-input" style={{ maxWidth: 100 }}
                             min={0} max={500} step={1}
                             value={gameOverrides.fps_cap} disabled={savingOverrides}
                             onChange={e => {
                               const v = parseInt(e.target.value, 10);
                               if (!isNaN(v) && v >= 0) saveOverrides({ fps_cap: v });
                             }} />
                      <span style={{ fontSize: 10, color: "#8a9ab0" }}>
                        {gameOverrides.fps_cap === 0 ? "uncapped" : `${gameOverrides.fps_cap} fps`}
                      </span>
                    </div>
                  </div>
                </CollapsibleSection>

                <CollapsibleSection title="Latency" defaultOpen={false}>
                  {/* Reflex */}
                  <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                       onClick={() => saveOverrides({ reflex: !gameOverrides.reflex })}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">NVIDIA Reflex (lower input lag){GS_INFO["NVIDIA Reflex"] && <InfoTip>{GS_INFO["NVIDIA Reflex"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Reduces latency via VK_NV_low_latency2</div>
                    </div>
                    <div className="gs-row-control">
                      <div className={`toggle${gameOverrides.reflex ? " on" : ""}`}>
                        <div className="toggle-track" /><div className="toggle-thumb" />
                      </div>
                    </div>
                  </div>
                </CollapsibleSection>

                <CollapsibleSection title="System" defaultOpen={false}>
                  {/* CPU Governor */}
                  <div className="gs-row" style={{ padding: "10px 16px" }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">CPU Governor{GS_INFO["CPU Governor"] && <InfoTip>{GS_INFO["CPU Governor"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Overrides global CPU governor for this game</div>
                    </div>
                    <div className="gs-row-control">
                      <select className="gs-select" value={gameOverrides.governor}
                              disabled={savingOverrides}
                              onChange={e => saveOverrides({ governor: e.target.value })}>
                        <option value="">Use Global</option>
                        <option value="performance">performance</option>
                        <option value="powersave">powersave</option>
                        <option value="schedutil">schedutil</option>
                        <option value="ondemand">ondemand</option>
                      </select>
                    </div>
                  </div>

                  {/* GPU Profile */}
                  {savedProfiles.length > 0 && (
                    <div className="gs-row" style={{ padding: "10px 16px" }}>
                      <div className="gs-row-label">
                        <div className="gs-row-title">GPU Profile{GS_INFO["GPU Profile"] && <InfoTip>{GS_INFO["GPU Profile"]}</InfoTip>}</div>
                        <div className="gs-row-sub">Auto-applies OC + fan curve on launch</div>
                      </div>
                      <div className="gs-row-control">
                        <select className="gs-select" value={gameOverrides.gpu_profile}
                                disabled={savingOverrides}
                                onChange={e => saveOverrides({ gpu_profile: e.target.value })}>
                          <option value="">None (manual)</option>
                          {savedProfiles.map(p => (
                            <option key={p} value={p}>{p}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  )}
                </CollapsibleSection>

                <CollapsibleSection title="Runtime Overrides"
                  subtitle="null = use global setting"
                  defaultOpen={(["gplasync", "perf_lock", "compositor_suspend", "vk_pipeline_cache"] as const)
                    .some(k => overrideDiffKeys.includes(k))}>
                  {(
                    [
                      // Labels must match the All Games row they override
                      // verbatim. These used to be the implementation names
                      // (dxvk-gplasync / VK Pipeline Cache), which meant one
                      // setting wore a different name in each tab and the
                      // "use global" tri-state pointed at a row the reader
                      // couldn't find.
                      { key: "gplasync",           label: "Background shader compiling",   desc: "Async pipeline compilation (dxvk-gplasync) , eliminates shader-comp stutter" },
                      { key: "perf_lock",          label: "Performance lock (CPU + GPU)",  desc: "CPU governor + GPU power limit locked for this game" },
                      { key: "compositor_suspend", label: "Pause desktop effects while playing", desc: "Pause KWin/GNOME effects for the duration of the game" },
                      { key: "vk_pipeline_cache",  label: "Remember compiled shaders",     desc: "Persist compiled VkPipelineCache to disk across sessions" },
                    ] as const
                  ).map(({ key, label, desc }) => {
                    const cur = gameOverrides[key] as boolean | null;
                    const globalVal = globalRuntimeDefaults?.[key];
                    const recommended = OPTIMAL_OVERRIDES[key];
                    const differs = cur !== recommended;
                    const triBtn = (val: boolean | null, shown: string) => (
                      <button
                        key={String(val)}
                        disabled={savingOverrides}
                        onClick={() => saveOverrides({ [key]: val })}
                        title={val === null && globalVal != null ? `Resolves to ${globalVal ? "ON" : "OFF"} right now` : undefined}
                        style={{
                          fontSize: 11, padding: "3px 9px",
                          background: cur === val ? (val === true ? "rgba(118,185,0,0.18)" : val === false ? "rgba(248,113,113,0.12)" : "rgba(255,255,255,0.07)") : "transparent",
                          border: `1px solid ${cur === val ? (val === true ? "rgba(118,185,0,0.5)" : val === false ? "rgba(248,113,113,0.4)" : "#444") : "#2a2a2a"}`,
                          color: cur === val ? (val === true ? "#86efac" : val === false ? "#f87171" : "#9a9a9a") : "#6a7a8a",
                          cursor: "pointer", borderRadius: val === null ? "4px 0 0 4px" : val === true ? "0" : "0 4px 4px 0",
                          fontWeight: cur === val ? 600 : 400,
                        }}
                      >{shown}{val === null && globalVal != null ? ` (${globalVal ? "ON" : "OFF"})` : ""}</button>
                    );
                    return (
                      <div key={key} className="gs-row" style={{ padding: "8px 16px", alignItems: "center" }}>
                        <div className="gs-row-label">
                          <div className="gs-row-title">{label}{GS_INFO[label] && <InfoTip>{GS_INFO[label]}</InfoTip>}</div>
                          <div className="gs-row-sub">{desc}</div>
                        </div>
                        <div className="gs-row-control" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          {differs && (
                            <RecommendedChip label={recommended ? "ON" : "OFF"} disabled={savingOverrides}
                              onClick={() => saveOverrides({ [key]: recommended })} />
                          )}
                          <div style={{ display: "flex" }}>
                            {triBtn(null,  "Global")}
                            {triBtn(true,  "ON")}
                            {triBtn(false, "OFF")}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </CollapsibleSection>

                <CollapsibleSection title="Launch Wrappers"
                  defaultOpen={overrideDiffKeys.includes("wrappers")}>
                  <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                       onClick={() => {
                         const w: GameWrappers = { ...(gameOverrides.wrappers ?? defaultWrappers()), gamemode: !(gameOverrides.wrappers?.gamemode ?? false) };
                         saveOverrides({ wrappers: w });
                       }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">GameMode{GS_INFO["GameMode"] && <InfoTip>{GS_INFO["GameMode"]}</InfoTip>}</div>
                      <div className="gs-row-sub">CPU scheduler optimization via gamemoderun</div>
                    </div>
                    <div className="gs-row-control" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      {overrideDiffKeys.includes("wrappers") && (
                        <RecommendedChip label="ON" disabled={savingOverrides}
                          onClick={() => {
                            const w: GameWrappers = { ...(gameOverrides.wrappers ?? defaultWrappers()), gamemode: true };
                            saveOverrides({ wrappers: w });
                          }} />
                      )}
                      <div className={`toggle${gameOverrides.wrappers?.gamemode ? " on" : ""}`}>
                        <div className="toggle-track" /><div className="toggle-thumb" />
                      </div>
                    </div>
                  </div>
                  <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                       onClick={() => {
                         const w: GameWrappers = { ...(gameOverrides.wrappers ?? defaultWrappers()), mangohud: !(gameOverrides.wrappers?.mangohud ?? false) };
                         saveOverrides({ wrappers: w });
                       }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">MangoHUD{GS_INFO["MangoHUD"] && <InfoTip>{GS_INFO["MangoHUD"]}</InfoTip>}</div>
                      <div className="gs-row-sub">FPS / GPU / CPU overlay via mangohud</div>
                    </div>
                    <div className="gs-row-control">
                      <div className={`toggle${gameOverrides.wrappers?.mangohud ? " on" : ""}`}>
                        <div className="toggle-track" /><div className="toggle-thumb" />
                      </div>
                    </div>
                  </div>
                  <div style={{ padding: "8px 16px" }}>
                    <div className="gs-row-title" style={{ marginBottom: 4 }}>
                      Gamescope args <span style={{ fontSize: 10, color: "#8a9ab0", fontWeight: 400 }}>(empty = disabled)</span>
                      {GS_INFO["Gamescope args"] && <InfoTip>{GS_INFO["Gamescope args"]}</InfoTip>}
                    </div>
                    <input
                      type="text"
                      className="settings-current-input"
                      style={{ width: "100%", maxWidth: 400 }}
                      placeholder="-W 1920 -H 1080 -r 120 -f"
                      value={(gameOverrides.wrappers?.gamescope ?? []).join(" ")}
                      disabled={savingOverrides}
                      onBlur={e => {
                        const args = e.target.value.trim().split(/\s+/).filter(Boolean);
                        const w: GameWrappers = { ...(gameOverrides.wrappers ?? defaultWrappers()), gamescope: args };
                        saveOverrides({ wrappers: args.length > 0 || (w.gamemode || w.mangohud) ? w : { ...w, gamescope: [] } });
                      }}
                      onChange={e => {
                        const args = e.target.value.split(/\s+/).filter(Boolean);
                        setGameOverrides(prev => prev ? {
                          ...prev,
                          wrappers: { ...(prev.wrappers ?? defaultWrappers()), gamescope: args }
                        } : prev);
                      }}
                    />
                  </div>
                </CollapsibleSection>

                {overridesMsg && (
                  <p style={{ fontSize: 11, color: "#8a9ab0", padding: "8px 16px 0" }}>
                    {overridesMsg}
                  </p>
                )}
              </CollapsibleSection>
              </>
            )}

            <div className="settings-table-wrap">
              {loadingSettings ? (
                <div className="spinner-row">
                  <span className="animate-spin" style={{ display: "inline-block" }}>
                    <Icon.Refresh />
                  </span>
                  Scanning configuration files…
                </div>
              ) : groups.length === 0 ? (
                <div className="settings-empty">
                  No configurable settings found for this program.
                </div>
              ) : (
                <CollapsibleSection title="IN-GAME SETTINGS" defaultOpen>
                  <div className="settings-table">
                    <div className="settings-group-divider">
                      <span className="settings-col-header">Setting</span>
                      <span className="settings-col-header">Current Value</span>
                      <span className="settings-col-header">Preview Value (Recommended)</span>
                    </div>

                    {groups.map((group, gi) => (
                      <div key={gi}>
                        {gi > 0 && (
                          <span className="settings-group-title">{group.title}</span>
                        )}
                        {group.settings.map((s, si) => {
                          const id = `${group.title}::${s.key}`;
                          const original = s.current === "Not Set" ? "" : s.current;
                          const draft = editValues[id] ?? original;
                          const isSaving = savingKey === id;

                          const commit = async () => {
                            if (draft === original) return;
                            setSavingKey(id);
                            try {
                              await invoke("set_game_setting", {
                                path: selected!.path, key: s.key, value: draft,
                              });
                              await loadSettings(selected!);
                              setEditValues(v => {
                                const n = { ...v }; delete n[id]; return n;
                              });
                            } catch (e) { console.error(e); }
                            setSavingKey(null);
                          };

                          return (
                            <div key={si} className="settings-row">
                              <span className="settings-key">{s.display}</span>
                              <input
                                className={`settings-current-input${s.needs_change ? " differs" : ""}`}
                                value={draft}
                                placeholder={s.current === "Not Set" ? "Not Set" : ""}
                                disabled={isSaving}
                                onFocus={(e) => e.currentTarget.select()}
                                onChange={(e) =>
                                  setEditValues(v => ({ ...v, [id]: e.target.value }))
                                }
                                onBlur={commit}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") e.currentTarget.blur();
                                  if (e.key === "Escape") {
                                    setEditValues(v => {
                                      const n = { ...v }; delete n[id]; return n;
                                    });
                                    e.currentTarget.blur();
                                  }
                                }}
                              />
                              <span className={`settings-recommended${s.needs_change ? " differs" : ""}`}>
                                {s.recommended}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                </CollapsibleSection>
              )}
            </div>
            </div>
          </>
        )}
      </div>

      {dlssModal && <InstallStreamModal {...dlssModal} />}

      {summary && (
        <ChangeSummaryModal
          title={summary.title}
          subtitle={summary.subtitle}
          changes={summary.changes}
          notes={summary.notes}
          unchangedMessage={summary.unchanged}
          onClose={() => setSummary(null)}
        />
      )}
    </div>
      )}
    </>
  );
}
