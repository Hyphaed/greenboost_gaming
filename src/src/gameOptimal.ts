import type { GameOverrides, GameWrappers } from "./types";

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
  wrappers: {
    gamemode: true,
  },
} as const;

/// Keys of GameOverrides that `diffFromOptimal`/`countActiveOverrides`
/// examine , "wrappers" stands in for `wrappers.gamemode` specifically,
/// since that's the only wrapper field with a recommended value.
type OptimalKey = "dlss_preset" | "gplasync" | "perf_lock" | "compositor_suspend"
  | "vk_pipeline_cache" | "wrappers";

/// Returns the GameOverrides keys that differ from OPTIMAL_OVERRIDES.
export function diffFromOptimal(o: GameOverrides): OptimalKey[] {
  const out: OptimalKey[] = [];
  if (o.dlss_preset        !== OPTIMAL_OVERRIDES.dlss_preset)        out.push("dlss_preset");
  if (o.gplasync           !== OPTIMAL_OVERRIDES.gplasync)           out.push("gplasync");
  if (o.perf_lock          !== OPTIMAL_OVERRIDES.perf_lock)          out.push("perf_lock");
  if (o.compositor_suspend !== OPTIMAL_OVERRIDES.compositor_suspend) out.push("compositor_suspend");
  if (o.vk_pipeline_cache  !== OPTIMAL_OVERRIDES.vk_pipeline_cache)  out.push("vk_pipeline_cache");
  if ((o.wrappers?.gamemode ?? false) !== OPTIMAL_OVERRIDES.wrappers.gamemode) out.push("wrappers");
  return out;
}

/// Builds the full GameOverrides patch to bring every OPTIMAL_OVERRIDES
/// field to its recommended value in one write , used by both the
/// per-field "Recommended" chips (one key at a time) and the "Apply all
/// recommended" button (every key at once).
export function optimalPatch(current: GameOverrides, only?: OptimalKey[]): Partial<GameOverrides> {
  const keys = only ?? (Object.keys(OPTIMAL_OVERRIDES) as OptimalKey[]);
  const patch: Partial<GameOverrides> = {};
  for (const key of keys) {
    if (key === "wrappers") {
      const wrappers: GameWrappers = {
        gamemode: OPTIMAL_OVERRIDES.wrappers.gamemode,
        mangohud: current.wrappers?.mangohud ?? false,
        gamescope: current.wrappers?.gamescope ?? [],
      };
      patch.wrappers = wrappers;
    } else {
      (patch as any)[key] = OPTIMAL_OVERRIDES[key];
    }
  }
  return patch;
}

/// How many overrides are explicitly set away from their neutral/global
/// default , the "N overrides active" count in the Game Settings header.
/// This counts ANY explicit override, not just the recommended set (e.g. a
/// deliberately-off Reflex or a custom gamescope arg still counts).
export function countActiveOverrides(o: GameOverrides): number {
  let n = 0;
  if (o.dlss_preset)                 n++;
  if (o.governor)                    n++;
  if (o.gpu_profile)                 n++;
  if (o.gplasync !== null)           n++;
  if (o.perf_lock !== null)          n++;
  if (o.compositor_suspend !== null) n++;
  if (o.vk_pipeline_cache !== null)  n++;
  if (o.reflex)                      n++;
  if (o.hdr)                         n++;
  if (o.nis)                         n++;
  if (o.fps_cap !== 0)               n++;
  if (o.wrappers?.gamemode)          n++;
  if (o.wrappers?.mangohud)          n++;
  if ((o.wrappers?.gamescope?.length ?? 0) > 0) n++;
  return n;
}
