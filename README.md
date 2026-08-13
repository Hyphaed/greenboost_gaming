# GreenBoost Gaming Suite

**Status: Alpha / early Beta** , it works on my machine, and that's about as
far as the evidence goes.
**Author:** Ferran Duarri · **License:** GPL v2 (see `LICENSE`)

---

## What this actually is

This is a **side project born from [GreenBoost](https://gitlab.com/IsolatedOctopi/greenboost)**.

GreenBoost is the real project. It's a Linux kernel module plus a CUDA shim
that pools your GPU's VRAM together with system DDR RAM (and NVMe, and even
memory on other machines) into one big tier'd pool, 
apart from offering turboquant, weight quantization, dataflux...

I'm not a gamer.

I don't have a backlog of AAA titles to test against. 
All of this is built and tested on exactly one machine: an RTX 5070 on GNOME/Wayland.

It works there. Beyond there, I genuinely don't know , which is where you come
in (see [Contributing](#contributing), I mean it).

---

**Disclaimer:** GreenBoost & Greenboost Gaming Suite are independent open-source projects and are
not affiliated with, endorsed by, or sponsored by NVIDIA Corporation.
NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

---

## Screenshots

**Games** , Steam library scan, per-title config, and a DLL version picker for
every DLSS/Streamline library the game ships:

![Games view with per-game DLSS library management](docs/screenshots/games-dlss.png)

**Profile → Fan Curve** , drag the anchor points, watch the current temperature
track along the curve. Live temp/power/fan up top:

![Fan curve editor](docs/screenshots/profile-fan-curve.png)

**Displays** , multi-monitor arrangement, resolution, refresh rate, scale, VRR:

![Display arrangement and settings](docs/screenshots/displays.png)

**About → Preferences** , every DLL in the cache with its version and where it
came from, so you can see the chain of custody at a glance:

![DLSS library provenance table](docs/screenshots/about-libraries.png)

---

## Who is this for?

- **Gamers** with an 8 / 12 GB NVIDIA card who keep hitting VRAM walls
  in modern AAA titles. You want the headroom of a bigger card without
  buying one.
- **Streamers and content creators** running the game + an encoder + a
  language model in the same session. VRAM gets tight fast.
- **Tinkerers** who already installed GreenBoost for AI workloads and
  want to use the same memory pool for the rest of their setup.

If your games run fine at their target quality preset and you never see
"out of video memory" warnings, you don't need this. The Suite adds no
benefit when VRAM isn't the bottleneck.

---

## What's in the box

| Component | What it does |
|---|---|
| **Vulkan implicit layer** (`greenboost_vulkan_layer.c`) | Inflates each game's reported `VkPhysicalDeviceMemoryProperties` to include T2 DDR, routes overflow allocations through DMA-BUF imports. Also does NIS sharpen/upscale (embedded SPIR-V) and NVIDIA Reflex via `VK_NV_low_latency2` |
| **OpenGL layer** (`greenboost_gl_layer.c`) | The same memory-tiering idea for OpenGL titles. Younger and less exercised than the Vulkan one |
| **Desktop app** (Tauri + React) | Six views: Status, Games, Displays, Profile, Live, About |
| **GreenBoost Proton** | A Steam compatibility tool that sets up the environment, picks CPU affinity from your actual topology, and writes session telemetry |
| **Fan daemon** | systemd user unit with a 3 °C hysteresis so your fans stop oscillating |
| **DLSS / Streamline updater** | Pulls the newest DLLs straight from NVIDIA's official GitHub repos, keeps every version it has ever fetched, and lets you pick per game |
| **~299 per-game profiles** | JSON files in `profiles/per-game/` with known-good tweaks per title |

What the views do:

- **Status** , is GreenBoost actually installed and working? Kernel module,
  shim, Vulkan loader, layer registration, with a straight ready / not-ready
  verdict instead of you guessing.
- **Games** , scans your Steam library, per-game overrides, DLSS DLL swapping,
  and a VRAM-risk badge based on what previous sessions of that game actually
  used.
- **Displays** , monitor arrangement, resolution, refresh rate, scaling, VRR,
  night light. Works on Wayland without falling back to X11.
- **Profile** , fan curve editor, power limit, clock locks. Auto Tune reads your
  real CPU/GPU topology instead of guessing.
- **Live** , real-time GPU telemetry, including a thermal-throttle banner that
  fires *before* your framerate tanks rather than after.
- **About** , DLL provenance table, preferences, license, disclaimer.

---

## Before you install: you need GreenBoost core

The Suite is a **frontend onto the GreenBoost memory pool**. The kernel module
and CUDA shim do the actual memory work , without them you still get the fan
curves, DLSS updater and display settings, but not the VRAM expansion, which is
the whole point.

---

## Install

```bash
git clone https://gitlab.com/isolatedoctopi1/greenboost_gaming_suite.git
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
