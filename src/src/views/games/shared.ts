// Helpers shared by the Games view and its panels.

export function dlssPresetLabel(v: string): string {
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

