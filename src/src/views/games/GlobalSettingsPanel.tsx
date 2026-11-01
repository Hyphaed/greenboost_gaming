import { useState, useEffect, useCallback, Children } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { InstallStreamProps, GlobalSettingsState, AutoTuneResult } from "../../types";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { InstallStreamModal } from "../../components/InstallStreamModal";
import { Toggle } from "../../components/Toggle";
import { InfoTip } from "../../components/InfoTip";
import { RowSub } from "../../components/RowSub";
import { DLL_ORDER, DLL_TYPE, DLL_EXPLAIN } from "../../dllInfo";
import { GS_INFO, GS_BENEFIT, GS_ADDED_BY_GB } from "../../gsHelp";
import { GB_DETAIL, GB_AUTOMATIC } from "../../gbFeatures";
import { ChangeSummaryModal } from "../../components/ChangeSummaryModal";
import { summarizeGlobal, type Change } from "../../changeSummary";
import { globalSettings, useGlobalSettings, loadGlobalSettings, patchGlobalSettings, replaceGlobalSettings } from "../../store/globalSettings";
import { VULKAN_LAYER_VISIBLE } from "../../featureFlags";

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
      <CollapsibleSection title="Downloaded DLSS libraries" defaultOpen={false}>
        <div style={{ padding: "0 16px 8px", display: "flex", alignItems: "center",
                      justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontSize: 12, color: "var(--color-text-dim)", lineHeight: 1.4 }}>
            Source: <span style={{ color: "var(--color-gb)", fontWeight: 600 }}>NVIDIA GitHub (official)</span>
            <span style={{ marginLeft: 8, fontSize: 12, color: "var(--color-text-muted)" }}>
              NVIDIA/DLSS · NVIDIAGameWorks/Streamline
            </span>
          </div>
          <button className="btn-component" onClick={fetchLatest} disabled={loading}
                  style={{ whiteSpace: "nowrap", fontSize: 12 }}>
            Fetch Latest
          </button>
        </div>
        {msg && <p style={{ fontSize: 12, color: "var(--color-gb)", margin: "0 16px 8px" }}>{msg}</p>}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "var(--color-text)" }}>
          <thead>
            <tr style={{ background: "#141414" }}>
              <th style={{ padding: "6px 16px", textAlign: "left", fontWeight: 600, fontSize: 12,
                           color: "#9a9ab0", textTransform: "uppercase", letterSpacing: "0.04em" }}>Library</th>
              <th style={{ padding: "6px 16px", textAlign: "left", fontWeight: 600, fontSize: 12,
                           color: "#9a9ab0", textTransform: "uppercase", letterSpacing: "0.04em" }}>Type</th>
              <th style={{ padding: "6px 16px", textAlign: "right", fontWeight: 600, fontSize: 12,
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
                    <code style={{ fontSize: 12 }}>{name}</code>
                    {DLL_EXPLAIN[name] && <InfoTip>{DLL_EXPLAIN[name]}</InfoTip>}
                  </td>
                  <td style={{ padding: "8px 16px", color: "var(--color-text-dim)" }}>{DLL_TYPE[name]}</td>
                  <td style={{ padding: "8px 16px", textAlign: "right", fontFamily: "monospace" }}>
                    {d ? (
                      <>
                        <span style={{ color: "var(--color-gb)" }}>{d.version}</span>
                        {versions.length > 1 && (
                          <span style={{ color: "var(--color-text-muted)", fontFamily: "inherit", marginLeft: 6 }}>
                            (+{versions.length - 1} older cached , pick a version per-game)
                          </span>
                        )}
                      </>
                    ) : (
                      <span style={{ color: "var(--color-text-muted)" }}>not cached</span>
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
            <div style={{ fontSize: 12, color: "var(--color-text-dim)", lineHeight: 1.4 }}>
              <span style={{ color: "var(--color-text)", fontWeight: 600 }}>Version pinning</span>
              <span style={{ marginLeft: 8, fontSize: 12, color: "var(--color-text-muted)" }}>
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
                    DLSS <code style={{ fontSize: 12, color: "#5a7a9a" }}>nvngx_*.dll</code>
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
                    Streamline <code style={{ fontSize: 12, color: "#5a7a9a" }}>sl.*.dll</code>
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
                  <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
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
            <p style={{ fontSize: 12, color: "var(--color-text-muted)", margin: "10px 0 0" }}>{verMsg}</p>
          )}
        </div>

        <div style={{ padding: "8px 16px", borderTop: "1px solid #1e1e1e", fontSize: 12, color: "var(--color-text-muted)" }}>
          Versions from <code>~/.local/share/greenboost-gaming/libraries/</code>. Click <b>Fetch Latest</b> to sync from NVIDIA GitHub.
        </div>
      </CollapsibleSection>
      {modal && <InstallStreamModal {...modal} />}
    </>
  );
}

export function GlobalSettingsPanel() {
  // One shared copy for the whole app , see store/globalSettings.ts. This
  // panel used to hold its own, which is why About/Profile/the launch
  // handler could all disagree with it.
  const state = useGlobalSettings();
  const [presets, setPresets] = useState<DlssPresetChoice[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [gsProfiles, setGsProfiles] = useState<string[]>([]);
  // "__smart" is not a saved profile , it's the always-present anchor entry
  // that replaces the old blank ", select ," placeholder. Read-only, same
  // as Profile.tsx's own "Default" fan-curve entry: Load/Delete stay
  // disabled while it's selected, there is nothing saved to load or delete.
  const SMART_DEFAULTS_ID = "__smart";
  const [selectedGsProfile, setSelectedGsProfile] = useState<string>(SMART_DEFAULTS_ID);
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
    if (!selectedGsProfile || selectedGsProfile === SMART_DEFAULTS_ID) return;
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
    if (!selectedGsProfile || selectedGsProfile === SMART_DEFAULTS_ID) return;
    try {
      await invoke("delete_gs_profile", { name: selectedGsProfile });
      setGsProfileMsg(`Deleted "${selectedGsProfile}".`);
      setSelectedGsProfile(SMART_DEFAULTS_ID);
      reloadGsProfiles();
    } catch (e: any) {
      setGsProfileMsg("Delete failed: " + (e?.message ?? e));
    }
  };

  if (!state) {
    return (
      <div className="content-scroll">
        <p style={{ color: "var(--color-text-dim)", padding: 24 }}>Loading global settings…</p>
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
                  <div style={{ fontSize: 12, color: "var(--color-text-muted)", textTransform: "uppercase",
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
                fontSize: 12, fontWeight: 600, lineHeight: 1.6,
                color: "var(--color-gb)",
                background: "rgba(118,185,0,0.12)",
                border: "1px solid rgba(118,185,0,0.35)",
                borderRadius: 4,
                verticalAlign: "middle", whiteSpace: "nowrap",
              }}>
              GreenBoost
            </span>
          )}
        </div>
        <RowSub benefit={benefit} sub={sub} forceOpen={filtering} />
        {locked && (
          <div style={{ fontSize: 12, color: "var(--color-amber)", marginTop: 3 }}>
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
          <div style={{ padding: "0 16px 10px", fontSize: 12, color: "var(--color-text-muted)" }}>{note}</div>
        )}
        {kids}
      </CollapsibleSection>
    );
  };

  const toggle = (on: boolean, onClick: () => void, label?: string) => (
    <Toggle on={on} onChange={() => onClick()} label={label} />
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
          <span style={{ fontSize: 12, color: "var(--color-text)" }}>
            Picks the right settings automatically for your specific graphics card
          </span>
        </div>
      </div>

      {/* Settings search + GreenBoost-extras filter */}
      <div className="gs-toolbar">
        <input
          type="search"
          className="gs-input-text"
          aria-label="Search settings"
          placeholder="Search settings, or describe a problem: stutter, VRAM, overlay…"
          style={{ flex: 1, minWidth: 200 }}
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="seg" role="group" aria-label="Which settings to show">
          <button type="button" className={`seg-btn${!gbOnly ? " on" : ""}`} aria-pressed={!gbOnly} onClick={() => setGbOnly(false)}>All settings</button>
          <button type="button" className={`seg-btn${gbOnly ? " on" : ""}`} aria-pressed={gbOnly} onClick={() => setGbOnly(true)}
                  title="Only what GreenBoost adds on top of a stock Linux install">GreenBoost extras</button>
        </div>
        <button
          hidden
          onClick={() => setGbOnly(v => !v)}
          title={"Show only settings that don't exist in a stock Linux/NVIDIA "
               + "install. Each one's (i) explains what it does, why the gap "
               + "exists on Linux, and how to check it yourself."}
          style={{
            fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: 4,
            cursor: "pointer", whiteSpace: "nowrap",
            background: gbOnly ? "rgba(118,185,0,0.18)" : "rgba(255,255,255,0.05)",
            border: `1px solid ${gbOnly ? "rgba(118,185,0,0.5)" : "#333"}`,
            color: gbOnly ? "#76b900" : "#8a9ab0",
          }}
        >
          GreenBoost extras only
        </button>
        {q.length > 0 && (
          <button
            onClick={() => { setQuery(""); }}
            style={{
              fontSize: 12, padding: "5px 10px", borderRadius: 4, cursor: "pointer",
              background: "transparent", border: "1px solid #333", color: "var(--color-text-muted)",
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
        <span style={{ fontSize: 12, color: "#b3b3b3", flexShrink: 0 }}>Saved setup</span>
        <select
          className="gs-select"
          aria-label="Saved setup"
          style={{ minWidth: 200, flex: "0 1 240px" }}
          value={selectedGsProfile}
          onChange={e => setSelectedGsProfile(e.target.value)}
        >
          <option value={SMART_DEFAULTS_ID}>Smart Defaults (recommended)</option>
          {gsProfiles.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <button className="btn-revert" style={{ padding: "5px 10px", fontSize: 12 }}
                disabled={!selectedGsProfile || selectedGsProfile === SMART_DEFAULTS_ID}
                onClick={handleGsProfileLoad}>
          Apply
        </button>
        <button className="btn-revert" style={{ padding: "5px 10px", fontSize: 12,
                                                borderColor: "var(--color-red)", color: "var(--color-text-error)" }}
                disabled={!selectedGsProfile || selectedGsProfile === SMART_DEFAULTS_ID}
                onClick={handleGsProfileDelete}>
          Delete
        </button>
        <div style={{ display: "flex", gap: 6, flex: 1, minWidth: 160 }}>
          <input
            type="text"
            className="gs-input-text"
            placeholder="Name these settings to save them…"
            aria-label="Name for the saved setup"
            style={{ flex: 1, minWidth: 0 }}
            value={gsProfileInput}
            onChange={e => setGsProfileInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") handleGsProfileSave(); }}
          />
          <button className="btn-optimize" style={{ padding: "5px 12px", fontSize: 12 }}
                  disabled={!gsProfileInput.trim()} onClick={handleGsProfileSave}>
            Save setup
          </button>
        </div>
        {gsProfileMsg && (
          <span style={{ fontSize: 12, color: "#9a9ab0", width: "100%" }}>{gsProfileMsg}</span>
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

        {VULKAN_LAYER_VISIBLE && row("Remember compiled shaders",
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
             "KDE Plasma only: pauses the desktop compositor while the game "
           + "runs, then resumes it when you quit. On GNOME it does nothing "
           + "inside Steam's container; use \"Turn off GNOME extensions\" "
           + "under While you play instead.",
             toggle(state.compositor_suspend,
                    () => update({ compositor_suspend: !state.compositor_suspend })),
             GS_BENEFIT["Pause desktop effects while playing"], undefined, GS_INFO["Pause desktop effects while playing"])}

        {VULKAN_LAYER_VISIBLE && row("Give the game GPU priority",
             "Tells the graphics driver the game's rendering work matters "
           + "more than background apps (your desktop effects, browser, "
           + "etc.), so the game gets first access to the GPU.",
             toggle(state.vk_queue_priority,
                    () => update({ vk_queue_priority: !state.vk_queue_priority })),
             GS_BENEFIT["Give the game GPU priority"], undefined, GS_INFO["Give the game GPU priority"])}

        {VULKAN_LAYER_VISIBLE && row("Shader compile threads",
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
             "Turns HDR on for your monitor, desktop-wide, when you save "
           + "(it doesn't change how any single game renders). Only use it "
           + "with an HDR-capable monitor, otherwise colors can look washed out.",
             toggle(state.hdr, () => update({ hdr: !state.hdr })),
             GS_BENEFIT["HDR (High Dynamic Range)"], undefined, GS_INFO["HDR (High Dynamic Range)"])}

        {VULKAN_LAYER_VISIBLE && row("NIS sharpening , ready to use",
             "Prepares NVIDIA Image Scaling so it's ready to go, without "
           + "turning the effect on yet. Turn this on first if you plan to "
           + "actually use NIS sharpening below.",
             toggle(state.nis_enable,
                    () => update({ nis_enable: !state.nis_enable })),
             GS_BENEFIT["NIS sharpening , ready to use"], undefined, GS_INFO["NIS sharpening , ready to use"])}

        {VULKAN_LAYER_VISIBLE && row("NIS sharpening , actually apply it",
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

        {VULKAN_LAYER_VISIBLE && row("Sharpness",
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

        {VULKAN_LAYER_VISIBLE && row("Upscale ratio",
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
        {VULKAN_LAYER_VISIBLE && row("NVIDIA Reflex (lower input lag)",
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
        {VULKAN_LAYER_VISIBLE && row("VRAM headroom before overflow (MB)",
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

        {VULKAN_LAYER_VISIBLE && row("Minimum reserved disk space (MB)",
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

      </FilterSection>

      <FilterSection title="WHILE YOU PLAY" defaultOpen>
        {row("Game mode",
             "Quiets the desktop for the length of a game and puts it back "
           + "exactly as it was when you quit, even if the game crashes.",
             toggle(state.game_mode, () => update({ game_mode: !state.game_mode })))}

        {row("Turn off GNOME extensions while playing",
             "Extensions such as app indicators wake up now and then and "
           + "can stall a game for a moment. They're switched back on when "
           + "the game closes. Extensions you had already turned off stay off.",
             toggle(state.game_mode && state.game_mode_pause_extensions,
                    () => update({ game_mode_pause_extensions: !state.game_mode_pause_extensions })))}

        {row("Keep the screen awake while playing",
             "Stops the screen from blanking or locking mid-race.",
             toggle(state.game_mode && state.game_mode_keep_awake,
                    () => update({ game_mode_keep_awake: !state.game_mode_keep_awake })))}
      </FilterSection>

      <FilterSection title="DISPLAY & SESSION" defaultOpen={false}>
        {row("Detected GPU",
             state.detected_series ? `Series: ${state.detected_series}` : null,
             <span className="gs-value-readonly">{state.detected_gpu || ","}</span>)}

        {row("Session",
             "Auto-detected from your desktop environment",
             <span className="gs-value-readonly">{state.detected_session || ","}</span>)}

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

      <FilterSection title="Gaming alongside local AI" defaultOpen={false}>
        {VULKAN_LAYER_VISIBLE && row("Protect game memory under pressure",
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

        {VULKAN_LAYER_VISIBLE && row("Verbose Vulkan logging",
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
                         fontSize: 12, padding: "2px 8px", borderRadius: 4,
                         cursor: "pointer",
                         background: active ? "rgba(118,185,0,0.15)" : "rgba(255,255,255,0.05)",
                         border: `1px solid ${active ? "rgba(118,185,0,0.4)" : "#333"}`,
                         color: active ? "#76b900" : "#8a9ab0",
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
          <span style={{ fontSize: 12, color: "var(--color-text-muted)", textAlign: "right",
                         display: "block", maxWidth: 190 }}>
            {f.noSwitch}
          </span>,
          f.tagline,
        ))}
      </FilterSection>

      <DlssLibrarySection />

      {msg && <p style={{ fontSize: 12, color: "var(--color-gb)",
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

