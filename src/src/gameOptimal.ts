import type { GameOverrides } from "./types";
import { VULKAN_LAYER_VISIBLE } from "./featureFlags";

/// Single source of truth for "the recommended per-game override set" ,
/// this used to be duplicated between Games.tsx's `anyNeedsChange` (what
/// drives the "Not optimized" badge) and `handleOptimize` (what the
/// Optimize button actually writes). Two independent copies meant they
/// could silently drift; both now read from here instead.
export const OPTIMAL_OVERRIDES = {
  dlss_preset:        "render_preset_latest",
  gplasync:           true,
  perf_lock:          true,
  compositor_suspend: true,
  vk_pipeline_cache:  true,
} as const;

/// Keys of GameOverrides that `diffFromOptimal` examines. "GameMode" used to
/// be recommended here too, but it can't run inside Steam's compatibility
/// container, so recommending it left the badge stuck on "Not optimized".
type OptimalKey = "dlss_preset" | "gplasync" | "perf_lock" | "compositor_suspend"
  | "vk_pipeline_cache";

/// vk_pipeline_cache lives entirely inside greenboost_vulkan_layer.c (same
/// as reflex/nis, see countActiveOverrides below), which now defaults off
/// , see featureFlags.ts. Real gap found 2026-09-02: this module kept
/// counting it as "differs from recommended" and the "Apply all
/// recommended" button kept writing it, with no visible row anywhere for
/// the user to have noticed it mattered. Excluded from the diff/apply set
/// while the layer is hidden; OPTIMAL_OVERRIDES itself is untouched so the
/// recommendation is ready the moment VULKAN_LAYER_VISIBLE flips back.
const HIDDEN_OPTIMAL_KEYS: readonly OptimalKey[] =
  VULKAN_LAYER_VISIBLE ? [] : ["vk_pipeline_cache"];

/// Returns the GameOverrides keys that differ from OPTIMAL_OVERRIDES.
export function diffFromOptimal(o: GameOverrides): OptimalKey[] {
  const out: OptimalKey[] = [];
  if (o.dlss_preset        !== OPTIMAL_OVERRIDES.dlss_preset)        out.push("dlss_preset");
  if (o.gplasync           !== OPTIMAL_OVERRIDES.gplasync)           out.push("gplasync");
  if (o.perf_lock          !== OPTIMAL_OVERRIDES.perf_lock)          out.push("perf_lock");
  if (o.compositor_suspend !== OPTIMAL_OVERRIDES.compositor_suspend) out.push("compositor_suspend");
  if (VULKAN_LAYER_VISIBLE && o.vk_pipeline_cache !== OPTIMAL_OVERRIDES.vk_pipeline_cache)
    out.push("vk_pipeline_cache");
  return out;
}

/// Builds the full GameOverrides patch to bring every OPTIMAL_OVERRIDES
/// field to its recommended value in one write , used by both the
/// per-field "Recommended" chips (one key at a time) and the "Apply all
/// recommended" button (every key at once).
export function optimalPatch(_current: GameOverrides, only?: OptimalKey[]): Partial<GameOverrides> {
  const keys = (only ?? (Object.keys(OPTIMAL_OVERRIDES) as OptimalKey[]))
    .filter(k => !HIDDEN_OPTIMAL_KEYS.includes(k));
  const patch: Partial<GameOverrides> = {};
  for (const key of keys) {
    {
      (patch as any)[key] = OPTIMAL_OVERRIDES[key];
    }
  }
  return patch;
}

/// How many overrides are explicitly set away from their neutral/global
/// default , the "N overrides active" count in the Game Settings header.
/// This counts ANY explicit override, not just the recommended set (e.g. a
/// deliberately-off Reflex or a custom gamescope arg still counts).
///
/// vk_pipeline_cache/reflex/nis are excluded while VULKAN_LAYER_VISIBLE is
/// false , all three live entirely inside greenboost_vulkan_layer.c (see
/// featureFlags.ts) and have no visible row anywhere right now. Counting
/// them here would show "N active" for overrides the user has no way to
/// see or change, which is the same "offers something with nothing behind
/// it" problem as an actual hidden row.
export function countActiveOverrides(o: GameOverrides): number {
  let n = 0;
  if (o.dlss_preset)                 n++;
  if (o.gpu_profile)                 n++;
  if (o.gplasync !== null)           n++;
  if (o.perf_lock !== null)          n++;
  if (o.compositor_suspend !== null) n++;
  if (VULKAN_LAYER_VISIBLE && o.vk_pipeline_cache !== null) n++;
  if (VULKAN_LAYER_VISIBLE && o.reflex)                     n++;
  if (VULKAN_LAYER_VISIBLE && o.nis)                        n++;
  if (o.fps_cap !== 0)               n++;
  if (o.wrappers?.mangohud)          n++;
  return n;
}
