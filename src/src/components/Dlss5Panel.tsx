import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Game, GameOverrides, PreflightCheck, Dlss5EnableResult, Dlss5Config,
  Dlss5SimpleResult, ExternalDll, FileResult } from "../types";
import { CollapsibleSection } from "./CollapsibleSection";
import { InfoTip } from "./InfoTip";
import { Dlss5FilesModal } from "./Dlss5FilesModal";
import { FolderBrowserModal } from "./FolderBrowserModal";
import { GS_INFO } from "../gsHelp";
import { Icon } from "../icons";

// Top-level "This Game" section for DLSS 5 (Neural Rendering). Was nested
// three levels deep inside GREENBOOST OVERRIDES (Games.tsx); promoted to a
// sibling of it 2026-09-02 so the feature is visible without two
// expansions. Everything here was previously inline in Games.tsx , moved
// out because that file already held three separate components at 3300+
// lines.
//
// Owns the whole flow: resolving where to install (the game's real exe
// directory, not always the Steam install root , see
// resolve_game_exe_dir_impl's doc comment in dlss5.rs), the enable/disable
// toggle (which installs/uninstalls GreenBoost's own DLSS Neural Rendering
// runtime via gb_gaming.dlss5.enable_for_game()/disable_for_game()), the
// preflight checklist, and the Neural Rendering + adjacent-feature config
// panel (gb_gaming.dlss5.read_config()/write_config()) once enabled.
//
// The former standalone "External DLLs" section (general Streamline/DLSS
// DLL-swap overlay , sl.*.dll, nvngx_*.dll, unrelated to the Neural
// Rendering runtime itself) merged in here 2026-09-02, at the owner's
// request: a Streamline bundle folder naturally holds BOTH
// nvngx_dlssnr.dll (what DLSS 5 needs) and the SR/FG/Reflex DLLs (what the
// overlay places), so one folder pick can drive both instead of two
// separate folder pickers. "Select DLSS5 folder" now does double duty ,
// see pickAssetDir() , and the discovered-DLL list / copy-in / restore
// controls live in the "Advanced" sub-section below the config panel. The
// reusable named-folder library the old section also had
// (list_dll_folders/save_dll_folders, ~/.config/greenboost-gaming/
// dll-folders.json) was removed with it , a single button superseded it.

const RANGE_MIN = -1;
const RANGE_MAX = 4;
const RANGE_STEP = 0.05;

/** A float config knob: "auto" (the runtime's own default) or a number,
 *  never coerced between the two. The "auto" pill both shows the current
 *  state and toggles it , clicking it while live seeds a starting number
 *  (the ini's own documented default) rather than leaving the slider at 0,
 *  which the ini comments explicitly warn is not the same as "unset". */
function Dlss5Slider({
  label, value, seed, onCommit, disabled, info,
  min = RANGE_MIN, max = RANGE_MAX, step = RANGE_STEP,
}: {
  label: string;
  value: string | undefined;
  seed: number;
  onCommit: (v: string) => void;
  disabled: boolean;
  info?: React.ReactNode;
  min?: number; max?: number; step?: number;
}) {
  const isAuto = value === undefined || value === "auto";
  const numeric = isAuto ? seed : (parseFloat(value!) || 0);
  const [live, setLive] = useState(numeric);
  useEffect(() => { setLive(numeric); }, [numeric]);

  return (
    <div className="gs-row" style={{ padding: "6px 16px" }}>
      <div className="gs-row-label">
        <div className="gs-row-title" style={{ fontSize: 12 }}>{label}{info && <InfoTip>{info}</InfoTip>}</div>
      </div>
      <div className="gs-row-control" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          type="range" min={min} max={max} step={step}
          value={live}
          disabled={disabled || isAuto}
          onChange={e => setLive(parseFloat(e.target.value))}
          onMouseUp={() => onCommit(String(live))}
          onTouchEnd={() => onCommit(String(live))}
          style={{ width: 110 }}
        />
        <span style={{ width: 42, textAlign: "right", fontSize: 11, color: "#c8d0da" }}>
          {isAuto ? "auto" : live.toFixed(2)}
        </span>
        <button className="btn-revert" style={{ padding: "2px 7px", fontSize: 10 }}
          disabled={disabled}
          onClick={() => onCommit(isAuto ? String(seed) : "auto")}>
          {isAuto ? "set" : "auto"}
        </button>
      </div>
    </div>
  );
}

function Dlss5Toggle({ label, value, onCommit, disabled, info }: {
  label: string; value: string | undefined; onCommit: (v: string) => void; disabled: boolean;
  info?: React.ReactNode;
}) {
  const on = value === "true";
  return (
    <div className="gs-row" style={{ padding: "6px 16px" }}>
      <div className="gs-row-label">
        <div className="gs-row-title" style={{ fontSize: 12 }}>{label}{info && <InfoTip>{info}</InfoTip>}</div>
      </div>
      <div className="gs-row-control">
        <div className={`toggle${on ? " on" : ""}`}
          onClick={disabled ? undefined : () => onCommit(on ? "false" : "true")}
          style={{ cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1 }}>
          <div className="toggle-track" /><div className="toggle-thumb" />
        </div>
      </div>
    </div>
  );
}

function Dlss5Select({ label, value, options, onCommit, disabled, note, info }: {
  label: string; value: string | undefined;
  options: { v: string; label: string }[];
  onCommit: (v: string) => void; disabled: boolean; note?: string;
  info?: React.ReactNode;
}) {
  return (
    <div className="gs-row" style={{ padding: "6px 16px" }}>
      <div className="gs-row-label">
        <div className="gs-row-title" style={{ fontSize: 12 }}>{label}{info && <InfoTip>{info}</InfoTip>}</div>
        {note && <div className="gs-row-sub">{note}</div>}
      </div>
      <div className="gs-row-control">
        <select className="gs-select" disabled={disabled}
          value={value ?? "auto"}
          onChange={e => onCommit(e.target.value)}>
          <option value="auto">auto</option>
          {options.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
        </select>
      </div>
    </div>
  );
}

function Dlss5HowItWorksModal({ onClose }: { onClose: () => void }) {
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
          width: 520, maxWidth: "94vw", maxHeight: "80vh", overflowY: "auto",
          background: "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 10,
          boxShadow: "0 14px 40px rgba(0,0,0,0.6)", padding: "18px 20px",
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ fontWeight: 600, fontSize: 14, color: "#e6e6e6", marginBottom: 10 }}>
          How DLSS 5 (Neural Rendering) works in GreenBoost
        </div>
        <div style={{ fontSize: 12, color: "#c8d0da", lineHeight: 1.6, display: "flex",
          flexDirection: "column", gap: 10 }}>
          <p>
            GreenBoost installs its own DLSS Neural Rendering runtime (built by this
            project, ~3 MB) into the game's own directory as a DLL proxy, and turns
            its Neural Rendering pass on directly , no in-game overlay, no separate
            install step for the runtime itself.
          </p>
          <p>
            <strong>One file you have to supply yourself: <code>nvngx_dlssnr.dll</code></strong>
            , NVIDIA's own Neural Rendering model weights. GreenBoost never downloads,
            bundles, or redistributes it, the same as every other NVIDIA DLL this Suite
            handles. You need to already have a copy (from a driver package or SDK that
            ships it) and point GreenBoost at the folder that contains it.
          </p>
          <p>
            Use <strong>Select DLSS5 folder</strong> to tell GreenBoost exactly where
            that folder is , it checks that folder for <code>nvngx_dlssnr.dll</code>
            every time you open this panel. The same folder also feeds the
            <strong> Advanced</strong> section below: if it also holds the regular
            Streamline SR/FG/Reflex DLLs (<code>sl.*.dll</code>, <code>nvngx_dlss*.dll</code>),
            those get offered for the game's own DLL overlay too , one folder, both
            mechanisms.
          </p>
          <p>
            Needs an RTX 50-series GPU , the runtime's own Neural Rendering pass
            does not run on anything older.
          </p>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn-revert" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

export function Dlss5Panel({
  game, appid, gameOverrides, saveOverrides,
}: {
  game: Game;
  appid: string;
  gameOverrides: GameOverrides;
  saveOverrides: (patch: Partial<GameOverrides>) => Promise<void>;
}) {
  const [targetDir, setTargetDir] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<PreflightCheck[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showFilesModal, setShowFilesModal] = useState(false);
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [browsingAssetDir, setBrowsingAssetDir] = useState(false);

  const [config, setConfig] = useState<Dlss5Config>({});
  const [configBusy, setConfigBusy] = useState(false);

  // ── DLL overlay (formerly the standalone "External DLLs" section) ────
  const [inspected, setInspected]   = useState<ExternalDll[]>([]);
  const [inspecting, setInspecting] = useState(false);
  const [copyBusy, setCopyBusy]     = useState(false);
  const [copyMsg, setCopyMsg]       = useState<string | null>(null);

  useEffect(() => {
    invoke<string | null>("resolve_game_exe_dir", { gameDir: game.path })
      .then(setTargetDir)
      .catch(() => setTargetDir(null));
  }, [game.path]);

  const runPreflight = useCallback(() => {
    if (!targetDir || !appid) { setPreflight([]); return; }
    invoke<PreflightCheck[]>("dlss5_preflight", { gameDir: targetDir, appid })
      .then(setPreflight).catch(() => setPreflight([]));
  }, [targetDir, appid]);

  useEffect(() => { runPreflight(); }, [runPreflight]);

  const loadConfig = useCallback(() => {
    if (!targetDir || !gameOverrides.dlss5_enabled) { setConfig({}); return; }
    invoke<Dlss5Config>("dlss5_get_config", { gameDir: targetDir })
      .then(setConfig).catch(() => setConfig({}));
  }, [targetDir, gameOverrides.dlss5_enabled]);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const commitConfig = useCallback(async (key: string, value: string) => {
    if (!targetDir) return;
    setConfig(prev => ({ ...prev, [key]: value }));
    setConfigBusy(true);
    try {
      const result = await invoke<Dlss5SimpleResult>("dlss5_set_config", {
        gameDir: targetDir, changes: { [key]: value },
      });
      // A rejected `ok: false` never throws , write_config() (dlss5.py)
      // returns it as a normal successful result (e.g. "GreenBoostDLSS5.ini
      // not found", the exact symptom of a game still on the pre-migration
      // OptiScaler.ini install , see the "Runtime is up to date" preflight
      // check above). Missing this case is what let a setting look like it
      // took, then silently revert with no explanation.
      if (!result.ok) {
        setMsg(result.msg);
        loadConfig();
      }
    } catch (e: any) {
      setMsg("Could not save setting: " + (e?.message ?? e));
      loadConfig(); // revert optimistic update on failure
    }
    setConfigBusy(false);
  }, [targetDir, loadConfig]);

  // Frame Generation source and output backend are two separate ini keys,
  // but with AMD/Intel code removed from this fork (see FORK_NOTES.md) only
  // one output backend is left standing: DLSS-G. Keeping FGOutput as its own
  // dropdown next to FGInput would mean asking the user a question with one
  // real answer. Instead the panel owns the pairing: any source other than
  // "off" always writes FGOutput=dlssg alongside it, and the toggle below
  // seeds a working source (native DLSS-G) the first time it's switched on
  // rather than leaving Frame Generation "enabled" with no source picked ,
  // silently equivalent to off.
  const commitFgSource = useCallback(async (v: string) => {
    await commitConfig("FrameGen.FGInput", v);
    const off = v === "nofg" || v === "auto";
    await commitConfig("FrameGen.FGOutput", off ? "nofg" : "dlssg");
  }, [commitConfig]);

  // No auto-seeding here on purpose , see the warning on this toggle below.
  // This used to also pick "Native" (dlssg) the first time Frame Generation
  // was switched on; real testing on 007 First Light showed Frame
  // Generation forced on alongside Neural Rendering produces pink/magenta
  // image corruption, not the "everything works from one toggle" result
  // this was meant to be. Left as a plain pass-through , picking a source
  // is now a separate, deliberate choice via the Source control below.
  const commitFrameGenEnabled = useCallback(async (v: string) => {
    await commitConfig("FrameGen.Enabled", v);
  }, [commitConfig]);

  // No auto-seeding here on purpose , see the warning on the toggle below.
  // This used to also pick Preset M the first time the override was turned
  // on; real testing on 007 First Light showed that combination (a forced
  // non-default SR preset + Neural Rendering, both on) produces severe
  // image corruption, not the ghosting fix it was meant to be. Left as a
  // plain pass-through so a user who wants to experiment picks a preset
  // deliberately, with the warning already read.
  const commitDlssPresetOverride = useCallback(async (v: string) => {
    await commitConfig("DLSS.RenderPresetOverride", v);
  }, [commitConfig]);

  const doEnable = useCallback(async () => {
    if (!appid || !targetDir) return;
    setShowFilesModal(false);
    setBusy(true);
    setMsg(null);
    try {
      const result = await invoke<Dlss5EnableResult>("dlss5_enable", { gameDir: targetDir, appid });
      if (!result.ok) { setMsg(result.msg); setBusy(false); return; }
      await saveOverrides({ dlss5_enabled: true, dlss5_proxy_dll: result.proxy_dll });
      setMsg(result.msg);
    } catch (e: any) {
      setMsg("DLSS 5 enable failed: " + (e?.message ?? e));
    }
    runPreflight();
    setBusy(false);
  }, [appid, targetDir, saveOverrides, runPreflight]);

  const toggle = useCallback(async () => {
    if (!appid || !targetDir) return;
    if (!gameOverrides.dlss5_enabled) {
      setShowFilesModal(true);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const result = await invoke<Dlss5EnableResult>("dlss5_disable", { gameDir: targetDir, appid });
      await saveOverrides({ dlss5_enabled: false, dlss5_proxy_dll: "" });
      setMsg(result.msg);
    } catch (e: any) {
      setMsg("DLSS 5 disable failed: " + (e?.message ?? e));
    }
    runPreflight();
    setBusy(false);
  }, [gameOverrides.dlss5_enabled, appid, targetDir, saveOverrides, runPreflight]);

  // Directory each already-discovered DLL name lives in, one entry per name
  // found by the scanner , the map _external_dll_plan() (Proton wrapper)
  // and install_external_folder_into_game() (copy-in) both read.
  const externalDllTargets = useCallback((): Record<string, string[]> => {
    const out: Record<string, string[]> = {};
    for (const dll of game.dlls) {
      const dir = dll.path.slice(0, Math.max(dll.path.lastIndexOf("/"), 0));
      if (!dir) continue;
      const key = dll.name.toLowerCase();
      if (!out[key]) out[key] = [];
      if (!out[key].includes(dir)) out[key].push(dir);
    }
    return out;
  }, [game]);

  // GameOverrides.external_dll_targets defaults to {} via serde, not null,
  // so `stored ?? computed` never falls through , an empty stored map from
  // an older profile (or one saved before the scanner had run) would keep
  // copy-in and restore pinned to the install root forever. Fall through on
  // emptiness, not nullishness.
  const effectiveTargets = useCallback((): Record<string, string[]> => {
    const stored = gameOverrides.external_dll_targets;
    return stored && Object.keys(stored).length > 0 ? stored : externalDllTargets();
  }, [gameOverrides.external_dll_targets, externalDllTargets]);

  useEffect(() => {
    const dir = gameOverrides.external_dll_dir;
    if (!dir) { setInspected([]); return; }
    setInspecting(true);
    invoke<ExternalDll[]>("inspect_dll_folder", { path: dir })
      .then(setInspected)
      .catch(() => setInspected([]))
      .finally(() => setInspecting(false));
  }, [gameOverrides.external_dll_dir]);

  // One button now drives both mechanisms: the DLSS5 asset folder
  // (nvngx_dlssnr.dll, global , shared across every game) AND, for the
  // currently selected game, the DLL overlay's source folder + computed
  // targets (sl.*.dll / nvngx_dlss*.dll, per-game). A real Streamline
  // bundle naturally has both kinds of file side by side.
  const pickAssetDir = useCallback(async (path: string) => {
    setBrowsingAssetDir(false);
    try {
      await invoke("dlss5_set_asset_dir", { path });
      await saveOverrides({
        external_dll_dir: path,
        external_dlls_enabled: true,
        external_dll_targets: externalDllTargets(),
      });
      runPreflight();
    } catch (e: any) {
      setMsg("Could not set DLSS5 folder: " + (e?.message ?? e));
    }
  }, [saveOverrides, externalDllTargets, runPreflight]);

  const toggleDllEnabled = useCallback((name: string) => {
    const key = name.toLowerCase();
    const cur = gameOverrides.external_dll_disabled ?? [];
    const next = cur.includes(key) ? cur.filter(n => n !== key) : [...cur, key];
    saveOverrides({ external_dll_disabled: next });
  }, [gameOverrides.external_dll_disabled, saveOverrides]);

  const summarizeFileResults = (results: FileResult[]): string => {
    const ok = results.filter(r => r.ok);
    const failed = results.filter(r => !r.ok);
    const added = ok.filter(r => r.added).length;
    const replaced = ok.length - added;
    const parts: string[] = [];
    if (replaced > 0) parts.push(`${replaced} replaced`);
    if (added > 0) parts.push(`${added} added`);
    if (failed.length > 0) parts.push(`${failed.length} failed`);
    return parts.length > 0 ? parts.join(", ") : (results[0]?.msg ?? "no files found");
  };

  const handleCopyIntoGame = async () => {
    if (!gameOverrides.external_dll_dir) return;
    setCopyBusy(true);
    setCopyMsg(null);
    try {
      const results = await invoke<FileResult[]>("install_external_folder", {
        srcDir: gameOverrides.external_dll_dir,
        gamePath: game.path,
        targets: effectiveTargets(),
        disabled: gameOverrides.external_dll_disabled ?? [],
      });
      setCopyMsg("Copy into game: " + summarizeFileResults(results));
    } catch (e: any) {
      setCopyMsg("Copy failed: " + (e?.message ?? e));
    }
    setCopyBusy(false);
  };

  const handleRestoreExternal = async () => {
    setCopyBusy(true);
    setCopyMsg(null);
    try {
      const results = await invoke<FileResult[]>("restore_external_folder", {
        gamePath: game.path,
        targets: effectiveTargets(),
      });
      setCopyMsg(results[0]?.msg ?? "Restored.");
    } catch (e: any) {
      setCopyMsg("Restore failed: " + (e?.message ?? e));
    }
    setCopyBusy(false);
  };

  const enabled = gameOverrides.dlss5_enabled;
  const cfgDisabled = !enabled || configBusy;

  return (
    <>
      <CollapsibleSection title="DLSS 5 (Neural Rendering)" defaultOpen={enabled}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          gap: 10, padding: "8px 16px 0", flexWrap: "wrap",
        }}>
          <p style={{ fontSize: 11, color: "#8a9ab0", lineHeight: 1.5, margin: 0, flex: "1 1 260px" }}>
            Installs GreenBoost's own DLSS Neural Rendering runtime and turns it on
            directly , no in-game menu to open, no checkbox to
            find.{GS_INFO["DLSS 5 (Neural Rendering)"] &&
              <InfoTip>{GS_INFO["DLSS 5 (Neural Rendering)"]}</InfoTip>}
          </p>
          <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
            <button className="btn-revert" style={{ padding: "4px 10px", fontSize: 11 }}
              onClick={() => setShowHowItWorks(true)}>
              How it works?
            </button>
            <button className="btn-revert" style={{ padding: "4px 10px", fontSize: 11 }}
              onClick={() => setBrowsingAssetDir(true)}>
              Select DLSS5 folder
            </button>
          </div>
        </div>

        {!targetDir && (
          <p style={{ fontSize: 11, color: "#e8a000", padding: "8px 16px" }}>
            Could not determine this game's executable directory automatically ,
            it has zero or more than one candidate .exe.
          </p>
        )}

        {targetDir && (
          <>
            <div className="gs-row" style={{ padding: "10px 16px" }}>
              <div className="gs-row-label">
                <div className="gs-row-title">Enable DLSS 5</div>
                <div className="gs-row-sub">
                  {gameOverrides.dlss5_proxy_dll
                    ? `Installed as ${gameOverrides.dlss5_proxy_dll}`
                    : targetDir}
                </div>
              </div>
              <div className="gs-row-control">
                <div className={`toggle${enabled ? " on" : ""}`}
                  onClick={busy ? undefined : toggle}
                  style={{ cursor: busy ? "wait" : "pointer" }}>
                  <div className="toggle-track" /><div className="toggle-thumb" />
                </div>
              </div>
            </div>

            <div style={{ padding: "0 16px 10px" }}>
              {preflight.map((c, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start",
                  fontSize: 11, padding: "3px 0" }}>
                  <span style={{
                    color: c.ok ? "#76b900" : c.level === "warn" ? "#e8a000" : "#e05252",
                    flexShrink: 0, width: 14,
                  }}>
                    {c.ok ? "✓" : c.level === "warn" ? "!" : "✗"}
                  </span>
                  <span style={{ color: "#c8d0da", flexShrink: 0 }}>{c.check}:</span>
                  <span style={{ color: "#8a9ab0" }}>{c.detail}</span>
                </div>
              ))}
            </div>

            {msg && (
              <p style={{ fontSize: 11, color: "#8a9ab0", padding: "0 16px 10px" }}>{msg}</p>
            )}

            <div style={{
              padding: "6px 16px 4px", fontSize: 10, color: "#6a7688",
              textTransform: "uppercase", letterSpacing: 0.4, borderTop: "1px solid #232323",
              marginTop: 4,
            }}>
              DLSS Super Resolution
            </div>
            <Dlss5Toggle label="Override DLSS's preset" value={config["DLSS.RenderPresetOverride"]}
              disabled={cfgDisabled} onCommit={commitDlssPresetOverride}
              info={<>
                <p style={{ margin: "0 0 8px", color: "#e8a000" }}><strong>Tested on 007 First
                  Light and it broke the picture</strong> , not ghosting, severe corruption
                  (blown-out highlights, a magenta cast, vertical noise). Neural Rendering here
                  is unofficial and was only ever confirmed working under this game's own
                  default preset; forcing a different one at the same time changes what Super
                  Resolution hands to it in a way Neural Rendering doesn't cope with. If you
                  turn this on and the picture looks broken rather than just soft, turn it back
                  off immediately , that's this bug, not new damage.</p>
                <p style={{ margin: 0 }}>Trailing or smearing behind moving objects is usually
                  DLSS Super Resolution's own preset, not Neural Rendering , NR only refines
                  whatever Super Resolution already produced, and has no temporal-accumulation
                  control of its own to fix ghosting with. This control exists for deliberate
                  experimentation with that, one game at a time , it's no longer applied
                  automatically, and picks no preset for you.</p>
              </>} />
            <Dlss5Select label="Preset" value={config["DLSS.RenderPresetForAll"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DLSS.RenderPresetForAll", v)}
              note="Only used when Override DLSS's preset (above) is on. Untested alongside
                Neural Rendering , see the warning above before picking anything but auto."
              info="M is NVIDIA's 2nd-generation transformer model , generally the
                lowest-ghosting choice on its own, without Neural Rendering also enabled.
                K is a faster fallback if M costs too much performance. Letters before J
                are older CNN-based presets predating the transformer model."
              options={[
                { v: "0", label: "Game's own default" },
                ...["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"]
                  .map((letter, i) => ({
                    v: String(i + 1),
                    label: letter === "M" ? "M (recommended , fixes ghosting)"
                         : letter === "K" ? "K (faster, still low ghosting)"
                         : letter,
                  })),
              ]} />

            <div style={{
              padding: "6px 16px 4px", fontSize: 10, color: "#6a7688",
              textTransform: "uppercase", letterSpacing: 0.4, borderTop: "1px solid #232323",
              marginTop: 8,
            }}>
              Neural Rendering
            </div>
            <Dlss5Select label="Preset" value={config["DlssNr.Preset"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.Preset", v)}
              note="Baked in when the model is created , takes effect next launch."
              info="Which trained Neural Rendering model variant runs. NVIDIA hasn't
                published what 0-3 mean , this is undocumented and driven directly,
                same as Style below. If one preset looks worse in a specific game, try
                another; there's no 'correct' one to recommend."
              options={[0, 1, 2, 3].map(n => ({ v: String(n), label: String(n) }))} />
            <Dlss5Select label="Style" value={config["DlssNr.Style"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.Style", v)}
              note="Takes effect next launch, same as Preset."
              info="A second undocumented model variant, alongside Preset. Also needs a
                relaunch to change."
              options={[0, 1, 2, 3].map(n => ({ v: String(n), label: String(n) }))} />
            <Dlss5Slider label="Intensity" value={config["DlssNr.Intensity"]} seed={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.Intensity", v)}
              info="How far the frame moves toward the Neural Rendering model's own
                picture. 0 gives back exactly what DLSS Super Resolution produced before
                this pass touches it; 1 is the model's full picture; above 1 goes further
                than the model's own answer." />
            <Dlss5Slider label="Local structure" value={config["DlssNr.LocalStructure"]} seed={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.LocalStructure", v)}
              info="Undocumented model parameter , NVIDIA hasn't published what this
                changes beyond the name. Defaults to 1." />
            <Dlss5Slider label="Local tone" value={config["DlssNr.LocalTone"]} seed={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.LocalTone", v)}
              info="Undocumented model parameter , NVIDIA hasn't published what this
                changes beyond the name. Defaults to 1." />
            <Dlss5Slider label="Skin structure" value={config["DlssNr.SkinStructure"]} seed={-1}
              min={-1} max={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.SkinStructure", v)}
              info={<>-1 means "follow Local Structure above" , the model's own default,
                not the same as a strength of zero.</>} />
            <Dlss5Slider label="Transfer strength" value={config["DlssNr.TransferStrength"]} seed={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.TransferStrength", v)}
              info="How much of the model's own brightness carries over. It doesn't add
                to the frame , it produces a complete rescaled picture and blends toward
                it. 0 keeps exactly what the upscaler produced, 1 is the model's picture,
                above 1 goes past it." />
            <Dlss5Slider label="Colour strength" value={config["DlssNr.ColourStrength"]} seed={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.ColourStrength", v)}
              info="Whether the model's own colour comes along with its brightness. 0
                keeps the game's own hue exactly and only lets brightness follow the
                model; 1 also brings the model's colour." />
            <Dlss5Slider label="Working scale" value={config["DlssNr.WorkingScale"]} seed={1}
              min={0.25} max={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.WorkingScale", v)}
              info="What fraction of the frame the model actually works on , the frame
                itself is never reduced, only the model's own internal work is done small
                and enlarged back. Cost falls with the square of this, so 0.5 is roughly
                a quarter of the GPU cost." />
            <Dlss5Slider label="Max ratio" value={config["DlssNr.MaxRatio"]} seed={2}
              min={1} max={4}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.MaxRatio", v)}
              info="The most the pass may brighten any pixel, as a multiple of what it
                already was. Darkening is never capped. Exists to stop the model turning
                a bright light into a cluster of blown-out coloured cells." />
            <Dlss5Toggle label="Auto mask" value={config["DlssNr.AutoMask"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.AutoMask", v)}
              info="Undocumented , NVIDIA hasn't published what this masks or how.
                Defaults on." />
            <Dlss5Toggle label="Auto capture (before/after frames)" value={config["DlssNr.AutoCapture"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.AutoCapture", v)}
              info="Writes matched before/after frames to a capture folder beside the
                DLSS 5 runtime the first time the pass runs each launch , useful for
                comparing what Neural Rendering actually changed. Bounded to 8 frames,
                and each run overwrites the last." />
            <Dlss5Select label="Debug view" value={config["DlssNr.DebugView"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("DlssNr.DebugView", v)}
              info="Replaces the game's picture with a diagnostic view of what the
                Neural Rendering pass is doing. A flat grey 'Diff x20' view means the
                model is doing nothing , useful for confirming the pass is actually
                running before you go looking for it in the final image."
              options={[
                { v: "0", label: "Off" },
                { v: "1", label: "Input" },
                { v: "2", label: "Raw output" },
                { v: "3", label: "Diff x20" },
              ]} />

            <div style={{
              padding: "6px 16px 4px", fontSize: 10, color: "#6a7688",
              textTransform: "uppercase", letterSpacing: 0.4, borderTop: "1px solid #232323",
              marginTop: 8,
            }}>
              DLSS Frame Generation
            </div>
            <Dlss5Toggle label="DLSS Frame Generation" value={config["FrameGen.Enabled"]}
              disabled={cfgDisabled} onCommit={commitFrameGenEnabled}
              info={<>
                <p style={{ margin: "0 0 8px", color: "#e8a000" }}><strong>Tested on 007 First
                  Light alongside Neural Rendering and it corrupted the picture</strong> , a
                  pink/magenta cast, not the framerate boost this is meant to be. The user's
                  original report, before this was ever forced on, was ghosting only , a much
                  milder issue , which is what points at Frame Generation rather than Neural
                  Rendering itself. Pink/magenta corruption specifically from DLSS Frame
                  Generation is also independently reported in unrelated games. If you turn
                  this on and the picture goes wrong rather than just faster, turn it back off.</p>
                <p style={{ margin: 0 }}>NVIDIA's DLSS Frame Generation inserts an AI-generated
                  frame between two rendered ones to raise the framerate you see, on top of
                  whatever DLSS Super Resolution and Neural Rendering already produced. Needs
                  DLSS already active in the game's own graphics menu. This control is for
                  deliberate, one-game-at-a-time experimentation , it no longer picks a source
                  for you; use the Source control below once you turn this on.</p>
              </>} />
            <Dlss5Select label="Source" value={config["FrameGen.FGInput"]}
              disabled={cfgDisabled} onCommit={commitFgSource}
              note="AMD FSR and Intel XeSS frame-gen sources were removed from this build ,
                GreenBoost targets NVIDIA hardware only, and every game this runs on
                already has an NVIDIA DLSS-Frame-Generation path."
              info={<>
                <p style={{ margin: "0 0 8px" }}><strong>Native (recommended)</strong> ,
                  captures the game's own DLSS Frame Generation call, the same one
                  Streamline exposes. Use this for any game that already supports DLSS
                  Frame Generation in its own settings menu.</p>
                <p style={{ margin: "0 0 8px" }}><strong>NGX passthrough (advanced)</strong> ,
                  for the small set of games that call NVIDIA's NGX frame-generation API
                  directly instead of through Streamline. Try Native first; only switch to
                  this if Native does nothing in a specific game.</p>
                <p style={{ margin: 0 }}><strong>Inject (experimental)</strong> , adds
                  DLSS Frame Generation to a game that never shipped any frame-generation
                  support at all, on top of whatever upscaler is already running. Not
                  guaranteed to work well, or at all, outside 007 First Light , this is
                  the newest and least-tested path.</p>
              </>}
              options={[
                { v: "dlssg", label: "Native (recommended)" },
                { v: "nvngxfg", label: "NGX passthrough (advanced)" },
                { v: "upscaler", label: "Inject (experimental)" },
                { v: "nofg", label: "Off" },
              ]} />

            <div style={{
              padding: "6px 16px 4px", fontSize: 10, color: "#6a7688",
              textTransform: "uppercase", letterSpacing: 0.4, borderTop: "1px solid #232323",
              marginTop: 8,
            }}>
              Image
            </div>
            <Dlss5Toggle label="Override DLSS sharpening" value={config["Sharpness.OverrideSharpness"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("Sharpness.OverrideSharpness", v)}
              info="DLSS Super Resolution already applies its own sharpening internally.
                Turning this on replaces that with the fixed Sharpness value and shader
                below instead , leave it off to use DLSS's own built-in choice." />
            <Dlss5Slider label="Sharpening strength" value={config["Sharpness.Sharpness"]} seed={0.3}
              min={0} max={1.3}
              disabled={cfgDisabled} onCommit={v => commitConfig("Sharpness.Sharpness", v)}
              info="Only used when Override DLSS sharpening (above) is on. Has no effect
                otherwise." />
            <Dlss5Select label="Sharpening method" value={config["Sharpness.Shader"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("Sharpness.Shader", v)}
              note="Only used when Override DLSS sharpening (above) is on."
              info={<>
                <p style={{ margin: "0 0 8px" }}><strong>RCAS (recommended)</strong> , a
                  fast, widely-used sharpening filter (Robust Contrast-Adaptive
                  Sharpening, originally published by AMD as a generic image-processing
                  technique, not tied to any GPU vendor). Good default for most scenes.</p>
                <p style={{ margin: "0 0 8px" }}><strong>Depth-aware (DA)</strong> , eases
                  off sharpening across object edges using the game's depth buffer, to
                  avoid haloing artifacts. Sharpens more on distant objects. Better for
                  scenes with complex geometry.</p>
                <p style={{ margin: 0 }}><strong>Local contrast depth-aware (LCDA)</strong> ,
                  the same depth-aware idea as DA, weighted by local contrast instead.</p>
              </>}
              options={[
                { v: "rcas", label: "RCAS (recommended)" },
                { v: "da", label: "Depth-aware (DA)" },
                { v: "lcda", label: "Local contrast depth-aware (LCDA)" },
              ]} />
            <Dlss5Toggle label="Output scaling" value={config["OutputScaling.Enabled"]}
              disabled={cfgDisabled} onCommit={v => commitConfig("OutputScaling.Enabled", v)}
              info="Not an NVIDIA/DLSS feature , this resizes the already-finished frame
                a second time, after DLSS Super Resolution and Neural Rendering have both
                already run. Turn it on to render past your monitor's resolution and scale
                down for extra clarity (like DSR/DLDSR), or to hit a specific output size
                a game doesn't offer natively. Leave off unless you have a specific reason
                to want a different output size than DLSS already produced." />
            <Dlss5Slider label="Output scaling ratio" value={config["OutputScaling.Multiplier"]} seed={1.5}
              min={0.5} max={3}
              disabled={cfgDisabled} onCommit={v => commitConfig("OutputScaling.Multiplier", v)}
              info="Only used when Output scaling (above) is on. Above 1 renders larger
                than your monitor and scales down (extra clarity, more GPU cost); below 1
                renders smaller and scales up (less GPU cost, softer image)." />
            <Dlss5Slider label="Framerate limit" value={config["Framerate.FramerateLimit"]} seed={60}
              min={0} max={240} step={1}
              disabled={cfgDisabled} onCommit={v => commitConfig("Framerate.FramerateLimit", v)}
              info="Caps the framerate using NVIDIA Reflex when it's available, which
                paces frames instead of just discarding them late , lower input latency
                than a generic frame cap. Particularly worth setting once DLSS Frame
                Generation is on, since Frame Generation inflates the frame counter
                without lowering latency on its own. 0 means no limit." />

            <div style={{
              padding: "6px 16px 4px", fontSize: 10, color: "#6a7688",
              textTransform: "uppercase", letterSpacing: 0.4, borderTop: "1px solid #232323",
              marginTop: 8,
            }}>
              Advanced , DLL overlay
            </div>
            <p style={{ fontSize: 11, color: "#8a9ab0", padding: "0 16px 6px", lineHeight: 1.5 }}>
              If the folder above also holds regular Streamline files ({"sl.*.dll"},{" "}
              {"nvngx_dlss*.dll"}), they're offered here for the game's own DLL overlay ,
              a separate mechanism from the Neural Rendering runtime, for updating
              Super Resolution / Frame Generation / Reflex.
              {GS_INFO["External DLL folder"] && <InfoTip>{GS_INFO["External DLL folder"]}</InfoTip>}
            </p>

            {!gameOverrides.external_dll_dir && (
              <p style={{ fontSize: 11, color: "#8a9ab0", padding: "0 16px 8px" }}>
                No folder selected yet , use "Select DLSS5 folder" above.
              </p>
            )}

            {gameOverrides.external_dll_dir && (
              <div style={{ padding: "0 16px 8px" }}>
                <div className="gs-row-title" style={{ marginBottom: 4 }}>
                  Contents {inspecting && <span className="animate-spin" style={{ display: "inline-block" }}><Icon.Refresh /></span>}
                </div>
                {!inspecting && inspected.length === 0 && (
                  <p className="gs-row-sub" style={{ margin: 0 }}>
                    No .dll files here , the game will launch normally with no overlay.
                  </p>
                )}
                {inspected.map(d => {
                  const shipped = game.dlls.find(g => g.name.toLowerCase() === d.name.toLowerCase());
                  const isDisabled = (gameOverrides.external_dll_disabled ?? []).includes(d.name.toLowerCase());
                  return (
                    <div key={d.name} className="gs-row" style={{ padding: "6px 0" }}>
                      <div className="gs-row-label">
                        <div className="gs-row-title" style={{ opacity: isDisabled ? 0.5 : 1 }}>{d.name}</div>
                        <div className="gs-row-sub">
                          {d.known_type || d.description || "Not something GreenBoost recognises , will still be linked"}
                          {" · "}{d.version}
                          {shipped
                            ? <> {" · "}game ships {shipped.version || "unknown"} <span style={{ color: "#a5b4fc" }}>→ {d.version}</span></>
                            : <> {" · "}<span style={{ color: "#e8a000" }}>not used by this game , no code path calls it</span></>}
                          {isDisabled && <> {" · "}<span style={{ color: "#8a9ab0" }}>excluded</span></>}
                        </div>
                      </div>
                      <div className="gs-row-control">
                        <div
                          className={`toggle${isDisabled ? "" : " on"}`}
                          style={{ width: 32, height: 18 }}
                          onClick={() => toggleDllEnabled(d.name)}
                          title={isDisabled ? "Excluded , won't be linked or copied in" : "Included in the overlay/copy-in"}
                        >
                          <div className="toggle-track" />
                          <div className="toggle-thumb" style={{
                            width: 12, height: 12, top: 3, left: 3,
                            transform: isDisabled ? "none" : "translateX(14px)",
                          }} />
                        </div>
                      </div>
                    </div>
                  );
                })}
                {Object.keys(externalDllTargets()).length > 0 && (
                  <p className="gs-row-sub" style={{ marginTop: 6 }}>
                    Overlays into: {Array.from(new Set(Object.values(externalDllTargets()).flat()))
                      .map(d => d.replace(game.path, "").replace(/^\//, "") || "(game folder)")
                      .join(", ")}
                  </p>
                )}
              </div>
            )}

            <div className="gs-row" style={{ padding: "10px 16px" }}>
              <div className="gs-row-label">
                <div className="gs-row-title">
                  Copy permanently into the game{GS_INFO["Copy DLLs into game"] && <InfoTip>{GS_INFO["Copy DLLs into game"]}</InfoTip>}
                </div>
                <div className="gs-row-sub">
                  Survives Steam's "Verify integrity of game files" , the launch-time
                  overlay does not. Files stay until you press Restore.
                </div>
              </div>
              <div className="gs-row-control" style={{ display: "flex", gap: 6 }}>
                <button className="btn-revert" disabled={!gameOverrides.external_dll_dir || copyBusy}
                        onClick={handleCopyIntoGame}>
                  {copyBusy ? "Working…" : "Copy into game"}
                </button>
                <button className="btn-revert" disabled={copyBusy}
                        onClick={handleRestoreExternal}>
                  Restore
                </button>
              </div>
            </div>

            {copyMsg && (
              <p style={{ fontSize: 11, color: "#8a9ab0", padding: "0 16px 8px" }}>
                {copyMsg}
              </p>
            )}
          </>
        )}
      </CollapsibleSection>

      {showFilesModal && (
        <Dlss5FilesModal onClose={() => setShowFilesModal(false)} onConfirm={doEnable} />
      )}
      {showHowItWorks && (
        <Dlss5HowItWorksModal onClose={() => setShowHowItWorks(false)} />
      )}
      {browsingAssetDir && (
        <FolderBrowserModal
          initialPath={gameOverrides.external_dll_dir || undefined}
          onClose={() => setBrowsingAssetDir(false)}
          onPick={pickAssetDir}
        />
      )}
    </>
  );
}
