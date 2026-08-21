// Turning a settings write into something a person can read back.
//
// "Smart Defaults" and per-game "Optimize" both apply a batch of changes in
// one click. Before this, both reported success with a single sentence and
// left you to go hunting through the panels to find out what they had
// actually touched , which, for a button whose whole promise is "I picked
// the right values for your hardware", is the one thing you most want to see.
//
// Field keys are the wire format; these maps give them the same names the UI
// uses, so the summary and the settings list agree.

import type { GlobalSettingsState, GameOverrides } from "./types";

/** Global settings field -> the row label used in All Games. */
const GS_FIELD_LABEL: Partial<Record<keyof GlobalSettingsState, string>> = {
  dlss_preset:        "DLSS Model Version",
  dlss_indicator:     "DLSS indicator overlay",
  dlss_upgrade:       "Always use newest DLSS files",
  wayland:            "Wayland",
  hdr:                "HDR (High Dynamic Range)",
  auto_disable_secondary_on_launch: "Cinema mode on launch",
  steam_silent_launch: "Keep Steam out of the way",
  proton_upstream:    "Upstream Proton",
  vk_pipeline_cache:  "Remember compiled shaders",
  vk_queue_priority:  "Give the game GPU priority",
  vk_memory_priority: "Protect game memory under pressure",
  nis_enable:         "NIS sharpening , ready to use",
  nis_dispatch:       "NIS sharpening , actually apply it",
  nis_sharpness:      "Sharpness",
  nis_scale:          "Upscale ratio",
  gl_layer_enabled:   "Enable OpenGL support",
  gl_overflow_min_mb: "Overflow threshold (MB)",
  gplasync:           "Background shader compiling",
  perf_lock:          "Performance lock (CPU + GPU)",
  compositor_suspend: "Pause desktop effects while playing",
  ddr_prewarm:        "Pre-warm overflow memory",
  memlock_unlimited:  "Remove memory-locking limit",
  mangohud_enabled:   "Performance overlay (GPU + FPS)",
  nvapi_hud:          "Show NVIDIA feature status overlay",
  reflex_enable:      "NVIDIA Reflex (lower input lag)",
  fps_cap:            "FPS cap",
  vk_debug:           "Verbose Vulkan logging",
  vk_overflow_min_mb: "VRAM headroom before overflow (MB)",
  vk_t3_min_mb:       "Minimum reserved disk space (MB)",
  log_ttl_days:       "Keep session logs for (days)",
  shader_threads:     "Shader compile threads",
  shader_cache_gb:    "Shader cache size limit (GB)",
  gplasync_version:   "Pin a specific shader-compiler version",
  vkd3d_config:       "DirectX 12 feature flags",
  perf_mode:          "Performance mode",
};

/** Per-game override field -> the row label used in This Game. */
const GAME_FIELD_LABEL: Partial<Record<keyof GameOverrides, string>> = {
  dlss_preset:        "DLSS Model Version",
  gplasync:           "Background shader compiling",
  perf_lock:          "Performance lock (CPU + GPU)",
  compositor_suspend: "Pause desktop effects while playing",
  vk_pipeline_cache:  "Remember compiled shaders",
  reflex:             "NVIDIA Reflex (lower input lag)",
  hdr:                "HDR",
  fps_cap:            "FPS cap",
  governor:           "CPU Governor",
  gpu_profile:        "GPU Profile",
  nis:                "NIS (NVIDIA Image Scaling)",
  wrappers:           "Launch wrappers",
};

const DLSS_PRESET_LABEL: Record<string, string> = {
  "":                     "Use global",
  "auto":                 "Auto (best for your GPU)",
  "render_preset_latest": "Latest / Recommended",
  "render_preset_m":      "Preset M",
  "render_preset_k":      "Preset K",
  "render_preset_l":      "Preset L",
  "default":              "Default (game decides)",
  "off":                  "Off",
};

export interface Change {
  label: string;
  from: string;
  to: string;
}

/** Render a value the way the UI would, not the way JSON would. */
function fmt(field: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "not set";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (field === "dlss_preset" && typeof v === "string") {
    return DLSS_PRESET_LABEL[v] ?? v;
  }
  if (field === "nis_scale" && typeof v === "number") {
    return v >= 1 ? "off (100%)" : `${(v * 100).toFixed(0)}%`;
  }
  if (field === "fps_cap" && v === 0) return "uncapped";
  if (field === "shader_threads" && v === 0) return "auto";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/**
 * Diff a patch against the values it is replacing.
 *
 * Only fields that actually change are returned , a summary listing settings
 * that were already correct reads as noise and hides the real change.
 */
export function summarizeGlobal(
  before: GlobalSettingsState | null,
  patch: Partial<GlobalSettingsState>,
): Change[] {
  const out: Change[] = [];
  for (const [key, next] of Object.entries(patch)) {
    const prev = before ? (before as any)[key] : undefined;
    if (Object.is(prev, next)) continue;
    out.push({
      label: GS_FIELD_LABEL[key as keyof GlobalSettingsState] ?? key,
      from: fmt(key, prev),
      to: fmt(key, next),
    });
  }
  return out;
}

export function summarizeGame(
  before: GameOverrides | null,
  patch: Partial<GameOverrides>,
): Change[] {
  const out: Change[] = [];
  for (const [key, next] of Object.entries(patch)) {
    const prev = before ? (before as any)[key] : undefined;
    // wrappers is a nested object; report the one field Optimize touches
    // rather than dumping JSON at the reader.
    if (key === "wrappers") {
      const pg = (prev as any)?.gamemode ?? false;
      const ng = (next as any)?.gamemode ?? false;
      if (pg !== ng) {
        out.push({ label: "GameMode", from: fmt("b", pg), to: fmt("b", ng) });
      }
      continue;
    }
    if (JSON.stringify(prev) === JSON.stringify(next)) continue;
    out.push({
      label: GAME_FIELD_LABEL[key as keyof GameOverrides] ?? key,
      from: fmt(key, prev),
      to: fmt(key, next),
    });
  }
  return out;
}
