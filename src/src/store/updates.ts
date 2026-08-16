// Shared release-update state for the Suite and GreenBoost core.
//
// Lives in a store rather than a view's useState for the same reason as
// global settings, plus one specific to this feature: the check is a network
// round-trip against gitlab.com, and it runs once on app start. If Status
// and About each owned their own copy, navigating between them would fire a
// second and third request for a result that hasn't changed and, by design,
// only refreshes every six hours.

import { invoke } from "@tauri-apps/api/core";
import { createStore, useStore } from "./observable";

export interface ComponentUpdate {
  key: string;
  name: string;
  installed: string | null;
  latest: string | null;
  update_available: boolean;
  release_url: string;
  notes: string;
  released_at: string | null;
  error: string | null;
  not_installed: boolean;
}

export interface UpdateReport {
  suite: ComponentUpdate;
  core: ComponentUpdate;
  checked_at: number;
  from_cache: boolean;
  advice: string;
}

export const updateReport = createStore<UpdateReport | null>(null);
export const updateChecking = createStore<boolean>(false);

let inFlight: Promise<UpdateReport | null> | null = null;

/**
 * Run the check.
 *
 * `force` bypasses the backend's 6-hour disk cache , that's the "Check now"
 * button. The automatic check on app start passes false, so a user who opens
 * the app ten times in an afternoon still only reaches GitLab once.
 */
export function checkForUpdates(force = false): Promise<UpdateReport | null> {
  if (inFlight) return inFlight;
  updateChecking.set(true);

  inFlight = invoke<UpdateReport>("check_updates", { force })
    .then(r => { updateReport.set(r); return r; })
    // A failed update check must never surface as a broken app. The backend
    // already folds per-component failures into `error` fields; this catch
    // only fires if the command itself is unavailable.
    .catch(() => updateReport.get())
    .finally(() => { inFlight = null; updateChecking.set(false); });

  return inFlight;
}

export function useUpdateReport(): UpdateReport | null {
  return useStore(updateReport);
}

export function useUpdateChecking(): boolean {
  return useStore(updateChecking);
}

/** True when either component has a newer release , drives the sidebar dot. */
export function hasAnyUpdate(r: UpdateReport | null): boolean {
  return !!r && (r.suite.update_available || r.core.update_available);
}
