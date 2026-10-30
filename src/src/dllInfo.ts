// Shared, dependency-free DLL metadata , used by both Games.tsx (the
// global DLSS LIBRARIES table) and DllPicker.tsx (the per-game library
// list). Kept in its own module specifically so DllPicker doesn't have to
// import from Games.tsx, which would create a circular import (Games.tsx
// already imports DllPicker).

// nvngx_dlssnr.dll / sl.dlss_nr.dll (DLSS Neural Rendering) and the four
// entries after them were added 2026-08-28 after finding them shipped in
// "FINAL FANTASY VII REBIRTH" and in a leaked DLSS 5 / Streamline 2.13
// bundle. See gb_gaming/dlss_updater.py's KNOWN table for why Neural
// Rendering has no official download source yet , recognised here for
// typing/inspection, not auto-fetch.
export const DLL_ORDER = [
  "nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll", "nvngx_dlssnr.dll",
  "sl.dlss.dll", "sl.dlss_g.dll", "sl.dlss_d.dll", "sl.dlss_nr.dll",
  "sl.reflex.dll", "sl.common.dll",
  "sl.interposer.dll", "sl.nis.dll", "sl.pcl.dll",
  "nvngx_deepdvc.dll", "sl.deepdvc.dll", "sl.directsr.dll", "sl.nvperf.dll",
];

export const DLL_TYPE: Record<string, string> = {
  "nvngx_dlss.dll":   "DLSS Super Resolution",
  "nvngx_dlssg.dll":  "DLSS Frame Generation",
  "nvngx_dlssd.dll":  "DLSS Ray Reconstruction",
  "nvngx_dlssnr.dll": "DLSS Neural Rendering",
  "sl.dlss.dll":      "Streamline DLSS SR",
  "sl.dlss_g.dll":    "Streamline DLSS FG",
  "sl.dlss_d.dll":    "Streamline DLSS RR",
  "sl.dlss_nr.dll":   "Streamline DLSS Neural Rendering",
  "sl.reflex.dll":    "Streamline Reflex",
  "sl.common.dll":    "Streamline Core",
  "sl.interposer.dll":"Streamline Interposer",
  "sl.nis.dll":       "Streamline NIS",
  "sl.pcl.dll":       "Streamline PCL",
  "nvngx_deepdvc.dll":"Deep DVC",
  "sl.deepdvc.dll":   "Streamline Deep DVC",
  "sl.directsr.dll":  "Streamline DirectSR",
  "sl.nvperf.dll":    "Streamline NvPerf",
};

// Plain-language explanation for the (i) info popup , what each library
// actually does, not what its filename means.
export const DLL_EXPLAIN: Record<string, string> = {
  "nvngx_dlss.dll":
    "NVIDIA's AI upscaler. Renders the game at a lower internal resolution, "
    + "then uses a trained neural network to reconstruct a sharp, "
    + "full-resolution image , higher framerate with only a small, often "
    + "unnoticeable quality trade-off.",
  "nvngx_dlssg.dll":
    "Uses AI to generate entire extra frames between the ones your GPU "
    + "actually renders, roughly doubling the frames per second you see. "
    + "Adds a small amount of input lag, which NVIDIA Reflex is designed "
    + "to offset.",
  "nvngx_dlssd.dll":
    "Improves the quality of ray-traced lighting and reflections by using "
    + "AI to clean up noise, instead of an older fixed-function denoiser. "
    + "Makes ray tracing look better at the same performance cost.",
  "sl.dlss.dll":
    "The Streamline-packaged version of DLSS Super Resolution , same "
    + "feature as nvngx_dlss.dll, delivered through NVIDIA's newer "
    + "Streamline framework, which some newer games use instead.",
  "sl.dlss_g.dll":
    "The Streamline-packaged version of DLSS Frame Generation , same "
    + "feature as nvngx_dlssg.dll, delivered through Streamline.",
  "sl.dlss_d.dll":
    "The Streamline-packaged version of DLSS Ray Reconstruction , same "
    + "feature as nvngx_dlssd.dll, delivered through Streamline.",
  "sl.reflex.dll":
    "NVIDIA Reflex , reduces the delay between your input and what "
    + "appears on screen, by having the driver pace how far ahead your "
    + "CPU gets from your GPU. Especially useful paired with Frame "
    + "Generation, which otherwise adds lag.",
  "sl.common.dll":
    "The shared plumbing NVIDIA's other Streamline components need to "
    + "talk to the game and the graphics driver. Not a feature by itself , "
    + "required for the others here to work.",
  "sl.interposer.dll":
    "Routes the game's rendering calls through NVIDIA's Streamline "
    + "framework so the other Streamline features can hook in. "
    + "Infrastructure, not a feature by itself.",
  "sl.nis.dll":
    "NVIDIA Image Scaling , a simpler, non-AI upscaler and sharpener. "
    + "Works on any GPU, not just NVIDIA , a good fallback when DLSS "
    + "isn't available or supported by a game.",
  "sl.pcl.dll":
    "Performance/telemetry plumbing Streamline components use internally "
    + "to report frame-timing data. Infrastructure, not a user-facing "
    + "feature.",
  "nvngx_dlssnr.dll":
    "NVIDIA's newest neural rendering technique (DLSS 5), which asks the "
    + "AI model for updated lighting and materials, not just an upscaled "
    + "image. Not officially released yet , placing this file in a game "
    + "does nothing on its own unless something (like a RenoDX mod) makes "
    + "the game call into it.",
  "sl.dlss_nr.dll":
    "The Streamline-packaged version of DLSS Neural Rendering , same "
    + "caveat as nvngx_dlssnr.dll: presence alone changes nothing without "
    + "a mod that redirects the game's rendering calls to it.",
  "nvngx_deepdvc.dll":
    "NVIDIA Deep Dynamic Vibrance , an AI-driven color/contrast "
    + "enhancement, separate from DLSS upscaling or frame generation.",
  "sl.deepdvc.dll":
    "The Streamline-packaged version of Deep Dynamic Vibrance.",
  "sl.directsr.dll":
    "Microsoft's DirectSR abstraction layer as exposed through Streamline "
    + ", lets a game call one upscaling API that Streamline then routes to "
    + "DLSS or another vendor's upscaler.",
  "sl.nvperf.dll":
    "NVIDIA performance-counter plumbing Streamline uses internally for "
    + "profiling. Infrastructure, not a user-facing feature.",
};
