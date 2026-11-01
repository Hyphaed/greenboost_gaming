import { useState, useEffect, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Game, SettingGroup, DlssSourceState, InstallStreamProps, GameOverrides, GameNisConfig, GameWrappers, DlssStatus, GlobalSettingsState, DirectStorageInfo, LaunchStatus } from "../types";
import { Icon } from "../icons";
import { GameThumb } from "../components/GameThumb";
import { GameHeroBanner } from "../components/GameHeroBanner";
import { CollapsibleSection } from "../components/CollapsibleSection";
import { InstallStreamModal } from "../components/InstallStreamModal";
import { DllPicker } from "../components/DllPicker";
import { SwitchLook } from "../components/Toggle";
import { InfoTip } from "../components/InfoTip";
import { Dlss5Panel } from "../components/Dlss5Panel";
import { GS_INFO } from "../gsHelp";
import { OPTIMAL_OVERRIDES, diffFromOptimal, optimalPatch, countActiveOverrides } from "../gameOptimal";
import { ChangeSummaryModal } from "../components/ChangeSummaryModal";
import { summarizeGame, type Change } from "../changeSummary";
import { globalSettings, useGlobalSettings, loadGlobalSettings } from "../store/globalSettings";
import { VULKAN_LAYER_VISIBLE } from "../featureFlags";
import { GlobalSettingsPanel } from "./games/GlobalSettingsPanel";
import { dlssPresetLabel } from "./games/shared";

const menuItemStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 8,
  width: "100%", padding: "10px 12px",
  background: "transparent", border: "none",
  color: "var(--color-text)", fontSize: 13, cursor: "pointer",
  textAlign: "left",
};
const menuItemHintStyle: React.CSSProperties = {
  marginLeft: "auto", color: "var(--color-text-muted)", fontSize: 12,
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
        fontSize: 12, padding: "2px 7px", borderRadius: 4, cursor: "pointer",
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

export function GamesView() {
  const [games, setGames]           = useState<Game[]>([]);
  // Split once here rather than re-filtering `installed` at every render
  // site , scan_games() (scanner.rs) now marks a Steam-uninstalled-but-
  // leftover directory `installed: false` instead of silently listing it
  // as a normal game (real incident 2026-08-31: FF7 Rebirth and FF16 both
  // kept showing as playable after being uninstalled).
  const installedGames    = games.filter(g => g.installed);
  const notInstalledGames = games.filter(g => !g.installed);
  const [filter, setFilter]         = useState("");
  const [selected, setSelected]     = useState<Game | null>(null);
  const [groups, setGroups]         = useState<SettingGroup[]>([]);
  const [gameOverrides, setGameOverrides] = useState<GameOverrides | null>(null);
  const [savingOverrides, setSavingOverrides] = useState(false);
  const [overridesMsg, setOverridesMsg] = useState<string | null>(null);
  const [, setSavedProfiles] = useState<string[]>([]);
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

  // What Steam's own CompatToolMapping currently has for the selected game.
  // `null` means "Steam default" (nothing mapped, or read failed) , see
  // manager.rs::read_compat_tool. This used to never be surfaced at all:
  // the mapping only ever got WRITTEN, silently, on Launch, so a game
  // correctly running under GreenBoost Proton had no visible confirmation
  // of that , confirmed live 2026-08-31 against "The First Berserker:
  // Khazan", which was already mapped and already ran through the wrapper,
  // with nothing in the UI able to show it.
  const [compatTool, setCompatTool] = useState<string | null>(null);
  const [togglingCompatTool, setTogglingCompatTool] = useState(false);
  const [compatToolMsg, setCompatToolMsg] = useState<string | null>(null);
  const [lastLaunchEpoch, setLastLaunchEpoch] = useState<number | null>(null);

  const [gameSettingProfiles, setGameSettingProfiles] = useState<string[]>([]);
  // "__default" is not a saved profile , it's the always-present anchor
  // entry that replaces the old blank ", select ," placeholder (same
  // pattern as GlobalSettingsPanel's own SMART_DEFAULTS_ID). Read-only:
  // Load/Delete stay disabled while it's selected.
  const DEFAULT_GSP_ID = "__default";
  const [selectedGspName, setSelectedGspName] = useState<string>(DEFAULT_GSP_ID);
  const [gspInput, setGspInput] = useState<string>("");
  const [gspMsg, setGspMsg] = useState<string | null>(null);

  // Per-game DLSS/Streamline DLL version status, keyed by game PATH.
  //
  // This used to be a single slot that the selection effect below reset to
  // null. That effect fires on every change of the `selected` OBJECT, and a
  // games-list refresh hands back a fresh object for the same game , so a
  // scan that had already run was wiped, and nothing re-ran it, because the
  // only thing that re-triggered a scan was the path CHANGING. Confirmed
  // 2026-08-21: "game shipped with v2.8.0.0" appeared right after an
  // Upgrade (which scans explicitly) and was gone on the next app open.
  // Keyed by path, a refresh cannot lose it and switching back is instant.
  const [dlssByPath, setDlssByPath] = useState<Record<string, DlssStatus>>({});
  const [scanningDlss, setScanningDlss] = useState(false);
  const [dlssScanError, setDlssScanError] = useState<string | null>(null);
  // Paths already scanned this session , stops a failing scan from looping.
  const dlssAttempted = useRef<Set<string>>(new Set());
  const dlssStatus = selected ? (dlssByPath[selected.path] ?? null) : null;

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
    | "game_mode_pause_extensions" | "game_mode_keep_awake"
  > | null = gs ? {
    game_mode_pause_extensions: gs.game_mode && gs.game_mode_pause_extensions,
    game_mode_keep_awake: gs.game_mode && gs.game_mode_keep_awake,
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
      // Prefer an installed game as the default selection , otherwise an
      // uninstalled leftover sorting alphabetically first (as FF7 Rebirth
      // did, ahead of "The First Berserker: Khazan") would open by default
      // with no Launch button and nothing useful in the detail pane.
      const defaultPick = deduped.find(g => g.installed) ?? deduped[0];
      if (defaultPick && !selectedRef.current) {
        setSelected(defaultPick);
        selectedRef.current = defaultPick;
      }
    } catch (e) { console.error(e); }
    setLoadingGames(false);
  }, []);

  const [hiddenGames, setHiddenGames] = useState<{ path: string; name: string }[]>([]);
  const [showHidden, setShowHidden] = useState(false);
  const [showNotInstalled, setShowNotInstalled] = useState(false);
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
      await invoke("hide_game", { path: game.path, name: game.name, appid: game.appid ?? null });
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

  const [deletingLeftover, setDeletingLeftover] = useState<string | null>(null);

  const handleDeleteLeftover = useCallback(async (game: Game) => {
    setHideMsg(null);
    setDeletingLeftover(game.path);
    try {
      const freedBytes: number = await invoke("delete_leftover_files", { path: game.path });
      const freedMb = (freedBytes / (1024 * 1024)).toFixed(0);
      setGames(prev => prev.filter(g => g.path !== game.path));
      if (selectedRef.current?.path === game.path) setSelected(null);
      setHideMsg(`Deleted "${game.name}" leftovers, freed ${freedMb} MB.`);
    } catch (err) {
      setHideMsg(`Couldn't delete "${game.name}"'s leftover files: ${err}`);
    }
    setDeletingLeftover(null);
  }, []);

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
    if (!selectedGspName || selectedGspName === DEFAULT_GSP_ID || !selected?.appid) return;
    try {
      const loaded = await invoke<GameOverrides | null>("load_game_setting_profile", { name: selectedGspName });
      if (!loaded) { setGspMsg(`Profile "${selectedGspName}" not found.`); return; }
      await invoke("save_game_overrides", { appid: selected.appid, overrides: loaded });
      setGameOverrides(loaded as GameOverrides);
      setGspMsg(`Loaded "${selectedGspName}".`);
    } catch (e: any) { setGspMsg("Load failed: " + (e?.message ?? e)); }
  }, [selectedGspName, selected]);

  const handleGspDelete = useCallback(async () => {
    if (!selectedGspName || selectedGspName === DEFAULT_GSP_ID) return;
    try {
      await invoke("delete_game_setting_profile", { name: selectedGspName });
      setGspMsg(`Deleted "${selectedGspName}".`);
      setSelectedGspName(DEFAULT_GSP_ID);
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
      invoke<string | null>("get_compat_tool", { appid: selected.appid })
        .then(setCompatTool)
        .catch(() => setCompatTool(null));
      invoke<number | null>("get_last_greenboost_launch", { appid: selected.appid })
        .then(setLastLaunchEpoch)
        .catch(() => setLastLaunchEpoch(null));
    } else {
      setGameOverrides(null);
      setGameAnalytics(null);
      setCompatTool(null);
      setLastLaunchEpoch(null);
    }
    setCompatToolMsg(null);
  }, [selected, loadSettings]);

  const handleToggleCompatTool = useCallback(async () => {
    if (!selected?.appid) return;
    setTogglingCompatTool(true);
    setCompatToolMsg(null);
    const enable = compatTool !== "greenboost-proton";
    try {
      const out: string = await invoke("set_compat_tool", { appid: selected.appid, enable });
      setCompatToolMsg(out);
      const fresh: string | null = await invoke("get_compat_tool", { appid: selected.appid });
      setCompatTool(fresh);
    } catch (err) {
      setCompatToolMsg(`Couldn't change the Proton mapping: ${err}`);
    }
    setTogglingCompatTool(false);
  }, [selected, compatTool]);

  const handleScanDlssStatus = useCallback(async (): Promise<DlssStatus | null> => {
    if (!selected) return null;
    const path = selected.path;
    dlssAttempted.current.add(path);
    setScanningDlss(true);
    setDlssScanError(null);
    let status: DlssStatus | null = null;
    try {
      status = await invoke<DlssStatus>("get_dlss_status", { path });
      const got = status;
      setDlssByPath(prev => ({ ...prev, [path]: got }));
    } catch (e: any) {
      // Was console.error only, which left the header reading "Not checked
      // for updates yet" , indistinguishable from a game with nothing to
      // show, and it hides the one line that says why.
      console.error(e);
      setDlssScanError(String(e?.message ?? e));
    }
    setScanningDlss(false);
    return status;
  }, [selected]);

  // Scan once per game, automatically. The "game shipped with vX" tag and the
  // Shipped/Backup dropdown entries all come from this, and they have to be
  // there the first time a game is opened , not only after an Upgrade.
  useEffect(() => {
    const path = selected?.path;
    if (!path || dlssByPath[path] || dlssAttempted.current.has(path)) return;
    void handleScanDlssStatus();
  }, [selected?.path, dlssByPath, handleScanDlssStatus]);

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
        const seeded = gameOverrides;
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
    if (!window.confirm(`Put ${selected.name} back to the settings it had before GreenBoost changed them?`)) return;
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
          // Re-scan anyway: the cache may have changed even though the run
          // failed, and stale "update to vX" badges would otherwise stay.
          await handleScanDlssStatus();
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
        // The DLLs on disk just changed , the panel must not keep showing
        // the versions from before the rollback.
        void handleScanDlssStatus();
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
        // The files on disk just changed , drop this game's scan and take a
        // fresh one, rather than leaving pre-restore versions on screen.
        const path = selectedRef.current?.path;
        if (path) {
          dlssAttempted.current.delete(path);
          setDlssByPath(prev => {
            const next = { ...prev };
            delete next[path];
            return next;
          });
        }
        void handleScanDlssStatus();
      },
    });
  };

  const [dlssMenuOpen, setDlssMenuOpen] = useState(false);

  const [launching, setLaunching] = useState(false);
  // Set when a launch's own failure was specifically Steam's in-prefix DRM
  // bootstrap (steam.exe) hanging , see LaunchStatus::Failed's stuck_reason.
  // Holds the appid so "Fix Stuck Launch" targets the right one even if the
  // user has since selected a different game in the list.
  const [stuckAppid, setStuckAppid] = useState<string | null>(null);
  const [fixingStuck, setFixingStuck] = useState(false);

  // ── RenderDoc capture ─────────────────────────────────────────────────
  //
  // A real, human-driven GPU frame debugger (renderdoc.org), not something
  // GreenBoost built or scripts on its own , the Suite arms capture and
  // launches the game exactly like the plain Launch button, then hands off
  // to qrenderdoc for a person to actually inspect. Hidden entirely when
  // install.sh's RenderDoc staging step didn't run (offline install,
  // --no-renderdoc, or the layer failed to register).
  const [renderdocAvailable, setRenderdocAvailable] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureArmedAt, setCaptureArmedAt] = useState<number | null>(null);
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);

  useEffect(() => {
    invoke<boolean>("renderdoc_available").then(setRenderdocAvailable).catch(() => {});
  }, []);

  const handleCaptureLaunch = async () => {
    if (!selected?.appid) return;
    const appid = selected.appid;
    setCapturing(true);
    setCaptureMsg(null);
    try {
      const armedAt = await invoke<number>("arm_renderdoc_capture", { appid });
      setCaptureArmedAt(armedAt);
      const cinema = !!globalSettings.get()?.auto_disable_secondary_on_launch;
      await invoke("launch_game", { appid, disableSecondaryDisplays: cinema });
      setCaptureMsg(
        "Capture armed , press the in-game capture hotkey (F12 by default), " +
        "then click “Open Capture” after exiting.");
      void watchLaunch(appid);
    } catch (e: any) {
      setCaptureMsg("Could not arm capture: " + (e?.message ?? e));
    }
    setCapturing(false);
  };

  const handleOpenCapture = async () => {
    if (captureArmedAt == null) return;
    setCaptureMsg(null);
    try {
      const path = await invoke<string | null>("find_renderdoc_capture", { sinceUnix: captureArmedAt });
      if (!path) {
        setCaptureMsg("No .rdc found yet under ~/RenderDoc/ , capture a frame in-game first (F12), then try again.");
        return;
      }
      await invoke("open_renderdoc_capture", { path });
      setCaptureMsg("Opened in qrenderdoc: " + path);
    } catch (e: any) {
      setCaptureMsg("Could not open capture: " + (e?.message ?? e));
    }
  };

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
        setStuckAppid(null);
        return;
      }
      if (st.state === "failed" && st.appid === appid) {
        const log = st.log.length
          ? "\n\nDetails:\n" + st.log.join("\n")
          : "\n\nThe Proton wrapper wrote no log at all, which usually means " +
            "Steam never reached it , check the game's compatibility tool is " +
            "set to GreenBoost Proton.";
        if (st.stuck_reason === "drm_check") {
          // Distinct from the generic timeout below: something specific and
          // nameable ran (Steam's own DRM/ownership check) and hung, rather
          // than nothing running at all. Offer the one-click recovery
          // instead of just a diagnosis.
          setMsg(
            "Stuck on Steam's own DRM/ownership check (steam.exe, inside the " +
            "prefix) , not GreenBoost, not this compat tool, not the game " +
            "itself. It never handed off to the game in 3 minutes.\n\nClick " +
            "\"Fix Stuck Launch\" to clear the wedged process tree, then try " +
            "launching again." + log);
          setStuckAppid(appid);
        } else {
          setMsg(
            "No game process ever started. Steam accepted the launch, but nothing " +
            "came up in 3 minutes.\n\nNothing is broken and no settings changed " +
            ", you can launch again, or pick a different Proton under Global " +
            "Settings \u2192 Upstream Proton." + log);
          setStuckAppid(null);
        }
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
    setStuckAppid(null);
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

  /** Clear a launch wedged on Steam's own DRM/ownership check. Same
   *  underlying stop as the tray's "Stop game" , there's nothing playing and
   *  nothing to save, just a stuck pre-launch process tree , offered here
   *  because the Suite already knows exactly what's stuck and why, instead
   *  of leaving the user to find the tray icon or quit Steam entirely (the
   *  full-client restart that was the only recovery before this existed). */
  const handleFixStuckLaunch = async () => {
    if (!stuckAppid) return;
    setFixingStuck(true);
    try {
      await invoke("fix_stuck_launch", { appid: stuckAppid });
      setMsg("Cleared. You can launch again.");
      setStuckAppid(null);
    } catch (e: any) {
      setMsg("Could not clear the stuck launch: " + (e?.message ?? e));
    }
    setFixingStuck(false);
  };

  const overrideDiffKeys = gameOverrides ? diffFromOptimal(gameOverrides) : [];
  const overrideActiveCount = gameOverrides ? countActiveOverrides(gameOverrides) : 0;
  const anyNeedsChange = groups.some(g => g.settings.some(s => s.needs_change))
    || overrideDiffKeys.length > 0;

  const applyAllRecommended = () => {
    if (!selected?.appid || !gameOverrides) return;
    const seeded = gameOverrides;
    saveOverrides(optimalPatch(seeded));
  };

  const [gamesTab, setGamesTab] = useState<"program" | "global">("program");

  return (
    <>
      <div className="sub-nav" role="tablist" aria-label="Settings scope"
           onKeyDown={e => {
             if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
               const next = gamesTab === "program" ? "global" : "program";
               setGamesTab(next);
               requestAnimationFrame(() => document.getElementById(`tab-${next}`)?.focus());
             }
           }}>
        <button role="tab" id="tab-program" aria-selected={gamesTab === "program"}
          tabIndex={gamesTab === "program" ? 0 : -1}
          className={`sub-nav-tab${gamesTab === "program" ? " active" : ""}`}
          onClick={() => setGamesTab("program")}>This Game</button>
        <button role="tab" id="tab-global" aria-selected={gamesTab === "global"}
          tabIndex={gamesTab === "global" ? 0 : -1}
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
              : `${installedGames.filter(g => !filter.trim() || g.name.toLowerCase().includes(filter.toLowerCase())).length}/${installedGames.length} ${installedGames.length === 1 ? "Game" : "Games"}`}
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
              borderRadius: 4, color: "var(--color-text)", outline: "none",
            }}
          />
        </div>

        <div className="games-list-scroll">
          {loadingGames ? (
            <div className="spinner-row">
              <span className="animate-spin" style={{ display: "inline-block" }}><Icon.Refresh /></span>
              Scanning Steam library…
            </div>
          ) : installedGames.length === 0 ? (
            <div className="settings-empty">No games found in Steam library.</div>
          ) : (
            installedGames.filter(g =>
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

        {notInstalledGames.length > 0 && (
          <div style={{ padding: "6px 12px 8px", borderTop: "1px solid #1e1e1e" }}>
            <button
              onClick={() => setShowNotInstalled(v => !v)}
              style={{ background: "none", border: "none", color: "var(--color-text-muted)",
                       fontSize: 12, cursor: "pointer", padding: 0 }}
            >
              {showNotInstalled ? "Hide" : `${notInstalledGames.length} not installed , show`}
            </button>
            {showNotInstalled && (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                {notInstalledGames.map(g => (
                  <div key={g.path} style={{ display: "flex", justifyContent: "space-between",
                                               alignItems: "center", fontSize: 12, color: "var(--color-text-dim)", gap: 8 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title="Steam has no manifest (or no longer a fully-installed one) for this directory">
                      {g.name}
                      {g.leftover_bytes != null && (
                        <> · {(g.leftover_bytes / (1024 * 1024)).toFixed(0)} MB</>
                      )}
                    </span>
                    <button
                      onClick={() => handleDeleteLeftover(g)}
                      disabled={deletingLeftover === g.path}
                      style={{ background: "none", border: "none", color: "#ff6b6b",
                               fontSize: 12, cursor: "pointer", padding: 0, flexShrink: 0 }}
                      title="Permanently delete these leftover files from disk"
                    >
                      {deletingLeftover === g.path ? "Deleting…" : "Delete leftovers"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {hiddenGames.length > 0 && (
          <div style={{ padding: "6px 12px 8px", borderTop: "1px solid #1e1e1e" }}>
            <button
              onClick={() => setShowHidden(v => !v)}
              style={{ background: "none", border: "none", color: "var(--color-text-muted)",
                       fontSize: 12, cursor: "pointer", padding: 0 }}
            >
              {showHidden ? "Hide" : `${hiddenGames.length} removed , show`}
            </button>
            {showHidden && (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                {hiddenGames.map(hg => (
                  <div key={hg.path} style={{ display: "flex", justifyContent: "space-between",
                                               alignItems: "center", fontSize: 12, color: "var(--color-text-dim)", gap: 8 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {hg.name}
                    </span>
                    <button
                      onClick={() => handleRestoreGame(hg.path)}
                      style={{ background: "none", border: "none", color: "var(--color-gb)",
                               fontSize: 12, cursor: "pointer", padding: 0, flexShrink: 0 }}
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
          <p style={{
            fontSize: 12, margin: "6px 12px 0",
            color: hideMsg.startsWith("Couldn't") ? "#ff6b6b" : "#76b900",
          }}>
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
                {selected.appid && renderdocAvailable && (
                  <button
                    className="btn-revert"
                    onClick={handleCaptureLaunch}
                    disabled={capturing || launching}
                    title="Launch with RenderDoc's Vulkan capture layer armed , a real GPU frame debugger you drive yourself in qrenderdoc"
                  >
                    <Icon.Activity /> {capturing ? "Arming…" : "Capture"}
                  </button>
                )}
                {selected.appid && renderdocAvailable && captureArmedAt != null && (
                  <button
                    className="btn-revert"
                    onClick={handleOpenCapture}
                    title="Open the newest .rdc written since capture was armed, in qrenderdoc"
                  >
                    <Icon.Package /> Open Capture
                  </button>
                )}
                {stuckAppid === selected.appid && (
                  <button
                    className="btn-revert"
                    onClick={handleFixStuckLaunch}
                    disabled={fixingStuck}
                    title="Clear a launch wedged on Steam's own DRM/ownership check"
                  >
                    <Icon.AlertCircle /> {fixingStuck ? "Fixing…" : "Fix Stuck Launch"}
                  </button>
                )}
                {!loadingSettings && (
                  <button
                    className="btn-optimize btn-quiet"
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
                        className="btn-optimize btn-quiet"
                        onClick={handleUpdateDlss}
                        disabled={updatingDlss}
                        style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
                      >
                        <Icon.Download />{" "}
                        {updatingDlss ? "Working…" : "Upgrade DLSS"}
                      </button>
                      <button
                        className="btn-optimize btn-quiet"
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

                {selected.appid && (
                  <span
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      fontSize: 12, padding: "2px 7px", borderRadius: 4,
                      background: compatTool === "greenboost-proton"
                        ? "rgba(118,185,0,0.10)" : "rgba(154,154,154,0.10)",
                      border: `1px solid ${compatTool === "greenboost-proton"
                        ? "rgba(118,185,0,0.35)" : "rgba(154,154,154,0.25)"}`,
                      color: compatTool === "greenboost-proton" ? "#76b900" : "#9a9a9a",
                    }}
                    title="Which Proton build Steam actually launches this game with , read from config.vdf's CompatToolMapping"
                  >
                    {compatTool === "greenboost-proton"
                      ? "GreenBoost Proton"
                      : compatTool
                        ? `Proton: ${compatTool}`
                        : "Steam default Proton"}
                    <button
                      onClick={handleToggleCompatTool}
                      disabled={togglingCompatTool}
                      style={{
                        background: "none", border: "none", padding: 0,
                        cursor: togglingCompatTool ? "default" : "pointer",
                        color: "inherit", textDecoration: "underline",
                        fontSize: 12, opacity: togglingCompatTool ? 0.6 : 1,
                      }}
                    >
                      {togglingCompatTool
                        ? "…"
                        : compatTool === "greenboost-proton" ? "Unset" : "Use GreenBoost Proton"}
                    </button>
                  </span>
                )}
                {selected.appid && lastLaunchEpoch != null && (
                  <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}
                        title="When the GreenBoost Proton wrapper last wrote to this game's log">
                    Last GreenBoost launch: {new Date(lastLaunchEpoch * 1000).toLocaleString()}
                  </span>
                )}

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
                        fontSize: 12, padding: "2px 7px", borderRadius: 4,
                        background: "rgba(99,102,241,0.12)",
                        border: "1px solid rgba(99,102,241,0.3)", color: "#a5b4fc",
                      }} title={`${gameAnalytics.session_count} sessions · ${hours}h total · avg ${avgFmt}/session`}>
                        {gameAnalytics.session_count}× played · {hours}h
                      </span>
                      <span style={{
                        display: "inline-flex", alignItems: "center",
                        fontSize: 12, padding: "2px 7px", borderRadius: 4,
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
                          fontSize: 12, padding: "2px 7px", borderRadius: 4,
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
                            fontSize: 12, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
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

            {(msg || dlssMsg || captureMsg || compatToolMsg) && (
              <div className="game-msg-bar">
                {msg && <span className="status-msg">{msg}</span>}
                {dlssMsg && <span className="status-msg" style={{ color: "var(--color-gb)" }}>{dlssMsg}</span>}
                {captureMsg && <span className="status-msg">{captureMsg}</span>}
                {compatToolMsg && <span className="status-msg">{compatToolMsg}</span>}
              </div>
            )}

            <div className="game-detail-body">
            {selected?.appid && gameOverrides && (
              <div style={{
                display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
                padding: "8px 16px 10px", borderBottom: "1px solid #1e1e1e",
              }}>
                <span style={{ fontSize: 12, color: "var(--color-text-muted)", flexShrink: 0 }}>Game profile:</span>
                <select
                  className="gs-select"
                  style={{ minWidth: 120, flex: "0 1 140px" }}
                  value={selectedGspName}
                  onChange={e => setSelectedGspName(e.target.value)}
                >
                  <option value={DEFAULT_GSP_ID}>Default</option>
                  {gameSettingProfiles.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <button className="btn-revert" style={{ padding: "4px 9px", fontSize: 12 }}
                        disabled={!selectedGspName || selectedGspName === DEFAULT_GSP_ID}
                        onClick={handleGspLoad}>Load</button>
                <button className="btn-revert" style={{ padding: "4px 9px", fontSize: 12,
                                                        borderColor: "var(--color-red)", color: "var(--color-text-error)" }}
                        disabled={!selectedGspName || selectedGspName === DEFAULT_GSP_ID}
                        onClick={handleGspDelete}>Delete</button>
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
                  <button className="btn-optimize" style={{ padding: "4px 10px", fontSize: 12 }}
                          disabled={!gspInput.trim()} onClick={handleGspSave}>Save</button>
                </div>
                {gspMsg && (
                  <span style={{ fontSize: 12, color: "#9a9ab0", width: "100%" }}>{gspMsg}</span>
                )}
              </div>
            )}

            {directStorageInfo?.detected && (
              <div className="gs-row" style={{ padding: "10px 16px", fontSize: 12, color: "var(--color-text-muted)" }}>
                <span style={{ color: "var(--color-text)", fontWeight: 600 }}>DirectStorage detected</span>
                {" "}({directStorageInfo.dlls_found.join(", ")}) ·{" "}
                Proton:{" "}
                {directStorageInfo.proton_capable === true
                  ? <span style={{ color: "var(--color-gb)" }}>{directStorageInfo.proton_build || "capable build"} (capable)</span>
                  : <span style={{ color: "var(--color-text-muted)" }}>
                      {directStorageInfo.proton_build || "build"} (not confirmed capable)
                    </span>}
                {" · "}Storage:{" "}
                {directStorageInfo.nvme_storage === true
                  ? <span style={{ color: "var(--color-gb)" }}>NVMe</span>
                  : directStorageInfo.nvme_storage === false
                    ? <span style={{ color: "var(--color-amber)" }}>not NVMe , limited benefit</span>
                    : <span style={{ color: "var(--color-text-muted)" }}>unknown</span>}
              </div>
            )}

            {selected.dlls.length > 0 && (
              <DllPicker game={selected}
                         onApplied={() => { loadGames(); void handleScanDlssStatus(); }}
                         refreshTrigger={cacheRevision}
                         dlssStatus={dlssStatus}
                         scanningDlss={scanningDlss}
                         scanError={dlssScanError}
                         onScanDlssStatus={handleScanDlssStatus}
                         onRestoreToShipped={handleRestoreDlssToOriginal} />
            )}

            {selected?.appid && gameOverrides && (
              <>
              <div className="gs-group">
                <div className="gs-group-head">
                  <div>
                    <h2 className="gs-group-title">Settings for this game</h2>
                    <p className="gs-group-sub">{`${overrideActiveCount} changed · ${overrideDiffKeys.length} differ from recommended`}</p>
                  </div>
                  <button
                    className="btn-optimize"
                    style={{ padding: "6px 12px", fontSize: 12 }}
                    disabled={savingOverrides || overrideDiffKeys.length === 0}
                    onClick={applyAllRecommended}
                    title={"Set every recommended field (DLSS preset, background shader compiling, "
                      + "performance lock, pause desktop effects"
                      + (VULKAN_LAYER_VISIBLE ? ", pipeline cache" : "")
                      + ") in one write"}
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
                        <option value="">Same as all games{globalRuntimeDefaults ? `: ${dlssPresetLabel(globalRuntimeDefaults.dlss_preset)}` : ""}</option>
                        <option value="render_preset_latest">Latest / Recommended (NVIDIA pick)</option>
                        <option value="render_preset_m">Preset M , RTX 40 / 50</option>
                        <option value="render_preset_k">Preset K , RTX 20 / 30</option>
                        <option value="render_preset_l">Preset L , Max sharpness</option>
                        <option value="default">Default (game decides)</option>
                        <option value="off">Off</option>
                      </select>
                    </div>
                  </div>

                  {/* NIS , via Vulkan layer, hidden while it's alpha-stage and off
                      by default. See VULKAN_LAYER_VISIBLE at the top of this file. */}
                  {VULKAN_LAYER_VISIBLE && (
                    <>
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
                          <SwitchLook on={!!(gameOverrides.nis?.enabled)} />
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
                            <span style={{ fontSize: 12, color: "var(--color-text-dim)", marginLeft: 8 }}>
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
                            <span style={{ fontSize: 12, color: "var(--color-text-dim)", marginLeft: 8 }}>
                              {gameOverrides.nis.scale.toFixed(2)}
                              {gameOverrides.nis.scale >= 1 ? " (no upscale, sharpen only)" : ` (render at ${(gameOverrides.nis.scale * 100).toFixed(0)}%, upscaled back up)`}
                            </span>
                          </div>
                        </div>
                      )}
                    </>
                  )}

                  {/* FPS Cap */}
                  <div className="gs-row" style={{ padding: "10px 16px" }}>
                    <div className="gs-row-label">
                      <div className="gs-row-title">FPS Cap{GS_INFO["FPS Cap (per-game)"] && <InfoTip>{GS_INFO["FPS Cap (per-game)"]}</InfoTip>}</div>
                      <div className="gs-row-sub">Frame-rate limit for this game. DirectX 9–11 games only; it has no effect on DirectX 12.</div>
                    </div>
                    <div className="gs-row-control" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <input type="number" className="settings-current-input" style={{ maxWidth: 100 }}
                             min={0} max={500} step={1}
                             value={gameOverrides.fps_cap} disabled={savingOverrides}
                             onChange={e => {
                               const v = parseInt(e.target.value, 10);
                               if (!isNaN(v) && v >= 0) saveOverrides({ fps_cap: v });
                             }} />
                      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                        {gameOverrides.fps_cap === 0 ? "uncapped" : `${gameOverrides.fps_cap} fps`}
                      </span>
                    </div>
                  </div>
                </CollapsibleSection>

                {/* Latency/Reflex , via VK_NV_low_latency2 in the Vulkan layer,
                    hidden while it's alpha-stage and off by default (the only
                    row this section had, so the section itself is skipped
                    rather than left with an empty header). See
                    VULKAN_LAYER_VISIBLE at the top of this file. */}
                {VULKAN_LAYER_VISIBLE && (
                  <CollapsibleSection title="Latency" defaultOpen={false}>
                    <div className="gs-row" style={{ padding: "10px 16px", cursor: "pointer" }}
                         onClick={() => saveOverrides({ reflex: !gameOverrides.reflex })}>
                      <div className="gs-row-label">
                        <div className="gs-row-title">NVIDIA Reflex (lower input lag){GS_INFO["NVIDIA Reflex"] && <InfoTip>{GS_INFO["NVIDIA Reflex"]}</InfoTip>}</div>
                        <div className="gs-row-sub">Reduces latency via VK_NV_low_latency2</div>
                      </div>
                      <div className="gs-row-control">
                        <SwitchLook on={!!(gameOverrides.reflex)} />
                      </div>
                    </div>
                  </CollapsibleSection>
                )}

                                <CollapsibleSection title="While you play"
                  subtitle="Restored when the game closes"
                  defaultOpen={gameOverrides.game_mode != null}>
                  {([
                    { key: "pause_extensions" as const,  label: "Turn off GNOME extensions", desc: "Extensions like app indicators can stall the game for a moment. They come back when you quit.", global: globalRuntimeDefaults?.game_mode_pause_extensions },
                    { key: "keep_screen_awake" as const, label: "Keep the screen awake",     desc: "No screen blanking or lock while this game runs.", global: globalRuntimeDefaults?.game_mode_keep_awake },
                  ]).map(({ key, label, desc, global }) => {
                    const cur = gameOverrides.game_mode?.[key] ?? null;
                    const set = (v: boolean | null) => {
                      const next = { ...(gameOverrides.game_mode ?? {}) };
                      if (v === null) delete next[key]; else next[key] = v;
                      saveOverrides({ game_mode: Object.keys(next).length ? next : null });
                    };
                    return (
                      <div key={key} className="gs-row" style={{ padding: "8px 16px", alignItems: "center" }}>
                        <div className="gs-row-label">
                          <div className="gs-row-title">{label}</div>
                          <div className="gs-row-sub">{desc}</div>
                        </div>
                        <div className="gs-row-control">
                          <div className="seg" role="group" aria-label={label}>
                            {([[null, `Default${global != null ? ` (${global ? "on" : "off"})` : ""}`], [true, "On"], [false, "Off"]] as const).map(([v, text]) => (
                              <button key={String(v)} type="button" disabled={savingOverrides}
                                      className={`seg-btn${cur === v ? " on" : ""}`} aria-pressed={cur === v}
                                      onClick={() => set(v)}>{text}</button>
                            ))}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </CollapsibleSection>

                <CollapsibleSection title="Runtime overrides"
                  subtitle="Unset follows All games"
                  defaultOpen={(["gplasync", "perf_lock", "compositor_suspend",
                    ...(VULKAN_LAYER_VISIBLE ? ["vk_pipeline_cache" as const] : [])] as const)
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
                      { key: "compositor_suspend", label: "Pause desktop effects while playing", desc: "KDE Plasma only. On GNOME use Turn off GNOME extensions." },
                      // Lives entirely inside greenboost_vulkan_layer.c, same as
                      // the All Games row it mirrors , hidden with it while the
                      // layer defaults off (VULKAN_LAYER_VISIBLE, top of file).
                      // Real gap found 2026-09-02: this per-game override list
                      // was never gated the same way the All Games row was,
                      // so this control kept offering a setting with nothing
                      // behind it while the layer was off.
                      ...(VULKAN_LAYER_VISIBLE ? [
                        { key: "vk_pipeline_cache" as const, label: "Remember compiled shaders", desc: "Persist compiled VkPipelineCache to disk across sessions" },
                      ] : []),
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
                          fontSize: 12, padding: "3px 9px",
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

                <CollapsibleSection title="Overlay"
                  defaultOpen={!!gameOverrides.wrappers?.mangohud}>
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
                      <SwitchLook on={!!(gameOverrides.wrappers?.mangohud)} />
                    </div>
                  </div>
                  <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 16px 0" }}>
                    Gamescope and GameMode can't be started from here: Steam runs this game inside its
                    compatibility container, where neither exists. Add them in the game's Steam launch options.
                  </p>
                </CollapsibleSection>

                {overridesMsg && (
                  <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 16px 0" }}>
                    {overridesMsg}
                  </p>
                )}
              </div>

              <Dlss5Panel
                game={selected}
                appid={selected.appid}
                gameOverrides={gameOverrides}
                saveOverrides={saveOverrides}
              />
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
                <CollapsibleSection title="In-game settings" defaultOpen>
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
