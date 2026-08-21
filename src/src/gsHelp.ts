// Plain-language (i)-popup content for Global Settings + per-game GreenBoost
// Overrides rows. Same role as DLL_EXPLAIN in dllInfo.ts, kept in its own
// module for the same reason: keeps Games.tsx from growing a second wall of
// prose. Content here goes a level deeper than each row's `sub` line ,
// sub says what a setting does, this says why you'd want it on/off, the
// trade-off, or a gotcha , never a restatement of sub.
//
// Keys are the exact row label string passed as row()'s first argument, or
// the plain-text title used in a hand-written GREENBOOST OVERRIDES row.

export const GS_INFO: Record<string, string> = {
  // ── DRIVER SETTINGS ──────────────────────────────────────────────────
  "DLSS Model Version":
    "Newer model versions are almost always better at the same performance "
    + "cost , NVIDIA rarely regresses image quality between releases. The "
    + "one reason to pin an older one deliberately is if a specific game "
    + "looks worse or more \"swimmy\" on the newest model, which does "
    + "happen occasionally.",
  "HDR (High Dynamic Range)":
    "A non-HDR monitor reading an HDR signal typically looks washed out "
    + "or grey, not just \"less good\" , this isn't subtle. If you're not "
    + "certain your display is HDR-capable, leave this off.",
  "Upstream Proton":
    "Leave this on Automatic unless a game misbehaves. It exists for two "
    + "cases: you run a distro Proton build (Proton-CachyOS, Proton-GE, "
    + "proton-tkg) that automatic detection would pass over in favour of "
    + "Valve's, or a specific title only works on one particular Proton. "
    + "Changing it affects every game you launch through GreenBoost, so a "
    + "per-game fix belongs in that game's own compatibility setting in "
    + "Steam instead.",
  "Keep Steam out of the way":
    "How well this works depends on your desktop, and it is worth knowing "
    + "which one you have. If Steam was not already running, it starts in "
    + "the tray and no Steam window ever appears , that part works "
    + "everywhere. If Steam was already open, minimising its window needs "
    + "the desktop's cooperation: X11 and KDE allow it, GNOME on Wayland "
    + "does not let any application touch another application's window, so "
    + "there the Steam window stays where you left it. Either way the tray "
    + "icon remains, because Steam has no option to hide it.",
  "Wayland":
    "Overriding the auto-detected value only matters if a specific game "
    + "renders incorrectly or won't launch under your current session type "
    + ", forcing the other mode is a troubleshooting step, not a "
    + "performance one.",
  "DLSS indicator overlay":
    "Purely diagnostic , it doesn't change how the game plays or looks. "
    + "Turn it on once after changing the DLSS preset to confirm the game "
    + "actually picked it up, then turn it back off.",
  "Always use newest DLSS files":
    "Applies per-game, automatically, every launch , you don't need to "
    + "manually update each game's DLL yourself. The rare downside: a "
    + "brand-new DLSS build can occasionally be less stable in one "
    + "specific title before NVIDIA patches it.",
  "Close to system tray":
    "With this on, the Suite keeps running in the background after you close "
    + "the window, so it can still watch the game and stop it for you. Turn "
    + "it off and closing the window exits the Suite outright, the way it "
    + "behaved before , the game then keeps running with nothing supervising "
    + "it. If your desktop has no tray, the Suite falls back to exiting and "
    + "tells you so.",

  "Stop the game when you quit":
    "The way Steam behaves: quitting the Suite also closes the game it "
    + "launched. The game is asked to exit first and only force-killed if it "
    + "ignores that, so saves get flushed. Wine's own background processes "
    + "are left alone, so another game running in a different prefix is not "
    + "affected.",

  "Cinema mode on launch":
    "Useful if a game's fullscreen detection gets confused by a second "
    + "monitor and launches windowed or at the wrong resolution. GreenBoost "
    + "does not turn your other monitors back on for you afterward , that's "
    + "a manual step on the Displays page.",

  // ── VULKAN LAYER ─────────────────────────────────────────────────────
  "Remember compiled shaders":
    "The cache is per-game and grows over time , see \"Shader cache size "
    + "limit\" below to cap how much disk space it's allowed to use. Only "
    + "helps on your SECOND time playing a given scene; the very first "
    + "encounter with new shaders always compiles fresh.",
  "Give the game GPU priority":
    "Mainly matters if you have other GPU-heavy apps running at the same "
    + "time (a second monitor's compositor effects, a background render, "
    + "local AI inference). On a machine only running the game, this has "
    + "little visible effect.",
  "Protect game memory under pressure":
    "Only relevant if you also run GreenBoost AI inference workloads on "
    + "this machine while gaming , without that, there's nothing competing "
    + "for VRAM in the first place, so this setting has nothing to do.",
  "NIS sharpening , ready to use":
    "Just prepares the shaders , costs nothing in performance until you "
    + "also turn on \"NIS sharpening , actually apply it\" below. Safe to "
    + "leave on permanently.",
  "NIS sharpening , actually apply it":
    "Adds a small per-frame GPU cost for the sharpen pass itself. Pair it "
    + "with the Upscale ratio slider in the NIS section below set under "
    + "100% for an actual performance gain, not just sharper visuals.",

  // ── OPENGL LAYER ─────────────────────────────────────────────────────
  "Enable OpenGL support":
    "Most modern Proton games use Vulkan or DirectX (translated to Vulkan "
    + "via vkd3d-proton) already, so this mainly matters for older or "
    + "indie titles still using OpenGL directly. No downside to leaving it "
    + "on.",
  "Overflow threshold (MB)":
    "Set this lower only if a specific OpenGL game shows visible texture "
    + "pop-in or stutter you suspect is overflow-related , the default is "
    + "tuned for typical texture/buffer sizes and rarely needs touching.",

  // ── PROTON + SYSTEM ──────────────────────────────────────────────────
  "Background shader compiling":
    "This is the single biggest fix for Proton's \"brief freeze the first "
    + "time something new appears\" problem , leave it on unless you're "
    + "specifically debugging a shader-related crash, since it changes "
    + "compile timing.",
  "Performance lock (CPU + GPU)":
    "Trades power draw and fan noise for consistency , your GPU/CPU stop "
    + "ramping up and down with load, which is what actually causes "
    + "micro-stutter on some systems. On a laptop, expect noticeably "
    + "shorter battery life while a game is running.",
  "Pause desktop effects while playing":
    "The gain is small but real on compositor-heavy desktops (KDE Plasma "
    + "with effects, GNOME with extensions) , negligible if you already "
    + "run a lightweight desktop.",
  "Pre-warm overflow memory":
    "Avoids a one-time stutter the first moment a game needs more than "
    + "your GPU's real VRAM , the trade-off is a few seconds of extra "
    + "memory setup work at launch, before you're even in a menu.",
  "Remove memory-locking limit":
    "Needed for some of GreenBoost's memory tricks to actually take "
    + "effect; on systems where the limit can't be lifted (some hardened "
    + "kernels), this silently does nothing rather than failing loudly , "
    + "check journalctl if you suspect it isn't applying.",
  "Performance overlay (GPU + FPS)":
    "Works identically across DirectX 9-12 and native Vulkan, unlike the "
    + "NVIDIA-specific overlay below. Requires the separate \"mangohud\" "
    + "package to already be installed on your system , GreenBoost doesn't "
    + "bundle it.",
  "Show NVIDIA feature status overlay":
    "Only shows real data for DXVK (DirectX 9/10/11) and native Vulkan "
    + "games , vkd3d-proton (DirectX 12) has no equivalent hook, so this "
    + "silently shows nothing for DX12 titles. The Performance overlay "
    + "above works on every game instead.",

  // ── FRAME PACING + LATENCY ───────────────────────────────────────────
  "NVIDIA Reflex (lower input lag)":
    "Reflex works by holding the CPU back slightly so it doesn't get too "
    + "far ahead of the GPU , the felt effect is smaller responsiveness "
    + "gains, not raw FPS. Most noticeable in fast, twitchy games; not "
    + "something you'll feel in a turn-based or slow-paced title.",
  "FPS cap":
    "A cap a few frames below your monitor's refresh rate paired with "
    + "Reflex above tends to feel smoother and lower-lag than uncapped , "
    + "counterintuitive, but this is a well-known Reflex-specific "
    + "interaction, not a general rule.",

  // ── NIS SECTION ──────────────────────────────────────────────────────
  "Sharpness":
    "Push this too high and you'll start seeing a visible \"halo\" or "
    + "ringing around high-contrast edges , if that happens, back it off "
    + "a bit rather than assuming NIS itself looks bad.",
  "Upscale ratio":
    "This is where the actual performance win comes from , Sharpness "
    + "alone doesn't speed anything up, it just sharpens whatever "
    + "resolution the game is already rendering at.",

  // ── ADVANCED ──────────────────────────────────────────────────────────
  "Verbose Vulkan logging":
    "Generates a large volume of log data very quickly , fine for a short "
    + "troubleshooting session, but leaving it on long-term will fill your "
    + "disk with logs you'll never read.",
  "VRAM headroom before overflow (MB)":
    "Set this higher if you're seeing GPU-memory-related crashes even "
    + "with overflow enabled , it means GreenBoost is cutting things too "
    + "close to your card's real limit for this specific game.",
  "Minimum reserved disk space (MB)":
    "This is the last resort tier, after both your graphics card and "
    + "system RAM are full , reserving space here only matters on systems "
    + "that are genuinely pushing memory limits, which is uncommon.",
  "Keep session logs for (days)":
    "Purely a disk-hygiene setting , has no effect on gameplay or "
    + "performance either way.",
  "Shader compile threads":
    "0 (auto) reads your actual CPU topology and picks a sensible number "
    + ", manually overriding this is really only useful if you're running "
    + "something else CPU-heavy at the same time and want to leave it more "
    + "headroom.",
  "Shader cache size limit (GB)":
    "Once the cache hits this limit, GreenBoost clears out the oldest "
    + "entries to make room , a smaller limit means more games will need "
    + "to recompile shaders they've already compiled before.",
  "Pin a specific shader-compiler version":
    "Only useful if a specific dxvk-gplasync release introduced a "
    + "regression for you , pinning lets you roll back without losing the "
    + "background-compile feature entirely.",
  "DirectX 12 feature flags":
    "These are the same flags vkd3d-proton exposes directly , DXR ray "
    + "tracing only helps in games that support it, and Pipeline "
    + "cache/Descriptor buffer reduce first-encounter stutter the same way "
    + "\"Background shader compiling\" does, but specifically for DX12 "
    + "titles.",

  // ── GREENBOOST OVERRIDES (per-game) ─────────────────────────────────
  "DLSS Preset":
    "Lets this one game use a different DLSS model than your global "
    + "default , useful if the global recommendation looks worse in this "
    + "specific title. \"Use Global\" always tracks whatever you set above, "
    + "even if you change it later.",
  "FPS Cap (per-game)":
    "Overrides the global FPS cap number above for just this game , handy "
    + "for a competitive title where you want a strict cap regardless of "
    + "your usual setting elsewhere.",
  "NVIDIA Reflex":
    "Same feature as the global Reflex toggle, scoped to this game only "
    + ", use this if a specific title has a known issue with Reflex "
    + "enabled, without turning it off everywhere else.",
  "NIS (NVIDIA Image Scaling)":
    "Independent of the global NIS toggles above , a game can have its "
    + "own sharpness/scale values here even while the global default is "
    + "off, or vice versa.",
  "HDR":
    "Overrides the global HDR toggle for this game specifically , useful "
    + "for the handful of titles with broken or flickery HDR "
    + "implementations you want to force off individually.",
  "CPU Governor":
    "Overrides the global CPU governor for just this game , rarely "
    + "needed unless a specific title behaves oddly under \"performance\" "
    + "mode (some older engines have CPU-frequency-sensitive timing bugs).",
  "GPU Profile":
    "Applies a saved overclock + fan curve automatically the moment this "
    + "game launches, and nothing changes for any other game , build the "
    + "profile itself from the GPU tuning page first.",
  "dxvk-gplasync":
    "\"Global\" always follows whatever the main Background shader "
    + "compiling toggle is set to , pick ON/OFF here only to lock this "
    + "specific game's behavior regardless of future global changes.",
  "Performance Lock":
    "Useful for a game you specifically want max clocks for (a "
    + "competitive shooter) while leaving your global default off to save "
    + "power the rest of the time , or the reverse, for a game where you'd "
    + "rather stay quiet and cool.",
  "Compositor Suspend":
    "Some games already run borderless-fullscreen in a way that suspends "
    + "compositing on their own , forcing it ON here is mostly useful for "
    + "windowed or oddly-behaved titles.",
  "VK Pipeline Cache":
    "Turning this OFF for one game is a troubleshooting step if you "
    + "suspect a corrupted cache is causing crashes on launch specifically "
    + "in that title , deleting the cache file achieves the same thing "
    + "more permanently.",
  "GameMode":
    "Feature Overlap note: GameMode's CPU scheduling and GreenBoost's own "
    + "Performance Lock both touch CPU behavior , using both together is "
    + "fine, they don't conflict, but most of GameMode's benefit is "
    + "already covered by Performance Lock above.",
  "MangoHUD":
    "This is the per-game override for launching specifically through "
    + "`mangohud %command%` , the global \"Performance overlay\" toggle in "
    + "Global Settings does the same thing for every game at once, so you "
    + "usually only need one or the other, not both.",
  "Gamescope args":
    "Gamescope is a separate nested-compositor wrapper (resolution/refresh "
    + "override, frame limiting, HDR passthrough) , most players never "
    + "need this; it's for forcing a specific resolution or refresh rate "
    + "a game itself won't let you set.",
};

// Short, honest, one-line "why you'd want this on" phrases , row()'s 4th
// arg. Only present for settings with a genuine, provable benefit; a
// missing entry here is deliberate, not an oversight (see B3 in
// enhance_gaming.md , don't invent a benefit for neutral/situational
// settings). Reused verbatim from gbFeatures.ts' GB_AUTOMATIC `tagline`
// where the row IS that exact feature, so the two panels never describe
// the same thing in two different ways.
export const GS_BENEFIT: Record<string, string> = {
  "Keep Steam out of the way":
    "No Steam window on top of your game",
  "Upstream Proton":
    "Works with distro Proton builds, not just Valve's",
  "Background shader compiling":
    "Removes the freeze when something new appears on screen.",
  "Remember compiled shaders":
    "The SECOND time you play, the game loads faster than the first.",
  "Performance lock (CPU + GPU)":
    "One toggle: your whole system stops holding back for battery life.",
  "Pause desktop effects while playing":
    "Your desktop's own animations stop competing with the game for GPU time.",
  "NVIDIA Reflex (lower input lag)":
    "Lower input lag, even in games that never added Reflex support themselves.",
  "NIS sharpening , actually apply it":
    "A universal sharpen/upscale layer that works even when a game has no upscaler of its own.",
  "Always use newest DLSS files":
    "Every version you've ever fetched stays available , pick any of them, per game.",
};

// ── Which Global Settings rows are GreenBoost's own ──────────────────────
//
// Keys are row labels (same keying as GS_INFO / GS_BENEFIT above); the value
// is the All Games section it lives in, shown on the badge tooltip.
// row() reads this map itself to decide whether to draw the GreenBoost chip,
// so adding a row here is the only step needed to badge it.
//
// Presence in this map IS the claim "this is GreenBoost-exclusive", and the
// test for it is mechanical, not editorial: the setting either produces a
// GREENBOOST_* env var (see as_env_dict() in gb_gaming/global_settings.py ,
// only our own Vulkan/GL layer and Proton wrapper read those, so on a
// machine without GreenBoost the value goes nowhere), or it has no env var
// at all because the app performs the action itself.
//
// Deliberately absent, and they must stay absent , these are passthroughs
// that a user could set by hand and that work without GreenBoost installed:
//   DLSS Model Version           -> DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE
//   HDR (High Dynamic Range)     -> ENABLE_HDR_WSI
//   Wayland                      -> PROTON_ENABLE_WAYLAND
//   DLSS indicator overlay       -> PROTON_DLSS_INDICATOR
//   Always use newest DLSS files -> PROTON_DLSS_UPGRADE
//   FPS cap                      -> DXVK_FRAME_RATE
//   DirectX 12 feature flags     -> VKD3D_CONFIG
// Detected GPU / Session are read-only and carry no claim either way.
export const GS_ADDED_BY_GB: Record<string, string> = {
  // DRIVER SETTINGS , app-implemented, no env var
  "Close to system tray": "Display & session",
  "Stop the game when you quit": "Display & session",
  "Cinema mode on launch": "Display & session",

  // GREENBOOST RUNTIME , VULKAN LAYER
  "Remember compiled shaders": "Performance & stutter",
  "Give the game GPU priority": "Performance & stutter",
  "Protect game memory under pressure": "Gaming alongside local AI",
  "NIS sharpening , ready to use": "Image quality & upscaling",
  "NIS sharpening , actually apply it": "Image quality & upscaling",

  // GREENBOOST RUNTIME , OPENGL LAYER
  "Enable OpenGL support": "Memory & VRAM overflow",
  "Overflow threshold (MB)": "Memory & VRAM overflow",

  // GREENBOOST RUNTIME , PROTON + SYSTEM
  "Background shader compiling": "Performance & stutter",
  "Performance lock (CPU + GPU)": "Performance & stutter",
  "Pause desktop effects while playing": "Performance & stutter",
  "Pre-warm overflow memory": "Memory & VRAM overflow",
  "Remove memory-locking limit": "Memory & VRAM overflow",
  "Performance overlay (GPU + FPS)": "Overlays & visibility",
  "Show NVIDIA feature status overlay": "Overlays & visibility",

  // FRAME PACING + LATENCY
  "NVIDIA Reflex (lower input lag)": "Latency & frame pacing",

  // NIS , NVIDIA IMAGE SCALING
  "Sharpness": "Image quality & upscaling",
  "Upscale ratio": "Image quality & upscaling",

  // ALWAYS ON , NOTHING TO SWITCH. These are GB_AUTOMATIC titles from
  // gbFeatures.ts rather than literal row() call sites, so a grep for
  // row("<label>" won't find them , they're rendered from that array.
  "GPU memory overflow to system RAM (T1/T2/T3 tiering)": "Always on , nothing to switch",
  "Multi-version DLSS/Streamline library cache": "Always on , nothing to switch",
  "\"Upgraded from shipped\" tracking on every DLSS file": "Always on , nothing to switch",
  "Driver update status: checked vs. update available": "Always on , nothing to switch",
  "DirectStorage awareness": "Always on , nothing to switch",
  "Gaming outranks local AI while a game is running": "Always on , nothing to switch",

  // ADVANCED
  "Verbose Vulkan logging": "Advanced & diagnostics",
  "VRAM headroom before overflow (MB)": "Memory & VRAM overflow",
  "Minimum reserved disk space (MB)": "Memory & VRAM overflow",
  "Keep session logs for (days)": "Advanced & diagnostics",
  "Shader compile threads": "Performance & stutter",
  "Shader cache size limit (GB)": "Performance & stutter",
  "Pin a specific shader-compiler version": "Advanced & diagnostics",
};
