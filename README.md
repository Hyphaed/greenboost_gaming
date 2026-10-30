# GreenBoost Gaming Suite

**Status: Beta** , it works on my machine, and that's about as far as the evidence goes.
**Author:** Ferran Duarri · **License:** GPL v2 (see `LICENSE`)

---

## What this actually is

A Linux app for NVIDIA gamers: per-game settings, DLSS version management,
fan curves, power and clock control, multi-monitor setup, and experimental
DLSS 5 Neural Rendering injection, all in one window , the things NVIDIA's
own app has never shipped for Linux.

It's a side project of [GreenBoost](https://gitlab.com/IsolatedOctopi/greenboost),
a separate GPU-memory-pooling tool built for AI workloads. Built and tested on two machines 
(laptop/destktop RTX 5070, GNOME/Wayland) , other setups are untested, and [Contributing](#contributing) explains how to help change that.

---

**Disclaimer:** GreenBoost & Greenboost Gaming Suite are independent open-source projects and are
not affiliated with, endorsed by, or sponsored by NVIDIA Corporation.
NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

---

## Screenshots

**Status** , what the machine actually is and whether every piece of the stack
came up: driver version, kernel module load state, Vulkan layer, CPU governor,
session type. Live GPU underneath, recent game sessions below that:

![Status view showing hardware summary, GreenBoost module state and live GPU readings](docs/screenshots/status.png)

**Games → All Games** , the honest list: every setting marked `GreenBoost` is
something Linux + NVIDIA doesn't give you on its own (no NVIDIA App / GeForce
Experience equivalent exists on Linux for most of these). Each row's (i) says
what it actually does, why the gap exists on Linux specifically, and a real
command to verify it yourself , no invented numbers. Search by symptom too
("stutter", "VRAM", "overlay"). All features are explained further on [docs/FEATURES.md](docs/FEATURES.md):

![All Games tab showing settings grouped by goal, each tagged with a GreenBoost badge and a plain-language benefit line](docs/screenshots/games-all-settings.png)

**Smart Defaults** , reads your actual CPU/GPU topology and says what it changed
and why, rather than applying a silent preset. Here it detected Blackwell and a
24P/32L-core CPU, and shows the reasoning it used:

![Smart Defaults dialog listing the single setting changed and the topology reasoning behind it](docs/screenshots/games-smart-defaults.png)

**Games → This Game** , Steam library scan, per-title config, and a DLL version
picker for every DLSS/Streamline library the game ships. Each row shows what the
game shipped with, what's installed now, and what's cached to switch to:

![Per-game DLSS library management showing shipped, installed and cached versions per DLL](docs/screenshots/games-dlss.png)

**Displays** , multi-monitor arrangement, resolution, refresh rate, scale, and
VRR. Works on a pure Wayland session, no XWayland required:

![Display arrangement diagram with per-output resolution, refresh rate, scale and VRR controls](docs/screenshots/displays.png)

**Profile → Overclocking** , presets, core/memory offsets and TDP limit. Auto
Tune reads your real GPU generation instead of guessing:

![Overclocking tab with Quiet/Balanced/Performance presets, clock offset sliders and TDP limit](docs/screenshots/profile-overclocking.png)

**Profile → Fan Curve** , drag the anchor points, watch the current temperature
track along the curve. The persistent daemon follows whatever you apply here, so
it keeps working after you close the window to play fullscreen:

![Fan curve editor with draggable anchor points and the current temperature tracked on the curve](docs/screenshots/profile-fan-curve.png)

**Profile → Profiles** , save a full clock/power/fan setup by name and re-activate
it later:

![Saved profiles tab with a named profile and load/activate controls](docs/screenshots/profile-profiles.png)

**Live** , real-time telemetry with no game required: GPU clock and power
sparklines, memory-tier fill, and a shared timeline of GreenBoost core
activity happening on the same GPU. Frame-time stats come from the Vulkan
layer specifically, which is off by default (see below) , those rows only
appear once you've turned it on for a game:

![Live Stats telemetry with GPU clock and power sparklines, memory tiers and a GreenBoost activity log](docs/screenshots/live-stats.png)

**About → Preferences** , every DLL in the cache with its version and which
official NVIDIA repository it came from, so the chain of custody is visible at a
glance:

![DLSS library table listing each DLL with its type, source repository and version](docs/screenshots/about-libraries.png)

---

## So what does it actually do?

**31 things Linux + NVIDIA doesn't give you today, in one window:**

- **Old DLSS files, fixed.** Games ship whatever DLSS version they launched
  with, sometimes years out of date. This pulls the current ones straight
  from NVIDIA's own repos, keeps every version it's ever fetched, and lets
  you pick per game , roll one title back in two clicks if a new release
  looks worse.
- **Fan curves that don't hunt.** Drag a curve, watch the current
  temperature track it. A 3 °C hysteresis stops it oscillating every few
  seconds.
- **Power and clocks, in one place**, instead of the terminal.
- **Monitors, natively on Wayland** , arrangement, refresh rate, scaling,
  VRR, no silent fallback to X11.
- **~299 per-game profiles already written**, so most games need no setup
  at all.
- **A live view while you play** , temperature, clocks, power, VRAM , that
  warns about a thermal or power throttle *before* you feel it in the
  framerate.
- **A status page to properly log the gaming session** it gives you info about whether everything's
  actually running, instead of leaving you to guess.

All features are listed with what they do, why the gap exists on Linux specifically,
and a way to check each one yourself: **[docs/FEATURES.md](docs/FEATURES.md)**.
Inside the app, every one carries a green `GreenBoost` badge, and **Games →
All Games → GreenBoost extras only** filters the list down to exactly them.

None of this needs the GreenBoost kernel module , it all works with
GreenBoost core doing nothing at all.

**What about "more VRAM"?** That's a separate, much earlier-stage piece: an
experimental Vulkan layer that can pool your GPU's VRAM with system RAM,
**off by default because it's alpha**. It's real and working for GreenBoost's
own AI workloads sharing the card with a game , it does not make an
arbitrary game's own graphics allocations spill into system RAM (no Linux
NVIDIA driver does that today, on its own). If you don't run AI work
alongside your games, the features above are what you're actually here
for , this part is optional and you can ignore it entirely.

**Who's it for?** Anyone on NVIDIA + Linux who wants their fan curve, DLSS
versions, per-game settings and monitors in one window instead of five
terminals , which, since NVIDIA has never shipped its own control panel for
Linux, is most of us. It's also, specifically, for people who already run
GreenBoost for AI work and want to game on the same machine without closing
everything first.

---

## What's in the box

| Component | What it does |
|---|---|
| **Vulkan implicit layer** (`greenboost_vulkan_layer.c`) | Inflates each game's reported `VkPhysicalDeviceMemoryProperties` to include T2 DDR, routes overflow allocations through DMA-BUF imports. Also does NIS sharpen/upscale (embedded SPIR-V) and NVIDIA Reflex via `VK_NV_low_latency2`. **Off by default for every game** , it's alpha-stage; turn it on per game with `GREENBOOST_VULKAN_DISABLE=0` in that game's launch options |
| **Controller panel** (Status view) | Lists every connected gamepad , driver, Bluetooth/USB, battery , and flags when Steam can't read its raw device file (a driver/Steam udev conflict, not a GreenBoost setting) instead of leaving you to guess why in-game remapping doesn't work |
| **OpenGL layer** (`greenboost_gl_layer.c`) | The same memory-tiering idea for OpenGL titles. Younger and less exercised than the Vulkan one |
| **Desktop app** | Six views: Status, Games, Displays, Profile, Live, About |
| **GreenBoost Proton** | A Proton version that shows up in Steam's compatibility list next to Proton 10.0 and Experimental, and that you pick per game the same way. It doesn't replace Proton , it sits in front of the one you already have: prepares the machine for that specific title, then hands the launch to upstream Proton (stable or Experimental, selected by a `channel` file) and gets out of the way. Before the hand-off it sets CPU affinity from your real topology, applies the per-game JSON profile's env / DXR / VKD3D settings, activates the GPU power-clock profile and matching fan curve, overlays dxvk-gplasync, stages the NIS shaders, tunes swappiness and the compositor for the session, and writes a telemetry line on exit. If any of that fails it falls back to a plain upstream Proton launch instead of taking the game down with it |
| **Fan daemon** | systemd user unit with a 3 °C hysteresis so your fans stop oscillating |
| **Session ownership** | Closing the window sends the Suite to the system tray instead of quitting, and quitting stops the game it launched , the way Steam behaves. GreenBoost Proton declares itself a subreaper so it keeps hold of the whole process tree even when a launcher forks and exits; the game is asked to exit first and only forced if it refuses, and Wine's shared processes are left alone so another prefix is unaffected. Both behaviours are settings, and if your desktop has no tray the Suite tells you and closes as before |
| **DLSS / Streamline updater** | Pulls the newest DLLs straight from NVIDIA's official GitHub repos, keeps every version it has ever fetched, and lets you pick per game |
| **~299 per-game profiles** | JSON files in `profiles/per-game/` with known-good tweaks per title |

What the views do:

- **Status** , is GreenBoost actually installed and working? Kernel module,
  shim, Vulkan loader, layer registration, with a straight ready / not-ready
  verdict instead of you guessing.
- **Games** , scans your Steam library, per-game overrides, DLSS DLL swapping,
  and a VRAM-risk badge based on what previous sessions of that game actually
  used. Non-Steam shortcuts show up too: a Battle.net or Epic launcher you
  added by hand lives in its own Proton prefix with no manifest, so it is
  read out of Steam's `shortcuts.vdf` , and for Battle.net, out of Blizzard's
  own database inside the prefix, so you get the games rather than one tile
  saying "Battle.net".
- **Displays** , monitor arrangement, resolution, refresh rate, scaling, VRR,
  night light. Works on Wayland without falling back to X11.
- **Profile** , fan curve editor, power limit, clock locks. Auto Tune reads your
  real CPU/GPU topology instead of guessing.
- **Live** , real-time GPU telemetry, including a throttle banner that names the
  driver's own reason (thermal, power limit, power brake, hardware slowdown) via
  NVML rather than guessing from a temperature reading, and fires *before* your
  framerate tanks rather than after.
- **About** , DLL provenance table, preferences, license, disclaimer.

---

## DLSS 5 (Neural Rendering) , experimental, and here's exactly what it needs

This is unofficial. NVIDIA has never shipped a public API for it, nothing
here is documented by NVIDIA, and it only runs on RTX 50-series cards. Say
that up front because the toggle is easy to find and the requirements are
easy to skip past.

Under the hood this installs **GreenBoost's own DLSS 5 runtime**
(`libraries/dlssnr/`, an NVIDIA-only fork with every AMD/Intel code path
removed , ~3 MB instead of the ~200 MB a vendor-agnostic upscaler proxy
would pull in) into the game's own executable directory as a DLL proxy, and
turns its Neural Rendering pass on directly in the runtime's own config file.
Nothing is fetched from the network at install time , the runtime is built
and shipped with the Suite, the same rule the DLSS DLL updater already
follows for redistribution: only NVIDIA's own signed DLLs ever get pulled
live, and only from NVIDIA's own repos.

What you need before the toggle in **Games → This Game → DLSS 5** does
anything:

1. **An RTX 50-series GPU.** The model does not run on anything older.
2. **`nvngx_dlssnr.dll`**, NVIDIA's own model weights. This repo cannot
   supply or redistribute it , place your own copy at
   `~/.local/share/greenboost/dlss5/nvngx_dlssnr.dll` (or `assets/dlss5/`
   in a dev checkout). The "files needed" dialog shown before you enable the
   feature tells you plainly if it's missing.
3. **A game that already has DLSS.** Neural Rendering runs as an extra pass
   on top of an already-active DLSS Super Resolution feature , it does
   nothing if DLSS itself isn't turned on in the game's own graphics menu.

With those three in place, one toggle does the rest: installs the runtime,
picks a DLL proxy name the game isn't already using (`winmm.dll` first,
falling back through `version.dll`, `dbghelp.dll`, `wininet.dll`,
`winhttp.dll`, `dxgi.dll`, `d3d12.dll`), and turns the pass on in its config.
The panel expanded past the one on/off toggle too , preset, style,
intensity, local structure/tone, skin structure, and the rest of the
runtime's real parameters are all there, alongside Frame Generation and the
Reflex-aware framerate limit, so there's no separate overlay to hunt for.
The preflight checklist in that same panel reports the real, measured state
(GPU model, driver, staged files, which proxy slot is free) every time you
open it.

**Confirmed working on 007 First Light only.** The mechanism is meant for
every game , it doesn't touch any game-specific shader code, unlike an
earlier RenoDX-based attempt that only ever worked for the one game it was
built against , but nothing beyond 007 First Light has actually been tested
yet. Whether the pass changes the picture in any given game also hasn't been
measured with a real A/B comparison. What's confirmed: the runtime installs
cleanly and the game's own native DLSS Super Resolution keeps evaluating
every frame underneath it, unaffected. See `CHANGELOG.md` for the fuller
story.

---

## How this actually plugs into Steam

This is the question I get asked first, so: there is no patching, no
injected DLL of mine, nothing replaced. Two ordinary, documented
extension points do all the work, and I want to be clear that **the
plumbing is boring on purpose , what's novel is what runs once it's
loaded, not how it gets there.**

```
you press Play in Steam
  │
  ▼
~/.local/share/Steam/compatibilitytools.d/greenboost-proton/proton
      a Steam "compatibility tool" , the same mechanism Proton-GE and
      Luxtorpeda use. You pick it per game under Properties →
      Compatibility. Deployed by greenboost_proton/install.sh
  │   detects your GPU and CPU topology, applies that game's JSON
  │   profile, sets GREENBOOST_VULKAN=1 plus ~25 other env vars
  ▼
upstream Proton (stable or Experimental) , inherits that environment
  │   GreenBoost wraps it, never replaces it: if anything above fails,
  │   the launch falls through to a plain Proton run instead of taking
  │   the game down
  ▼
the Vulkan loader reads /usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json
      an implicit layer manifest, gated on enable_environment:
      GREENBOOST_VULKAN=1 , which is exactly why the wrapper sets it.
      No env var, no layer. Nothing loads into apps you didn't launch
      through it
  │
  ▼
libVkLayer_greenboost.so is now inside the game process
      hooks vkAllocateMemory, vkQueuePresentKHR (that's where NIS
      dispatches), VK_NV_low_latency2 (Reflex), and snapshots the
      pipeline cache
  │
  ▼
ioctl() to greenboost.ko for the memory tiers , optional; without the
kernel module everything else still runs
```

Two details worth knowing because they get assumed wrong:

- **The Vulkan path is not `LD_PRELOAD`.** The Vulkan loader loads the
  layer itself, through the same mechanism MangoHud and the validation
  layers use. The OpenGL path *is* a preload, because OpenGL has no
  equivalent loader concept.
- **The wrapper wraps Proton rather than forking it.** Every Proton fork
  has to be re-forked on each Valve release. This one sets up an
  environment and then hands off, so a Proton update is just a Proton
  update.

---

## What you actually get, and where it comes from

I'd rather sort this honestly than sell it. Everything here falls into
one of three buckets, and only the third is genuinely new.

**Parity work** , Windows has had it for years, usually through NVIDIA
App / GeForce Experience, which has never shipped for Linux. Nothing
clever, it just needed doing:

| Feature | On Windows | NVIDIA ships it on Linux? | What this does |
|---|---|---|---|
| Swap a game's DLSS version | NVIDIA App | No | Fetches from NVIDIA's own GitHub repos, keeps **every** version ever fetched, per-game dropdown |
| Driver update notification | NVIDIA App | No , nothing at all | Status view checks your package manager and says which of three states you're in |
| Fan curve | NVIDIA App / vendor tools | No | Drag-a-curve editor with 3 °C hysteresis so it stops oscillating |
| Power limit + clock locks | NVIDIA App | Partially (`nvidia-smi`, CLI only) | Same window as everything else, via NVML , no X11 needed |
| Performance mode | NVIDIA App | No | CPU governor + GPU clocks + PowerMizer, one toggle, reverted on exit |
| Monitor arrangement / VRR | Windows display settings | No | Wayland-native, no silent X11 fallback |

**Automation of things Linux already had, that nobody wired up.** The
pieces are upstream and community-built; doing it per-game by hand is
what nobody was doing:

| Feature | Who built the underlying piece | What this adds |
|---|---|---|
| Background shader compiling | dxvk-gplasync (community DXVK fork) | Fetches, stages into every game's prefix, keeps updated. One toggle instead of a per-game manual install |
| Persistent pipeline cache | Vulkan's own `VK_EXT_pipeline_cache_control` | Wires it up per-AppID and re-injects on next launch |
| NIS sharpening/upscaling | NVIDIA's own NIS shaders | Dispatches them from the layer, so it works in games whose studio never integrated the SDK |
| Reflex | NVIDIA's `VK_NV_low_latency2` | Same , applied at the layer, independent of game support |
| DirectStorage | vkd3d-proton has shipped it since 2023 | Doesn't reimplement it; tells you whether it's actually engaged for your game, Proton build and disk |

**Genuinely not shipped anywhere, by anyone.** Small list, and I'd rather
it be a small honest list. All of it is experimental and tested on one
machine:

- **Per-file DLSS provenance.** The first time anything touches a DLL,
  the original is snapshotted permanently, so "what did this game
  actually ship with" stays answerable forever. Every other DLSS swapper
  I know of, NVIDIA's included, keeps one most-recent backup , which
  means one wrong swap and the original is gone.
- **Live T1/T2/T3 tier occupancy in the game overlay.** MangoHud has no
  idea GreenBoost's kernel module exists; this wires its `exec=`
  directive to the module's live counters, so you see memory-tier state
  on the same overlay as your FPS.
- **GPU memory tiering itself** , with the scope caveat above. Real for
  GreenBoost's CUDA work sharing your card, not for a game's own
  allocations.

**"Is any of this like DLSS?"** No, and I want to head that off. DLSS is
a neural model NVIDIA trains and ships; nothing here competes with it or
replaces it. What this does is *manage* DLSS , versions, provenance,
presets , and separately adds a memory-tiering idea that has nothing to
do with upscaling. Compatibility with NVIDIA's own tech is by
construction rather than by luck: the layer sits in the loader chain
Khronos documents, and the DLSS work moves NVIDIA's own signed DLLs from
NVIDIA's own repositories. Nothing is patched, reimplemented, or
reverse-engineered.

---

## Before you install: you need GreenBoost core

One of the Suite's features is a **frontend onto the GreenBoost memory pool**.
The kernel module and CUDA shim do the actual memory work , without them you
still get the fan curves, DLSS updater, per-game profiles and display settings,
but not the VRAM expansion.

---

## Install

```bash
git clone https://gitlab.com/IsolatedOctopi/greenboost_gaming_suite.git
cd greenboost_gaming_suite
sudo ./install.sh
```

What that does, in order:

1. Installs the OS packages it needs (only the missing ones).
2. Confirms GreenBoost core is present , and installs it if it isn't.
3. Builds `libVkLayer_greenboost.so` and the NIS SPIR-V shaders if they're
   stale or missing.
4. Installs the Vulkan layer + manifest to `/usr/local/lib/` and
   `/usr/share/vulkan/implicit_layer.d/`.
5. Builds and installs the GUI.
6. Drops the `greenboost-gaming` launcher on PATH and writes a `.desktop`
   entry, so it shows up in the GNOME app grid / KDE / XFCE menus.
7. Runs `greenboost_proton/install.sh` as *you* (not root) to deploy the Steam
   compatibility tool into `~/.local/share/Steam/compatibilitytools.d/`.

Useful variations:

```bash
./install.sh --check                    # dry run , tells you what it would do, changes nothing
sudo ./install.sh --uninstall           # remove everything it installed
```

Then either run `greenboost-gaming` from a terminal, or hit the super key and
type "GreenBoost".

---

## Checking it's actually running, and what's known broken

The Status view answers this for you, and I'd start there. If you want to
confirm it yourself:

```bash
# The Vulkan layer is registered , this only confirms it CAN load, not that
# it's active for any given game. It defaults off (see above); the wrapper
# sets GREENBOOST_VULKAN_DISABLE=0 explicitly for a game that enables it.
ls /usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json
GREENBOOST_VULKAN=1 GREENBOOST_VULKAN_DISABLE=0 GREENBOOST_VK_DEBUG=1 vkcube &
journalctl --user --since "5 seconds ago" | grep -i greenboost

# The kernel module is loaded (needed only for the memory tiers)
lsmod | grep greenboost && ls /dev/greenboost

# What the wrapper would set for a given game, without launching it
GREENBOOST_DRY_RUN=1 STEAM_APPID=123456 \
  ~/.local/share/Steam/compatibilitytools.d/greenboost-proton/proton run /bin/true
```

**Known gaps, observed on my machine, not hypothetical.** These fail
quietly by design , the wrapper swallows them so a partial install can't
take your game down , which also means you won't notice unless you look:

- **`gaming_mode` needs you in the `greenboost` group.**
  `/sys/module/greenboost/parameters/gaming_mode` is `root:greenboost 0664`,
  and the installer adds you to that group , but group membership only takes
  effect after you log out and back in. Until it does, the wrapper can't
  raise the flag and the "gaming outranks inference" behavior silently
  no-ops. Check with `id -nG | grep greenboost`.
- **`nvidia-smi` not on PATH inside Steam's sandbox.** Steam Runtime Sniper
  (`pressure-vessel`) doesn't necessarily bind the directory `nvidia-smi`
  lives in into the game's environment. When that happens the pre-flight log
  says so and the GPU half of the performance lock is skipped for that
  session.
- **Layer installed but not found.** If `libVkLayer_greenboost.so` or its
  manifest is missing, NIS and Reflex silently stage but never dispatch.
  This is the single most common cause of "I turned it on and nothing
  happened" , check the first command above.
- **`gb_gaming` not importable from the wrapper.** Then Global Settings
  defaults don't reach the launch. Env vars the wrapper sets directly are
  unaffected, so it degrades partially rather than obviously.
- **Deployed copy drift.** Steam runs the copy in
  `compatibilitytools.d/`, not the one in this repo. `sudo ./install.sh`
  re-deploys it (step 7 above) , if a change doesn't appear at runtime,
  this is almost always why.

The wrapper logs its own pre-flight diagnostics to journald, so
`journalctl --user -n 100 | grep -i greenboost` right after a launch will
tell you which of these you hit.

## Contributing

**Contributors are more than welcome.** 

Good places to start, easiest first:

| Where | What |
|---|---|
| `profiles/per-game/` | Add a profile for a game you play. It's one JSON file. Genuinely the most useful thing you can do. |
| Issues | "It broke on my GPU" is a real contribution here. Given I test on one card, I'm flying blind on everything else. |
| `src/src/` | React/TS frontend. Plenty of UI polish left. |
| `gb_gaming/` | Python helpers , fan daemon, NVML control, DLSS updater. |
| `greenboost_vulkan_layer.c` | The deep end. Memory tiering, NIS, Reflex. |
| `greenboost_proton/proton` | The Proton wrapper , launch-time intelligence. |

See [CONTRIBUTING.md](CONTRIBUTING.md) for build instructions and the handful of
rules that actually matter.

---

## Greenboost Gaming Studio references

Logic was ported or adapted from those other projects;

- **[Valve Proton](https://github.com/ValveSoftware/Proton)** , the
  Wine + DXVK + VKD3D stack that runs Windows games on Linux. The
  `greenboost_proton/` wrapper builds on Proton's compatibilitytool
  packaging conventions.
- **[NVIDIA cuda-samples](https://github.com/NVIDIA/cuda-samples)** ,
  reference for external-memory import patterns
  (`cuImportExternalMemory`, OpaqueFd handle type) used by the Vulkan
  layer's T2 overflow path.
- **[vuda](https://github.com/jgbit/vuda)** , Vulkan-as-CUDA shim that
  showed how a Vulkan layer can present CUDA-like memory semantics to
  client applications.
- **[VulkanShaderCUDA](https://github.com/waefrebeorn/VulkanShaderCUDA)** ,
  reference for cross-API memory sharing between Vulkan and CUDA on
  the same physical device.
- **[DLSS-Updater](https://github.com/Recol/DLSS-Updater)** , DLL
  version scanning + patching pattern adapted for the DLSS panel.
- **[OptiScaler](https://github.com/optiscaler/OptiScaler)** and its
  **[Dagherbou/OptiScaler_DLSSNR](https://github.com/Dagherbou/OptiScaler_DLSSNR)**
  fork , studying how OptiScaler hooks a game's DXGI/D3D12/NGX calls is what
  made DLSS 5 (Neural Rendering) injection possible here at all. GreenBoost's
  own DLSS 5 runtime (`libraries/dlssnr/`) is a further NVIDIA-only fork of
  that DLSSNR fork, built specifically for this project , see `CHANGELOG.md`
  and `libraries/dlssnr/FORK_NOTES.md` for the full history.
- **[GreenWithEnvy](https://gitlab.com/leinardi/gwe)** (GPL v3) ,
  reference for the GPU profile editor (clocks, fan curve, power limit). 
  (actually I have permission for being the successor / official mantainer of GreenWithEnvy)
- **[NVIDIA Image Scaling](https://github.com/NVIDIAGameWorks/NVIDIAImageScaling)** ,
  the NIS shaders the layer compiles to SPIR-V and dispatches at present time.

If you maintain one of these and want the attribution adjusted, open an issue
and I'll fix it.

---

## A note on the DLSS DLLs

You won't find any `.dll` files in this repository, and that's deliberate.
`libraries/` is a **runtime cache** , empty in a fresh clone. The first time you
hit **Update DLSS**, the Suite fetches the Streamline libraries from NVIDIA's
own GitHub repositories, checksums them, and caches them under
`~/.local/share/greenboost-gaming/libraries/`. Every version it fetches is kept,
so you can pin a specific one per game and roll back if a new release regresses.

[DLSS_UPDATER.md](DLSS_UPDATER.md) explains the sources and the chain of custody.

---

## License

GPL v2, open source. Fork it, change it, ship it , just keep the credit.

```
Copyright (C) 2026 Ferran Duarri
```
