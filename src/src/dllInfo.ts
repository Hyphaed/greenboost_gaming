// Shared, dependency-free DLL metadata , used by both Games.tsx (the
// global DLSS LIBRARIES table) and DllPicker.tsx (the per-game library
// list). Kept in its own module specifically so DllPicker doesn't have to
// import from Games.tsx, which would create a circular import (Games.tsx
// already imports DllPicker).

export const DLL_ORDER = [
  "nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll",
  "sl.dlss.dll", "sl.dlss_g.dll", "sl.dlss_d.dll",
  "sl.reflex.dll", "sl.common.dll",
  "sl.interposer.dll", "sl.nis.dll", "sl.pcl.dll",
];

export const DLL_TYPE: Record<string, string> = {
  "nvngx_dlss.dll":   "DLSS Super Resolution",
  "nvngx_dlssg.dll":  "DLSS Frame Generation",
  "nvngx_dlssd.dll":  "DLSS Ray Reconstruction",
  "sl.dlss.dll":      "Streamline DLSS SR",
  "sl.dlss_g.dll":    "Streamline DLSS FG",
  "sl.dlss_d.dll":    "Streamline DLSS RR",
  "sl.reflex.dll":    "Streamline Reflex",
  "sl.common.dll":    "Streamline Core",
  "sl.interposer.dll":"Streamline Interposer",
  "sl.nis.dll":       "Streamline NIS",
  "sl.pcl.dll":       "Streamline PCL",
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
};
