// The single copy of GlobalSettings for the whole app.
//
// Before this, `get_global_settings` was invoked in six places across four
// views, each into a private useState. Changing a setting in All Games left
// About, Profile and the Games launch handler holding stale values until
// their view happened to remount. The launch handler worked around it by
// re-reading the backend on every click , a fetch that is now unnecessary,
// because the store is always current.
//
// Contract:
//   * `loadGlobalSettings()` is idempotent and safe to call from anywhere;
//     concurrent callers share one in-flight request.
//   * `patchGlobalSettings()` is the ONLY way to write. It applies
//     optimistically so the UI responds immediately, persists, and rolls
//     back to the server's truth if the write fails.
//   * Nothing outside this module calls save_global_settings.

import { invoke } from "@tauri-apps/api/core";
import type { GlobalSettingsState } from "../types";
import { createStore, useStore } from "./observable";

/** null = not loaded yet. Views must handle that rather than assume defaults. */
export const globalSettings = createStore<GlobalSettingsState | null>(null);

/** Last write error, for views that want to surface it. Cleared on success. */
export const globalSettingsError = createStore<string | null>(null);

let inFlight: Promise<GlobalSettingsState | null> | null = null;

/**
 * Load settings into the store. Returns the loaded value.
 *
 * Callers that just need "make sure this is populated" can fire and forget;
 * simultaneous callers (several views mounting at once on app start) share
 * the same request rather than racing six identical ones.
 */
export function loadGlobalSettings(force = false): Promise<GlobalSettingsState | null> {
  if (!force && globalSettings.get() !== null) {
    return Promise.resolve(globalSettings.get());
  }
  if (inFlight) return inFlight;

  inFlight = invoke<GlobalSettingsState>("get_global_settings")
    .then(s => { globalSettings.set(s); globalSettingsError.set(null); return s; })
    .catch(e => {
      globalSettingsError.set(`Load failed: ${e?.message ?? e}`);
      return globalSettings.get();
    })
    .finally(() => { inFlight = null; });

  return inFlight;
}

/**
 * Apply a partial change and persist it.
 *
 * Optimistic: subscribers see the new value before the round-trip finishes,
 * which is what makes a toggle feel instant. On failure we re-read the
 * backend rather than just restoring the previous local value , if the write
 * partially applied, the backend is the only thing that knows the truth.
 */
export async function patchGlobalSettings(
  patch: Partial<GlobalSettingsState>,
): Promise<void> {
  const before = globalSettings.get();
  if (!before) {
    // Nothing loaded yet: load, then re-apply against real data rather than
    // inventing a base object and writing invented defaults to disk.
    const loaded = await loadGlobalSettings();
    if (!loaded) throw new Error("settings not loaded");
    return patchGlobalSettings(patch);
  }

  const next = { ...before, ...patch };
  globalSettings.set(next);

  try {
    await invoke("save_global_settings", { settings: next });
    globalSettingsError.set(null);
  } catch (e: any) {
    globalSettingsError.set(`Save failed: ${e?.message ?? e}`);
    await loadGlobalSettings(true);
    throw e;
  }
}

/**
 * Replace the whole object (loading a saved profile). Same persist-and-verify
 * path as patch, so profile loads can't diverge from single-setting writes.
 */
export async function replaceGlobalSettings(next: GlobalSettingsState): Promise<void> {
  const before = globalSettings.get();
  globalSettings.set(next);
  try {
    await invoke("save_global_settings", { settings: next });
    globalSettingsError.set(null);
  } catch (e: any) {
    globalSettingsError.set(`Save failed: ${e?.message ?? e}`);
    if (before) globalSettings.set(before);
    await loadGlobalSettings(true);
    throw e;
  }
}

/** Subscribe a component to the shared settings. */
export function useGlobalSettings(): GlobalSettingsState | null {
  return useStore(globalSettings);
}

export function useGlobalSettingsError(): string | null {
  return useStore(globalSettingsError);
}
