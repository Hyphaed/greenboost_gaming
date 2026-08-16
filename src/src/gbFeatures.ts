// The "what GreenBoost actually adds" prose, keyed by Global Settings row
// label. Formerly a separate "Added by GreenBoost" tab, which duplicated a
// live toggle for every setting it described , two controls writing one
// value, and three different names for the same switch across three tabs.
// The explanation now travels with the control it explains: row() folds
// these three fields into that row's (i) popup, and GS_ADDED_BY_GB in
// gsHelp.ts drives the badge + the "GreenBoost extras only" filter that
// reproduces what browsing that tab was actually for.
//
// Honesty discipline, inherited from the tab and worth keeping: `verify`
// must describe something the reader can actually do on their own machine.
// Where an effect can't be measured without hardware we don't have (Reflex
// without a latency analyzer) or won't show up at all on a quiet machine,
// say that plainly instead of inventing a number.

export interface GbDetail {
  /** Mechanically, what happens. */
  what: string;
  /** Why this is missing from a stock Linux/NVIDIA install specifically. */
  why: string;
  /** How the reader can confirm the effect themselves. */
  verify: string;
}

export const GB_DETAIL: Record<string, GbDetail> = {
  // ── Overlays & visibility ────────────────────────────────────────────
  "Performance overlay (GPU + FPS)": {
    what:
      "Turns on MangoHud's overlay (FPS, frametime graph, GPU temp/power/clocks, "
      + "CPU load, VRAM/RAM usage) and adds one GreenBoost-specific line on top of "
      + "it: live T1/T2/T3 memory-tier occupancy , how much is in real GPU VRAM "
      + "(T1), how much has spilled into system DDR (T2), and how much is on NVMe "
      + "(T3) , read directly from the kernel module's own live counters.",
    why:
      "MangoHud on its own has no awareness of GreenBoost's kernel module at all; "
      + "there is no first-party way to see tier occupancy without a terminal open "
      + "on the side. GreenBoost wires MangoHud's own `exec=` directive to the "
      + "kernel module's live status file, so the same overlay you already use for "
      + "FPS shows this too , no separate window, no MangoHud fork.",
    verify:
      "Turn this on, launch a game, and look for the extra GreenBoost line in the "
      + "overlay. You can see the exact same numbers it's reading from any time via "
      + "`cat /sys/class/greenboost/greenboost/pool_brief` in a terminal.",
  },
  "Show NVIDIA feature status overlay": {
    what:
      "Turns on DXVK-NVAPI's status text, which prints the live DLSS mode, Frame "
      + "Generation state, and Reflex state into the game's own built-in overlay , "
      + "so \"is DLSS actually running, and in which mode?\" is a thing you read off "
      + "the screen instead of infer from how the image looks.",
    why:
      "On Windows the NVIDIA overlay answers this. That overlay has never shipped "
      + "for Linux, so the honest answer on Linux has been to trust the in-game menu "
      + "and hope the DLL swap took. Worth knowing the limit before you rely on it: "
      + "this only reaches games running through DXVK (DirectX 9/10/11) or native "
      + "Vulkan. DirectX 12 games go through vkd3d-proton, which has no equivalent, "
      + "so it will show nothing there , use the performance overlay for a HUD that "
      + "works on every game.",
    verify:
      "Turn it on, launch a DirectX 11 title with DLSS enabled, and look for the "
      + "status text in the game's overlay. If a DirectX 12 game shows nothing, "
      + "that's the vkd3d-proton limitation above, not a failure.",
  },

  // ── Performance & stutter ────────────────────────────────────────────
  "Background shader compiling": {
    what:
      "Proton/DXVK has to compile a shader the first time your GPU sees a new "
      + "material, effect, or camera angle , normally that compile blocks the render "
      + "thread and you see a stutter. GreenBoost stages the dxvk-gplasync overlay "
      + "into every game's Proton prefix automatically, which moves that compilation "
      + "to a background thread instead, and keeps a persistent on-disk cache so a "
      + "game you've played before starts warm.",
    why:
      "This isn't something NVIDIA ships at all, on any platform , gplasync is a "
      + "community DXVK fork that Linux/Proton gamers have had to install and manage "
      + "by hand, per-game, for years. GreenBoost is what automates it: fetch, stage, "
      + "keep updated, one toggle.",
    verify:
      "Play a shader-heavy game's first 2–3 minutes in a new area with the toggle "
      + "off, then on. Record a MangoHud frametime graph for both runs , the spikes "
      + "on first-encounter with a new shader should be visibly shorter and less "
      + "frequent with it on.",
  },
  "Remember compiled shaders": {
    what:
      "Saves the driver's already-compiled shader pipelines to disk "
      + "(~/.local/share/greenboost/proton-cache/vk-pipeline/<AppID>.bin) and "
      + "re-injects them on your next launch, so shaders compiled last session don't "
      + "have to be recompiled from scratch this session.",
    why:
      "Vulkan's own VK_EXT_pipeline_cache_control exists, but nothing wires it up "
      + "automatically per-game on Linux , this is plumbing GreenBoost built, not "
      + "something the driver or Proton does by itself.",
    verify:
      "Time how long it takes to reach a specific in-game moment (e.g. first combat "
      + "encounter) on a completely fresh install vs. your second session of the same "
      + "game , the gap should shrink noticeably with the cache warm.",
  },
  "Performance lock (CPU + GPU)": {
    what:
      "For the length of a game session, forces the CPU governor to performance "
      + "mode, locks GPU/memory clocks, and sets NVIDIA PowerMizer to \"Prefer Max "
      + "Performance\" , then puts everything back to normal automatically the moment "
      + "you quit.",
    why:
      "NVIDIA App (Windows-only) has a broadly similar \"Performance\" mode. There is "
      + "no NVIDIA-provided equivalent on Linux at all , this is GreenBoost "
      + "reimplementing that capability using nvidia-smi, cpupower, and PowerMizer "
      + "directly.",
    verify:
      "Watch `nvidia-smi --query-gpu=clocks.sm,power.draw --format=csv -l 1` in a "
      + "terminal while launching a game with the toggle on vs off , clocks should "
      + "jump to (near) max immediately with it on, instead of ramping up gradually "
      + "under load.",
  },
  "Pause desktop effects while playing": {
    what:
      "Suspends compositor effects (KWin/GNOME Shell animations) for the duration of "
      + "the game, restoring them the instant you quit.",
    why:
      "Desktop-environment-specific, and no upstream game launcher on Linux does this "
      + "automatically today , usually a manual per-DE setting most players never "
      + "discover.",
    verify:
      "Compare 1% low framerates (MangoHud) in a CPU-bound scene with the toggle on "
      + "vs off , the effect is small but consistent on compositor-heavy desktops.",
  },
  "Give the game GPU priority": {
    what:
      "Asks the driver for a high-priority Vulkan queue for the game, so its "
      + "submissions are scheduled ahead of other GPU work on the same card , a "
      + "compositor, a browser, or GreenBoost's own inference if you run it.",
    why:
      "Vulkan has had queue-priority in the API for years; what's missing on Linux is "
      + "anything that sets it for you. A game asks for whatever its engine asks for, "
      + "and nothing sits in between to raise it. The layer is that in-between piece.",
    verify:
      "Most visible on a busy desktop: run something GPU-hungry in the background and "
      + "compare 1% lows with this on vs off. On an otherwise idle machine there is "
      + "nothing to outrank, so expect no measurable change , that's the correct "
      + "result, not a broken setting.",
  },

  // ── Memory & VRAM overflow ───────────────────────────────────────────
  "Enable OpenGL support": {
    what:
      "Loads a GreenBoost OpenGL layer alongside the Vulkan one, so an OpenGL game's "
      + "large textures and buffers can be routed through the same T1/T2/T3 machinery "
      + "rather than being the one API that misses out. A size threshold decides "
      + "what's worth routing , small, frequently-updated buffers stay put, because "
      + "moving those costs more than it saves.",
    why:
      "Every VRAM-extension effort that exists targets Vulkan or D3D12, since that's "
      + "where new games are. OpenGL is where a large part of the Linux-native and "
      + "older-port back catalogue actually lives, and nothing addresses it. The same "
      + "scope caveat as the memory tiering entry applies here , this is GreenBoost's "
      + "own allocation path, and it does not make NVIDIA's driver oversubscribe VRAM "
      + "on its own.",
    verify:
      "Turn it off if an OpenGL game misbehaves , that's the intended escape hatch and "
      + "the fastest way to attribute a problem to this layer. The Status view's T2 "
      + "tile shows whether anything is actually landing in DDR while the game runs.",
  },
  "Pre-warm overflow memory": {
    what:
      "Sets up GreenBoost's system-memory overflow pool at launch instead of building "
      + "it lazily the first time something doesn't fit. The work is the same either "
      + "way; this just makes it happen while the game is loading rather than "
      + "mid-scene.",
    why:
      "There is no overflow pool in a stock Linux/NVIDIA setup, so there is nothing to "
      + "pre-warm , the setting only exists because the mechanism it primes does. Its "
      + "usefulness is bounded by the same scope note as the memory tiering entry.",
    verify:
      "The effect is a stutter that doesn't happen, which is hard to photograph. The "
      + "honest check is a MangoHud frametime capture across the first minutes of a "
      + "memory-heavy scene with it on vs off, looking for a single early spike rather "
      + "than a general trend , and on a machine where nothing ever overflows, expect "
      + "no difference at all.",
  },
  "Remove memory-locking limit": {
    what:
      "Raises RLIMIT_MEMLOCK for the game process. Pinned host memory is what lets the "
      + "GPU read a buffer that lives in system RAM without a copy, and Linux caps how "
      + "much a process may pin , commonly a few megabytes, far below what this is for.",
    why:
      "The usual answer is editing /etc/security/limits.conf as root and logging out, "
      + "which is both a system-wide change and a thing nobody wants to do to play a "
      + "game. Doing it per-process at launch keeps the change scoped to the game. If "
      + "your system refuses the raise, it's skipped silently and everything else still "
      + "works.",
    verify:
      "`ulimit -l` in a terminal shows your shell's current cap for comparison. Whether "
      + "the raise was granted for a given launch shows up in that session's GreenBoost "
      + "log.",
  },
  "VRAM headroom before overflow (MB)": {
    what:
      "Every threshold the layer and wrapper make decisions against is a value you can "
      + "change rather than a constant compiled into a binary: how much VRAM to keep "
      + "free before spilling to system RAM, how much NVMe to reserve as the last tier, "
      + "how large an OpenGL buffer must be to be worth routing, how many CPU threads "
      + "compile shaders, how large the shader cache may grow, and which dxvk-gplasync "
      + "release to pin.",
    why:
      "Not a Linux gap so much as a deliberate choice about who this is for. The "
      + "defaults are what this machine settled on, derived from CPU topology and GPU "
      + "class rather than picked. Since the whole thing is tested on exactly one "
      + "machine, leaving the constants reachable is the honest option , someone on "
      + "different hardware will need to move them, and shouldn't have to rebuild to "
      + "do it.",
    verify:
      "Verbose Vulkan logging (in Advanced) is the one to reach for when you want to "
      + "see the tiering decisions rather than infer them , it produces a lot of output "
      + "fast, so turn it back off afterwards.",
  },

  // ── Image quality & upscaling ────────────────────────────────────────
  "NIS sharpening , ready to use": {
    what:
      "Injects NVIDIA Image Scaling via a Vulkan layer that works across any Vulkan or "
      + "Proton-translated game, not dependent on the game itself integrating it.",
    why:
      "NIS ships as an SDK NVIDIA expects each game studio to integrate individually; "
      + "very few Linux/Proton titles do. GreenBoost applies it at the driver-adjacent "
      + "layer instead, so it works regardless of whether the game developer bothered.",
    verify:
      "Turn both NIS toggles on with the upscale ratio below 100%, and compare a static "
      + "scene's sharpness/detail before and after , take your own screenshot pair if "
      + "you want a visual reference for this specific machine's actual output.",
  },

  // ── Latency & frame pacing ───────────────────────────────────────────
  "NVIDIA Reflex (lower input lag)": {
    what:
      "Injects VK_NV_low_latency2 markers so the driver paces how far ahead your CPU "
      + "gets from your GPU, shortening the delay between input and the frame that "
      + "shows it.",
    why:
      "Same integration problem as NIS , Reflex needs per-game SDK integration "
      + "upstream; GreenBoost's layer applies it independent of whether the game "
      + "shipped it.",
    verify:
      "Needs a Reflex Analyzer-capable monitor/mouse combo for a hard number, same as "
      + "on Windows. Without that hardware, the honest signal is felt input "
      + "responsiveness in fast-paced sections, not a number this app can show you "
      + "today.",
  },

  // ── Gaming alongside local AI ────────────────────────────────────────
  "Protect game memory under pressure": {
    what:
      "Tags GreenBoost's own allocations as lower-priority than the game's via Vulkan "
      + "memory priority, so when VRAM gets tight the driver reclaims GreenBoost's "
      + "pages first and leaves the game's working set where it is.",
    why:
      "This one is specifically about GreenBoost not harming the thing it's meant to "
      + "help. It matters when the same card is running local AI inference alongside a "
      + "game , without it, the loser under pressure could just as easily be the game's "
      + "textures. Nothing upstream arbitrates between two unrelated GPU consumers like "
      + "this because nothing upstream expects them to share a card.",
    verify:
      "Meaningful only when something else of GreenBoost's is actually resident. Start "
      + "a local inference workload, then a memory-heavy game, and watch the Status "
      + "view's tier tiles for which side gives ground. On a gaming-only machine this "
      + "setting has nothing to act on.",
  },
  // ── Display & session ────────────────────────────────────────────────
  "Cinema mode on launch": {
    what:
      "Turns off every display except your primary one the moment you press Launch, so "
      + "a game that mishandles multiple outputs , wrong monitor, wrong resolution, "
      + "cursor escaping to the second screen , only ever sees one. Your other monitors "
      + "come back from the Displays view when you're done; it is deliberately not "
      + "automatic, because restoring outputs under a crashed game is how you end up "
      + "with a black desktop.",
    why:
      "Windows games have had exclusive fullscreen handling this for years. Under "
      + "Wayland the compositor owns display state and exclusive fullscreen isn't the "
      + "same lever, so the multi-monitor failure modes are back, and the workaround "
      + "has been to unplug a cable or hand-edit your display config before playing.",
    verify:
      "Turn it on with a second monitor connected and press Launch , the secondary "
      + "output should go dark before the game appears. Restore from the Displays view "
      + "afterwards.",
  },
};

// GreenBoost behaviors with no on/off state at all , always-on kernel
// behavior, a manual action, or automatic bookkeeping. They were entries in
// the old tab and would be dishonest as toggles, so they render as read-only
// rows carrying the same what/why/verify prose. They still count as
// "GreenBoost extras" for the filter, since that's exactly what they are.
export interface GbAutomatic extends GbDetail {
  title: string;
  tagline: string;
  /** Why there is no switch. Shown in place of a control. */
  noSwitch: string;
}

export const GB_AUTOMATIC: GbAutomatic[] = [
  {
    title: "Gaming outranks local AI while a game is running",
    tagline: "Automatic. Your game wins for as long as it's running, then priority goes back to inference.",
    what:
      "The Proton wrapper raises greenboost.ko's `gaming_mode` flag when it "
      + "launches a game and clears it when the game exits. The CUDA shim reads "
      + "that flag and doubles the VRAM headroom it reserves for the game, so "
      + "inference is what gives ground under pressure rather than the game's "
      + "textures. With no game running the flag is 0, and inference gets the "
      + "high-priority CUDA streams instead.",
    why:
      "Nothing arbitrates between a game and a local model on one GPU, because "
      + "nothing upstream expects them to share one , the usual advice is to "
      + "close one before running the other. This project exists because that "
      + "became a normal thing to want, so the arbitration had to be built. It "
      + "used to be exposed as a \"prioritise AI inference\" switch, which was "
      + "the wrong shape twice over: the choice is decided by whether a game is "
      + "running, not by a preference, and the environment variable behind it "
      + "is read by the inference process, never by the game it was being "
      + "exported to.",
    verify:
      "`cat /sys/module/greenboost/parameters/gaming_mode` , 1 while a game is "
      + "running under GreenBoost Proton, 0 otherwise. The wrapper also logs the "
      + "transition: `journalctl --user -n 50 | grep gaming_mode`. The flag is "
      + "root:greenboost 0664, so your user must be in the `greenboost` group "
      + "for the wrapper to set it , `id -nG | grep greenboost` confirms.",
    noSwitch: "Automatic , follows whether a game is running.",
  },
  {
    title: "GPU memory overflow to system RAM (T1/T2/T3 tiering)",
    tagline: "Extends what your graphics card's memory can hold.",
    what:
      "GreenBoost's kernel module can spill GPU allocations that don't fit in real "
      + "VRAM (T1) into system RAM (T2) and, beyond that, NVMe storage (T3) , useful "
      + "for GreenBoost's own AI-inference workloads sharing this machine with games.",
    why:
      "AMD's own Linux driver (RADV/amdgpu) does something like this natively for any "
      + "Vulkan game , but NVIDIA's Linux Vulkan driver has no automatic "
      + "VRAM-oversubscription at all (confirmed directly on the NVIDIA developer "
      + "forums), unlike the Windows driver. Be precise about scope here: this tiering "
      + "is currently real and active for GreenBoost's own CUDA-based AI inference, not "
      + "for an arbitrary game's own Vulkan/DirectX allocations , that gap is a "
      + "genuinely unsolved problem industry-wide on NVIDIA/Linux, not something this "
      + "app can currently claim to close for gaming specifically.",
    verify:
      "Query live tier occupancy any time via the Status view's \"T2 DDR Used\" tile or "
      + "the Live view's \"Live Pool State\" gauge , both read the kernel module's live "
      + "counters directly, MB-precision. `cat /sys/class/greenboost/greenboost/status` "
      + "shows the same numbers in a terminal if you want the raw source.",
    noSwitch: "Always on whenever the kernel module is loaded.",
  },
  {
    title: "Multi-version DLSS/Streamline library cache",
    tagline: "Every version you've ever fetched stays available , pick any of them, per game.",
    what:
      "Fetches DLSS Super Resolution, Frame Generation, Ray Reconstruction, and the "
      + "Streamline plumbing DLLs from NVIDIA's own official GitHub repos, keeps every "
      + "distinct version ever downloaded (not just the latest), and lets you pick "
      + "exactly which one to install into any given game from a per-game dropdown , "
      + "including reverting to the exact version the game originally shipped with.",
    why:
      "NVIDIA App's DLSS-swap feature is Windows-only. On Linux, updating a game's DLSS "
      + "files has meant manually downloading DLLs and copying them into a Wine prefix "
      + "by hand , no version history, no safe revert. Nothing else on Linux tracks "
      + "\"what did this game originally ship with\" at all.",
    verify:
      "This Game → any game with detected libraries → DLSS Settings. Fetch a version, "
      + "then check the dropdown shows both \"Shipped: vX\" and every \"Cached: vY\" "
      + "entry.",
    noSwitch: "A manual per-DLL action, not a background behavior.",
  },
  {
    title: "\"Upgraded from shipped\" tracking on every DLSS file",
    tagline: "Always know whether a file is still what the game shipped, or something you changed.",
    what:
      "The first time GreenBoost ever touches a DLL, it snapshots the original "
      + "permanently. Every game and settings panel shows a clear \"⬆ upgraded from "
      + "vX\" badge whenever the installed file differs from what shipped.",
    why:
      "No other tool, on either platform, tracks per-file provenance like this , most "
      + "DLSS-swapping tools (including NVIDIA's own) only ever keep a single "
      + "most-recent backup, not the true original.",
    verify:
      "Upgrade a game's DLSS files once, then check any DLL row for the purple "
      + "\"upgraded\" badge and the \"game shipped with vX\" note.",
    noSwitch: "Automatic bookkeeping , there's no meaningful \"off\" state.",
  },
  {
    title: "Driver update status: checked vs. update available",
    tagline: "Know your driver situation without running apt commands yourself.",
    what:
      "Checks the installed NVIDIA driver against your system package manager and "
      + "clearly distinguishes three states: not yet checked, checked and up to date, "
      + "or a specific newer version available.",
    why:
      "The NVIDIA Linux driver has no update-notification UI of any kind , you either "
      + "know to run `apt list --upgradable` yourself or you don't find out. Windows "
      + "users get this from NVIDIA App/GeForce Experience; Linux users get nothing "
      + "from NVIDIA at all.",
    verify: "Status view , the Driver row shows the current state live.",
    noSwitch: "An on-demand check, not a background behavior.",
  },
  {
    title: "DirectStorage awareness",
    tagline: "Know whether a game's fast-NVMe-loading tech is actually engaged, not just present.",
    what:
      "DirectStorage itself is already implemented in Proton , vkd3d-proton has shipped "
      + "real DirectStorage support, including GPU-accelerated GDeflate decompression, "
      + "since 2023. GreenBoost doesn't reimplement that; it detects whether a selected "
      + "game actually ships DirectStorage (dstorage.dll/dstoragecore.dll present), "
      + "whether the Proton build Steam will launch it with is new enough to have that "
      + "support, and whether the game's install actually sits on NVMe storage , the "
      + "one condition DirectStorage needs to deliver any real benefit at all.",
    why:
      "Nothing on Linux tells you whether DirectStorage is actually doing anything for "
      + "a specific game and Proton build combination , you either take it on faith or "
      + "dig through vkd3d-proton changelogs yourself. On Windows, DirectStorage's own "
      + "diagnostics are equally opaque to end users, so this isn't solving a "
      + "Windows-vs-Linux gap so much as a genuine visibility gap DirectStorage has "
      + "everywhere.",
    verify:
      "Select a game that ships dstorage.dll in This Game and check the DirectStorage "
      + "status line. Cross-check the storage type against `lsblk -d -o name,rota` for "
      + "the disk your Steam library actually lives on.",
    noSwitch: "A status display, not a setting.",
  },
];

// Register the always-on entries under GB_DETAIL so they get the same (i)
// popup and the same search coverage as everything else , without this,
// searching "provenance" or "oversubscription" would miss them, since those
// words only appear in `why`. `what` is left empty on purpose: it is already
// the row's own description line, and row() skips empty tip sections.
for (const f of GB_AUTOMATIC) {
  GB_DETAIL[f.title] = { what: "", why: f.why, verify: f.verify };
}
