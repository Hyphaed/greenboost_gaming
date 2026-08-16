# What GreenBoost Gaming Suite adds

**31 features** that a stock Linux + NVIDIA install does not give you. For most of them there is no NVIDIA-provided equivalent on Linux at all , NVIDIA App / GeForce Experience, the closest Windows counterpart for several, has never shipped for Linux.

25 are settings you control; 6 are always-on behavior, a manual action, or automatic bookkeeping, and say so rather than pretending to have an on/off state.

Every entry below appears in the app itself, under **Games → All Games**, marked with a green `GreenBoost` badge. Click **GreenBoost extras only** to filter the list down to exactly this set, or open any row's ⓘ for the same text you're reading here.

Back to [README.md](../README.md).

## How to read an entry

- **What it does** , the mechanism, plainly.
- **Why this is a Linux/NVIDIA gap** , why you don't already have it.
- **How to see it yourself** , a real check you can run on your own machine. Where an effect can't be measured without hardware most people don't own, or won't show up at all on an idle system, the entry says so instead of quoting an invented number.

Nothing here has been benchmarked across a fleet. It is built and tested on one machine (RTX 5070, GNOME/Wayland), which is why every entry tells you how to check it rather than asking you to take a figure on faith.

---

## Contents

- [Performance & stutter](#performance-stutter) , 7
- [Image quality & upscaling](#image-quality-upscaling) , 4
- [Latency & frame pacing](#latency-frame-pacing) , 1
- [Memory & VRAM overflow](#memory-vram-overflow) , 6
- [Overlays & visibility](#overlays-visibility) , 2
- [Display & session](#display-session) , 1
- [Gaming alongside local AI](#gaming-alongside-local-ai) , 1
- [Advanced & diagnostics](#advanced-diagnostics) , 3
- [Always on , nothing to switch](#always-on-nothing-to-switch) , 6

---

## Performance & stutter

### Remember compiled shaders

**The SECOND time you play, the game loads faster than the first.**

**What it does**

Saves the driver's already-compiled shader pipelines to disk (~/.local/share/greenboost/proton-cache/vk-pipeline/<AppID>.bin) and re-injects them on your next launch, so shaders compiled last session don't have to be recompiled from scratch this session.

**Why this is a Linux/NVIDIA gap**

Vulkan's own VK_EXT_pipeline_cache_control exists, but nothing wires it up automatically per-game on Linux , this is plumbing GreenBoost built, not something the driver or Proton does by itself.

**How to see it yourself**

Time how long it takes to reach a specific in-game moment (e.g. first combat encounter) on a completely fresh install vs. your second session of the same game , the gap should shrink noticeably with the cache warm.

**Worth knowing**

The cache is per-game and grows over time , see "Shader cache size limit" below to cap how much disk space it's allowed to use. Only helps on your SECOND time playing a given scene; the very first encounter with new shaders always compiles fresh.

### Give the game GPU priority

**What it does**

Asks the driver for a high-priority Vulkan queue for the game, so its submissions are scheduled ahead of other GPU work on the same card , a compositor, a browser, or GreenBoost's own inference if you run it.

**Why this is a Linux/NVIDIA gap**

Vulkan has had queue-priority in the API for years; what's missing on Linux is anything that sets it for you. A game asks for whatever its engine asks for, and nothing sits in between to raise it. The layer is that in-between piece.

**How to see it yourself**

Most visible on a busy desktop: run something GPU-hungry in the background and compare 1% lows with this on vs off. On an otherwise idle machine there is nothing to outrank, so expect no measurable change , that's the correct result, not a broken setting.

**Worth knowing**

Mainly matters if you have other GPU-heavy apps running at the same time (a second monitor's compositor effects, a background render, local AI inference). On a machine only running the game, this has little visible effect.

### Background shader compiling

**Removes the freeze when something new appears on screen.**

**What it does**

Proton/DXVK has to compile a shader the first time your GPU sees a new material, effect, or camera angle , normally that compile blocks the render thread and you see a stutter. GreenBoost stages the dxvk-gplasync overlay into every game's Proton prefix automatically, which moves that compilation to a background thread instead, and keeps a persistent on-disk cache so a game you've played before starts warm.

**Why this is a Linux/NVIDIA gap**

This isn't something NVIDIA ships at all, on any platform , gplasync is a community DXVK fork that Linux/Proton gamers have had to install and manage by hand, per-game, for years. GreenBoost is what automates it: fetch, stage, keep updated, one toggle.

**How to see it yourself**

Play a shader-heavy game's first 2–3 minutes in a new area with the toggle off, then on. Record a MangoHud frametime graph for both runs , the spikes on first-encounter with a new shader should be visibly shorter and less frequent with it on.

**Worth knowing**

This is the single biggest fix for Proton's "brief freeze the first time something new appears" problem , leave it on unless you're specifically debugging a shader-related crash, since it changes compile timing.

### Performance lock (CPU + GPU)

**One toggle: your whole system stops holding back for battery life.**

**What it does**

For the length of a game session, forces the CPU governor to performance mode, locks GPU/memory clocks, and sets NVIDIA PowerMizer to "Prefer Max Performance" , then puts everything back to normal automatically the moment you quit.

**Why this is a Linux/NVIDIA gap**

NVIDIA App (Windows-only) has a broadly similar "Performance" mode. There is no NVIDIA-provided equivalent on Linux at all , this is GreenBoost reimplementing that capability using nvidia-smi, cpupower, and PowerMizer directly.

**How to see it yourself**

Watch `nvidia-smi --query-gpu=clocks.sm,power.draw --format=csv -l 1` in a terminal while launching a game with the toggle on vs off , clocks should jump to (near) max immediately with it on, instead of ramping up gradually under load.

**Worth knowing**

Trades power draw and fan noise for consistency , your GPU/CPU stop ramping up and down with load, which is what actually causes micro-stutter on some systems. On a laptop, expect noticeably shorter battery life while a game is running.

### Pause desktop effects while playing

**Your desktop's own animations stop competing with the game for GPU time.**

**What it does**

Suspends compositor effects (KWin/GNOME Shell animations) for the duration of the game, restoring them the instant you quit.

**Why this is a Linux/NVIDIA gap**

Desktop-environment-specific, and no upstream game launcher on Linux does this automatically today , usually a manual per-DE setting most players never discover.

**How to see it yourself**

Compare 1% low framerates (MangoHud) in a CPU-bound scene with the toggle on vs off , the effect is small but consistent on compositor-heavy desktops.

**Worth knowing**

The gain is small but real on compositor-heavy desktops (KDE Plasma with effects, GNOME with extensions) , negligible if you already run a lightweight desktop.

### Shader compile threads

**Worth knowing**

0 (auto) reads your actual CPU topology and picks a sensible number , manually overriding this is really only useful if you're running something else CPU-heavy at the same time and want to leave it more headroom.

### Shader cache size limit (GB)

**Worth knowing**

Once the cache hits this limit, GreenBoost clears out the oldest entries to make room , a smaller limit means more games will need to recompile shaders they've already compiled before.

---

## Image quality & upscaling

### NIS sharpening , ready to use

**What it does**

Injects NVIDIA Image Scaling via a Vulkan layer that works across any Vulkan or Proton-translated game, not dependent on the game itself integrating it.

**Why this is a Linux/NVIDIA gap**

NIS ships as an SDK NVIDIA expects each game studio to integrate individually; very few Linux/Proton titles do. GreenBoost applies it at the driver-adjacent layer instead, so it works regardless of whether the game developer bothered.

**How to see it yourself**

Turn both NIS toggles on with the upscale ratio below 100%, and compare a static scene's sharpness/detail before and after , take your own screenshot pair if you want a visual reference for this specific machine's actual output.

**Worth knowing**

Just prepares the shaders , costs nothing in performance until you also turn on "NIS sharpening , actually apply it" below. Safe to leave on permanently.

### NIS sharpening , actually apply it

**A universal sharpen/upscale layer that works even when a game has no upscaler of its own.**

**Worth knowing**

Adds a small per-frame GPU cost for the sharpen pass itself. Pair it with the Upscale ratio slider in the NIS section below set under 100% for an actual performance gain, not just sharper visuals.

### Sharpness

**Worth knowing**

Push this too high and you'll start seeing a visible "halo" or ringing around high-contrast edges , if that happens, back it off a bit rather than assuming NIS itself looks bad.

### Upscale ratio

**Worth knowing**

This is where the actual performance win comes from , Sharpness alone doesn't speed anything up, it just sharpens whatever resolution the game is already rendering at.

---

## Latency & frame pacing

### NVIDIA Reflex (lower input lag)

**Lower input lag, even in games that never added Reflex support themselves.**

**What it does**

Injects VK_NV_low_latency2 markers so the driver paces how far ahead your CPU gets from your GPU, shortening the delay between input and the frame that shows it.

**Why this is a Linux/NVIDIA gap**

Same integration problem as NIS , Reflex needs per-game SDK integration upstream; GreenBoost's layer applies it independent of whether the game shipped it.

**How to see it yourself**

Needs a Reflex Analyzer-capable monitor/mouse combo for a hard number, same as on Windows. Without that hardware, the honest signal is felt input responsiveness in fast-paced sections, not a number this app can show you today.

**Worth knowing**

Reflex works by holding the CPU back slightly so it doesn't get too far ahead of the GPU , the felt effect is smaller responsiveness gains, not raw FPS. Most noticeable in fast, twitchy games; not something you'll feel in a turn-based or slow-paced title.

---

## Memory & VRAM overflow

### Enable OpenGL support

**What it does**

Loads a GreenBoost OpenGL layer alongside the Vulkan one, so an OpenGL game's large textures and buffers can be routed through the same T1/T2/T3 machinery rather than being the one API that misses out. A size threshold decides what's worth routing , small, frequently-updated buffers stay put, because moving those costs more than it saves.

**Why this is a Linux/NVIDIA gap**

Every VRAM-extension effort that exists targets Vulkan or D3D12, since that's where new games are. OpenGL is where a large part of the Linux-native and older-port back catalogue actually lives, and nothing addresses it. The same scope caveat as the memory tiering entry applies here , this is GreenBoost's own allocation path, and it does not make NVIDIA's driver oversubscribe VRAM on its own.

**How to see it yourself**

Turn it off if an OpenGL game misbehaves , that's the intended escape hatch and the fastest way to attribute a problem to this layer. The Status view's T2 tile shows whether anything is actually landing in DDR while the game runs.

**Worth knowing**

Most modern Proton games use Vulkan or DirectX (translated to Vulkan via vkd3d-proton) already, so this mainly matters for older or indie titles still using OpenGL directly. No downside to leaving it on.

### Overflow threshold (MB)

**Worth knowing**

Set this lower only if a specific OpenGL game shows visible texture pop-in or stutter you suspect is overflow-related , the default is tuned for typical texture/buffer sizes and rarely needs touching.

### Pre-warm overflow memory

**What it does**

Sets up GreenBoost's system-memory overflow pool at launch instead of building it lazily the first time something doesn't fit. The work is the same either way; this just makes it happen while the game is loading rather than mid-scene.

**Why this is a Linux/NVIDIA gap**

There is no overflow pool in a stock Linux/NVIDIA setup, so there is nothing to pre-warm , the setting only exists because the mechanism it primes does. Its usefulness is bounded by the same scope note as the memory tiering entry.

**How to see it yourself**

The effect is a stutter that doesn't happen, which is hard to photograph. The honest check is a MangoHud frametime capture across the first minutes of a memory-heavy scene with it on vs off, looking for a single early spike rather than a general trend , and on a machine where nothing ever overflows, expect no difference at all.

**Worth knowing**

Avoids a one-time stutter the first moment a game needs more than your GPU's real VRAM , the trade-off is a few seconds of extra memory setup work at launch, before you're even in a menu.

### Remove memory-locking limit

**What it does**

Raises RLIMIT_MEMLOCK for the game process. Pinned host memory is what lets the GPU read a buffer that lives in system RAM without a copy, and Linux caps how much a process may pin , commonly a few megabytes, far below what this is for.

**Why this is a Linux/NVIDIA gap**

The usual answer is editing /etc/security/limits.conf as root and logging out, which is both a system-wide change and a thing nobody wants to do to play a game. Doing it per-process at launch keeps the change scoped to the game. If your system refuses the raise, it's skipped silently and everything else still works.

**How to see it yourself**

`ulimit -l` in a terminal shows your shell's current cap for comparison. Whether the raise was granted for a given launch shows up in that session's GreenBoost log.

**Worth knowing**

Needed for some of GreenBoost's memory tricks to actually take effect; on systems where the limit can't be lifted (some hardened kernels), this silently does nothing rather than failing loudly , check journalctl if you suspect it isn't applying.

### VRAM headroom before overflow (MB)

**What it does**

Every threshold the layer and wrapper make decisions against is a value you can change rather than a constant compiled into a binary: how much VRAM to keep free before spilling to system RAM, how much NVMe to reserve as the last tier, how large an OpenGL buffer must be to be worth routing, how many CPU threads compile shaders, how large the shader cache may grow, and which dxvk-gplasync release to pin.

**Why this is a Linux/NVIDIA gap**

Not a Linux gap so much as a deliberate choice about who this is for. The defaults are what this machine settled on, derived from CPU topology and GPU class rather than picked. Since the whole thing is tested on exactly one machine, leaving the constants reachable is the honest option , someone on different hardware will need to move them, and shouldn't have to rebuild to do it.

**How to see it yourself**

Verbose Vulkan logging (in Advanced) is the one to reach for when you want to see the tiering decisions rather than infer them , it produces a lot of output fast, so turn it back off afterwards.

**Worth knowing**

Set this higher if you're seeing GPU-memory-related crashes even with overflow enabled , it means GreenBoost is cutting things too close to your card's real limit for this specific game.

### Minimum reserved disk space (MB)

**Worth knowing**

This is the last resort tier, after both your graphics card and system RAM are full , reserving space here only matters on systems that are genuinely pushing memory limits, which is uncommon.

---

## Overlays & visibility

### Performance overlay (GPU + FPS)

**What it does**

Turns on MangoHud's overlay (FPS, frametime graph, GPU temp/power/clocks, CPU load, VRAM/RAM usage) and adds one GreenBoost-specific line on top of it: live T1/T2/T3 memory-tier occupancy , how much is in real GPU VRAM (T1), how much has spilled into system DDR (T2), and how much is on NVMe (T3) , read directly from the kernel module's own live counters.

**Why this is a Linux/NVIDIA gap**

MangoHud on its own has no awareness of GreenBoost's kernel module at all; there is no first-party way to see tier occupancy without a terminal open on the side. GreenBoost wires MangoHud's own `exec=` directive to the kernel module's live status file, so the same overlay you already use for FPS shows this too , no separate window, no MangoHud fork.

**How to see it yourself**

Turn this on, launch a game, and look for the extra GreenBoost line in the overlay. You can see the exact same numbers it's reading from any time via `cat /sys/class/greenboost/greenboost/pool_brief` in a terminal.

**Worth knowing**

Works identically across DirectX 9-12 and native Vulkan, unlike the NVIDIA-specific overlay below. Requires the separate "mangohud" package to already be installed on your system , GreenBoost doesn't bundle it.

### Show NVIDIA feature status overlay

**What it does**

Turns on DXVK-NVAPI's status text, which prints the live DLSS mode, Frame Generation state, and Reflex state into the game's own built-in overlay , so "is DLSS actually running, and in which mode?" is a thing you read off the screen instead of infer from how the image looks.

**Why this is a Linux/NVIDIA gap**

On Windows the NVIDIA overlay answers this. That overlay has never shipped for Linux, so the honest answer on Linux has been to trust the in-game menu and hope the DLL swap took. Worth knowing the limit before you rely on it: this only reaches games running through DXVK (DirectX 9/10/11) or native Vulkan. DirectX 12 games go through vkd3d-proton, which has no equivalent, so it will show nothing there , use the performance overlay for a HUD that works on every game.

**How to see it yourself**

Turn it on, launch a DirectX 11 title with DLSS enabled, and look for the status text in the game's overlay. If a DirectX 12 game shows nothing, that's the vkd3d-proton limitation above, not a failure.

**Worth knowing**

Only shows real data for DXVK (DirectX 9/10/11) and native Vulkan games , vkd3d-proton (DirectX 12) has no equivalent hook, so this silently shows nothing for DX12 titles. The Performance overlay above works on every game instead.

---

## Display & session

### Cinema mode on launch

**What it does**

Turns off every display except your primary one the moment you press Launch, so a game that mishandles multiple outputs , wrong monitor, wrong resolution, cursor escaping to the second screen , only ever sees one. Your other monitors come back from the Displays view when you're done; it is deliberately not automatic, because restoring outputs under a crashed game is how you end up with a black desktop.

**Why this is a Linux/NVIDIA gap**

Windows games have had exclusive fullscreen handling this for years. Under Wayland the compositor owns display state and exclusive fullscreen isn't the same lever, so the multi-monitor failure modes are back, and the workaround has been to unplug a cable or hand-edit your display config before playing.

**How to see it yourself**

Turn it on with a second monitor connected and press Launch , the secondary output should go dark before the game appears. Restore from the Displays view afterwards.

**Worth knowing**

Useful if a game's fullscreen detection gets confused by a second monitor and launches windowed or at the wrong resolution. GreenBoost does not turn your other monitors back on for you afterward , that's a manual step on the Displays page.

---

## Gaming alongside local AI

### Protect game memory under pressure

**What it does**

Tags GreenBoost's own allocations as lower-priority than the game's via Vulkan memory priority, so when VRAM gets tight the driver reclaims GreenBoost's pages first and leaves the game's working set where it is.

**Why this is a Linux/NVIDIA gap**

This one is specifically about GreenBoost not harming the thing it's meant to help. It matters when the same card is running local AI inference alongside a game , without it, the loser under pressure could just as easily be the game's textures. Nothing upstream arbitrates between two unrelated GPU consumers like this because nothing upstream expects them to share a card.

**How to see it yourself**

Meaningful only when something else of GreenBoost's is actually resident. Start a local inference workload, then a memory-heavy game, and watch the Status view's tier tiles for which side gives ground. On a gaming-only machine this setting has nothing to act on.

**Worth knowing**

Only relevant if you also run GreenBoost AI inference workloads on this machine while gaming , without that, there's nothing competing for VRAM in the first place, so this setting has nothing to do.

---

## Advanced & diagnostics

### Verbose Vulkan logging

**Worth knowing**

Generates a large volume of log data very quickly , fine for a short troubleshooting session, but leaving it on long-term will fill your disk with logs you'll never read.

### Keep session logs for (days)

**Worth knowing**

Purely a disk-hygiene setting , has no effect on gameplay or performance either way.

### Pin a specific shader-compiler version

**Worth knowing**

Only useful if a specific dxvk-gplasync release introduced a regression for you , pinning lets you roll back without losing the background-compile feature entirely.

---

## Always on , nothing to switch

### GPU memory overflow to system RAM (T1/T2/T3 tiering)

**Extends what your graphics card's memory can hold.**

*No switch , Always on whenever the kernel module is loaded.*

**What it does**

GreenBoost's kernel module can spill GPU allocations that don't fit in real VRAM (T1) into system RAM (T2) and, beyond that, NVMe storage (T3) , useful for GreenBoost's own AI-inference workloads sharing this machine with games.

### Multi-version DLSS/Streamline library cache

**Every version you've ever fetched stays available , pick any of them, per game.**

*No switch , A manual per-DLL action, not a background behavior.*

**What it does**

Fetches DLSS Super Resolution, Frame Generation, Ray Reconstruction, and the Streamline plumbing DLLs from NVIDIA's own official GitHub repos, keeps every distinct version ever downloaded (not just the latest), and lets you pick exactly which one to install into any given game from a per-game dropdown , including reverting to the exact version the game originally shipped with.

### "Upgraded from shipped" tracking on every DLSS file

**Always know whether a file is still what the game shipped, or something you changed.**

*No switch , Automatic bookkeeping , there's no meaningful "off" state.*

**What it does**

The first time GreenBoost ever touches a DLL, it snapshots the original permanently. Every game and settings panel shows a clear "⬆ upgraded from vX" badge whenever the installed file differs from what shipped.

### Driver update status: checked vs. update available

**Know your driver situation without running apt commands yourself.**

*No switch , An on-demand check, not a background behavior.*

**What it does**

Checks the installed NVIDIA driver against your system package manager and clearly distinguishes three states: not yet checked, checked and up to date, or a specific newer version available.

### DirectStorage awareness

**Know whether a game's fast-NVMe-loading tech is actually engaged, not just present.**

*No switch , A status display, not a setting.*

**What it does**

DirectStorage itself is already implemented in Proton , vkd3d-proton has shipped real DirectStorage support, including GPU-accelerated GDeflate decompression, since 2023. GreenBoost doesn't reimplement that; it detects whether a selected game actually ships DirectStorage (dstorage.dll/dstoragecore.dll present), whether the Proton build Steam will launch it with is new enough to have that support, and whether the game's install actually sits on NVMe storage , the one condition DirectStorage needs to deliver any real benefit at all.

### Gaming outranks local AI while a game is running

**Automatic. Your game wins for as long as it's running, then priority goes back to inference.**

*No switch , Automatic , follows whether a game is running.*

**What it does**

The Proton wrapper raises greenboost.ko's `gaming_mode` flag when it launches a game and clears it when the game exits. The CUDA shim reads that flag and doubles the VRAM headroom it reserves for the game, so inference is what gives ground under pressure rather than the game's textures. With no game running the flag is 0, and inference gets the high-priority CUDA streams instead.

---

<sub>Generated from `src/src/gsHelp.ts` and `src/src/gbFeatures.ts` by `scripts/gen-features-doc.py` , edit those, not this file, then re-run the script.</sub>
