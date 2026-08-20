# GreenBoost Gaming Suite

**Status:** Alpha / early Beta  
**Author:** Ferran Duarri  
**License:** GPL v2 (see `LICENSE`)

GreenBoost Gaming Suite is a Linux gaming utility built around [GreenBoost](https://gitlab.com/IsolatedOctopi/greenboost). It brings GPU management, per-game configuration, DLSS/Streamline library management, display controls, telemetry, and GreenBoost's memory-tiering capabilities into one application.

> **Important:** This project is early software. It has been developed and tested on one machine: an RTX 5070 running GNOME/Wayland. Other GPUs, desktops, distributions, and games may behave differently.

GreenBoost and GreenBoost Gaming Suite are independent open-source projects. They are not affiliated with, endorsed by, or sponsored by NVIDIA Corporation. NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

---

## What is GreenBoost Gaming Suite?

The Suite is the gaming side of the GreenBoost project.

GreenBoost itself provides a Linux kernel module and CUDA shim for tiered GPU memory. The memory pool can use:

- **T1:** GPU VRAM
- **T2:** system DDR RAM
- **T3:** NVMe storage
- **T4:** memory available on another machine on the local network

The Gaming Suite adds the tools needed to use the same system comfortably for gaming. It can manage GPU clocks and power, fan curves, displays, per-game settings, DLSS/Streamline libraries, Steam launches, and live telemetry.

The important distinction is that **the memory-tiering system does not automatically turn a game's own Vulkan or DirectX allocations into tiered allocations**. The tiering is currently used by GreenBoost's own CUDA workloads. Extending that behavior to allocations made directly by games through the NVIDIA driver remains an unsolved problem.

That means the memory feature is most useful when a game and another GPU workload, such as a local AI model or encoder, need to share the same GPU.

The rest of the Suite does not require the kernel module and can be used independently.

---

## What does it provide?

### GPU management

- Fan-curve editor with temperature tracking and 3 °C hysteresis
- GPU power-limit controls
- Core and memory clock offsets
- Performance presets
- Auto Tune based on the detected CPU/GPU topology
- Saved GPU profiles
- Persistent fan control through a user systemd service

### Per-game management

The Suite scans your Steam library and provides per-title configuration.

Each game can have its own:

- GreenBoost settings
- CPU affinity
- GPU power and clock profile
- Fan curve
- Environment variables
- DXR / VKD3D settings
- Shader configuration
- DLSS / Streamline library selection

The repository also contains approximately 299 per-game JSON profiles in `profiles/per-game/`.

### DLSS and Streamline library management

Games often ship with the version of DLSS or Streamline that was available when they were released. The Suite can fetch newer versions from NVIDIA's official repositories and keep multiple versions locally.

For each game you can see:

- the library shipped by the game
- the version currently installed
- versions available in the local cache

The original DLL is preserved when it is first handled, allowing the shipped version to remain identifiable.

No DLL files are committed to this repository. They are downloaded at runtime and stored in:

```text
~/.local/share/greenboost-gaming/libraries/
```

See [`DLSS_UPDATER.md`](DLSS_UPDATER.md) for details about the sources and provenance of these libraries.

### Displays

Display management is available directly from the Suite:

- monitor arrangement
- resolution
- refresh rate
- scaling
- VRR
- night light

The display controls are designed for native Wayland sessions and do not require an X11 fallback.

### Live telemetry

The Live view provides real-time information including:

- GPU clock
- GPU power
- GPU memory usage
- frame-time statistics from the Vulkan layer
- GreenBoost memory-tier occupancy
- GreenBoost activity
- GPU throttling state

When available, NVML is used to report the driver's reason for throttling, such as thermal limits, power limits, power brakes, or hardware slowdown.

### Status

The Status page gives a single view of whether the required pieces are available:

- NVIDIA driver
- GreenBoost kernel module
- CUDA shim
- Vulkan loader
- GreenBoost Vulkan layer
- CPU governor
- session type
- current GPU state

It is intended to answer a simple question: **is the installation actually working?**

---

## Screenshots

### Status

Shows the detected hardware, GreenBoost module state, Vulkan layer state, session information, and current GPU readings.

![Status view showing hardware summary, GreenBoost module state and live GPU readings](docs/screenshots/status.png)

### Games → All Games

Lists the available game settings and identifies settings provided by GreenBoost. Each setting includes an explanation of what it changes and, where applicable, how to verify the behavior yourself.

![All Games tab showing settings grouped by goal, each tagged with a GreenBoost badge and a plain-language benefit line](docs/screenshots/games-all-settings.png)

### Smart Defaults

Smart Defaults reads the detected CPU and GPU topology before deciding what to change. The UI shows both the change and the reason for it.

![Smart Defaults dialog listing the single setting changed and the topology reasoning behind it](docs/screenshots/games-smart-defaults.png)

### Games → This Game

Shows the Steam library entry, per-game configuration, and available DLSS/Streamline versions.

![Per-game DLSS library management showing shipped, installed and cached versions per DLL](docs/screenshots/games-dlss.png)

### Displays

Provides display arrangement, resolution, refresh rate, scaling, and VRR controls.

![Display arrangement diagram with per-output resolution, refresh rate, scale and VRR controls](docs/screenshots/displays.png)

### Profile → Overclocking

Provides GPU performance presets, clock offsets, and power-limit controls.

![Overclocking tab with Quiet/Balanced/Performance presets, clock offset sliders and TDP limit](docs/screenshots/profile-overclocking.png)

### Profile → Fan Curve

Create a fan curve by moving its control points. The fan daemon continues applying the curve after the application window is closed.

![Fan curve editor with draggable anchor points and the current temperature tracked on the curve](docs/screenshots/profile-fan-curve.png)

### Profile → Profiles

Save and activate complete clock, power, and fan configurations.

![Saved profiles tab with a named profile and load/activate controls](docs/screenshots/profile-profiles.png)

### Live

Monitor GPU telemetry, frame-time data, GreenBoost memory tiers, and GreenBoost activity in real time.

![Live Stats telemetry with GPU clock and power sparklines, memory tiers and a GreenBoost activity log](docs/screenshots/live-stats.png)

### About → Preferences

Shows the libraries in the local cache, their versions, and their source repositories.

![DLSS library table listing each DLL with its type, source repository and version](docs/screenshots/about-libraries.png)

---

## How it connects to Steam

GreenBoost Gaming Suite does not replace Steam, patch Proton, or require a custom game executable.

Steam launches a **compatibility tool**. GreenBoost provides one called `greenboost-proton`, which appears in Steam's compatibility-tool list. You select it for a game in:

**Steam → Properties → Compatibility**

The compatibility tool prepares the environment for that game and then hands control to an upstream Proton build.

The launch flow is:

```text
Steam
  │
  │ Play
  ▼
greenboost-proton
  │
  ├─ Detect CPU/GPU topology
  ├─ Load the game's JSON profile
  ├─ Set the required environment variables
  ├─ Apply GPU power/clock settings
  ├─ Apply the game's fan curve
  ├─ Configure CPU affinity
  ├─ Prepare shaders and other optional components
  │
  ▼
Upstream Proton
  │
  │ runs the game with the prepared environment
  ▼
Game
  │
  ▼
Vulkan loader
  │
  │ GREENBOOST_VULKAN=1
  ▼
GreenBoost Vulkan implicit layer
  │
  ├─ Vulkan memory handling
  ├─ NIS
  ├─ Reflex
  └─ telemetry / pipeline information
```

The important part is that **GreenBoost Proton is a launcher layer, not a replacement for Proton**.

It prepares the environment and then invokes the upstream Proton version selected by the configuration. If the GreenBoost-specific preparation fails, the wrapper is designed to fall back to a normal Proton launch rather than making the game fail because an optional GreenBoost feature was unavailable.

### How the Vulkan layer is loaded

The Vulkan component is an **implicit Vulkan layer**. It is registered with the Vulkan loader through:

```text
/usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json
```

The layer is gated by the `GREENBOOST_VULKAN=1` environment variable. GreenBoost Proton sets that variable for launches using the compatibility tool.

As a result, the layer is not globally injected into every Vulkan application. Applications launched without the variable do not load the GreenBoost layer.

The OpenGL component is different: OpenGL does not provide the same loader mechanism, so the OpenGL path uses `LD_PRELOAD`.

### What happens inside the game

When the Vulkan layer is active, `libVkLayer_greenboost.so` participates in the Vulkan loader chain and can handle operations including:

- `vkAllocateMemory`
- `vkQueuePresentKHR`
- `VK_NV_low_latency2`
- pipeline-cache related operations

The kernel module is optional for most of the gaming features. The Vulkan layer can operate without it, while GreenBoost's memory-tiering functionality depends on the module.

---

## Components

| Component | Purpose |
|---|---|
| **Vulkan implicit layer** (`greenboost_vulkan_layer.c`) | Vulkan integration, memory-tier handling, NIS, Reflex, and telemetry |
| **OpenGL layer** (`greenboost_gl_layer.c`) | GreenBoost integration for OpenGL applications |
| **Desktop application** | GPU, games, displays, profiles, telemetry, and configuration UI |
| **GreenBoost Proton** | Steam compatibility tool that prepares the environment and launches upstream Proton |
| **Fan daemon** | Persistent fan control with 3 °C hysteresis |
| **DLSS / Streamline updater** | Downloads and manages NVIDIA library versions |
| **Per-game profiles** | JSON configuration files for individual games |

---

## What is actually new here?

Not every feature in the Suite is a new technology. Some features bring functionality that is common on Windows to Linux; others automate existing Linux tools; a smaller group is specific to GreenBoost.

### Linux parity

| Feature | Windows equivalent | Linux situation | GreenBoost Gaming Suite |
|---|---|---|---|
| DLSS version selection | NVIDIA App | No equivalent from NVIDIA | Downloads and manages multiple versions per game |
| Driver update notification | NVIDIA App | No equivalent from NVIDIA | Reports the current package/driver state |
| Fan curve | NVIDIA App / vendor tools | Limited or CLI-based | GUI curve editor with hysteresis |
| Power limit and clock controls | NVIDIA App | Available through tools such as `nvidia-smi` | Centralized GUI |
| Performance mode | NVIDIA App | Not provided as one integrated control | Coordinates CPU/GPU performance settings |
| Display arrangement and VRR | Windows display settings | Desktop-dependent | Wayland-native display controls |

### Automation

Some functionality already exists in Linux or community projects. The Suite connects those pieces to games and profiles.

Examples include:

- background shader compilation using `dxvk-gplasync`
- persistent Vulkan pipeline-cache handling
- NVIDIA Image Scaling shaders
- NVIDIA Reflex through `VK_NV_low_latency2`
- DirectStorage status information from the Proton/VKD3D stack

The goal is not to reimplement those projects. It is to make their configuration part of the per-game workflow.

### GreenBoost-specific functionality

The more experimental parts include:

- persistent per-file DLSS provenance
- live GreenBoost T1/T2/T3 memory-tier occupancy
- integration between GreenBoost's memory-tier system and the gaming telemetry
- GreenBoost GPU memory tiering for GreenBoost's own CUDA workloads

The last point has an important limitation: **it does not currently make arbitrary game allocations spill from VRAM into system memory on NVIDIA's Linux driver.**

---

## GreenBoost core is optional for most gaming features

The Gaming Suite can be used without the GreenBoost kernel module.

Without GreenBoost core, you can still use:

- fan curves
- GPU profiles
- DLSS/Streamline library management
- per-game profiles
- display management
- Steam integration
- telemetry features that do not depend on the kernel module

The GreenBoost core is required for the memory-tiering functionality.

If your primary goal is simply to manage an NVIDIA GPU and your games on Linux, you do not need to use the memory pool.

---

## Installation

Clone the repository and run the installer:

```bash
git clone https://gitlab.com/IsolatedOctopi/greenboost_gaming_suite.git
cd greenboost_gaming_suite
sudo ./install.sh
```

The installer:

1. Installs missing system packages.
2. Checks for GreenBoost core and installs it if necessary.
3. Builds the Vulkan layer and NIS shaders when required.
4. Installs the Vulkan layer and manifest.
5. Builds and installs the desktop application.
6. Installs the `greenboost-gaming` launcher and desktop entry.
7. Deploys the GreenBoost Proton compatibility tool to your Steam compatibility-tools directory.

The Proton tool is installed as your user rather than as root.

### Check the installation without changing anything

```bash
./install.sh --check
```

### Uninstall

```bash
sudo ./install.sh --uninstall
```

After installation, start the application with:

```bash
greenboost-gaming
```

It should also appear in the desktop application menu.

---

## Verifying the installation

The Status page is the easiest way to check the installation.

For manual verification:

### Vulkan layer

```bash
ls /usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json

GREENBOOST_VULKAN=1 GREENBOOST_VK_DEBUG=1 vkcube &

journalctl --user --since "5 seconds ago" | grep -i greenboost
```

### GreenBoost kernel module

```bash
lsmod | grep greenboost
ls /dev/greenboost
```

### Check the Steam compatibility wrapper

The following runs the wrapper without launching a game:

```bash
GREENBOOST_DRY_RUN=1 STEAM_APPID=123456 \
  ~/.local/share/Steam/compatibilitytools.d/greenboost-proton/proton run /bin/true
```

---

## Known limitations

This project is still early software. The following issues have been observed on the development machine.

### `gaming_mode` requires group membership

The kernel module exposes `gaming_mode` to the `greenboost` group. The installer adds the user to that group, but the new group membership does not become active until the user logs out and back in.

Check with:

```bash
id -nG | grep greenboost
```

### `nvidia-smi` may not be visible inside Steam's environment

Steam Runtime / pressure-vessel may not expose the directory containing `nvidia-smi` to the game environment.

When that happens, the GPU part of a performance lock can be skipped for that launch.

### The Vulkan layer can be installed but not discovered

If the library or manifest is missing, features such as NIS and Reflex may be staged without being dispatched.

Check:

```bash
ls /usr/share/vulkan/implicit_layer.d/VkLayer_greenboost.json
```

### Python helpers may be unavailable to the wrapper

If `gb_gaming` cannot be imported by the Proton wrapper, some global settings may not reach the game launch. Environment variables set directly by the wrapper continue to work.

### Steam runs the deployed Proton copy

Steam uses the copy installed under:

```text
~/.local/share/Steam/compatibilitytools.d/
```

It does not run the copy directly from the Git repository.

If you modify `greenboost_proton/proton` and do not see the change when launching a game, redeploy it:

```bash
sudo ./install.sh
```

### Logs

The compatibility wrapper writes diagnostics to the user journal:

```bash
journalctl --user -n 100 | grep -i greenboost
```

Run this after launching a game to see which optional setup steps succeeded or failed.

---

## Contributing

Contributions are welcome, especially testing on hardware other than the development machine.

Useful places to contribute:

| Area | Good contribution |
|---|---|
| `profiles/per-game/` | Add or improve a game profile |
| Issues | Report behavior on another GPU, driver, desktop, or game |
| `src/src/` | Frontend improvements and UI work |
| `gb_gaming/` | Python helpers, fan daemon, NVML controls, DLSS updater |
| `greenboost_vulkan_layer.c` | Vulkan, memory-tiering, NIS, and Reflex work |
| `greenboost_proton/proton` | Steam launch and per-game setup |

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for build instructions and project guidelines.

---

## References and attribution

GreenBoost Gaming Suite uses or builds on ideas and components from several open-source projects:

- [Valve Proton](https://github.com/ValveSoftware/Proton) — the Wine, DXVK, and VKD3D stack used to run Windows games on Linux. The GreenBoost Proton wrapper follows Proton's compatibility-tool packaging conventions.
- [NVIDIA cuda-samples](https://github.com/NVIDIA/cuda-samples) — reference material for CUDA external-memory import patterns.
- [vuda](https://github.com/jgbit/vuda) — reference for Vulkan/CUDA-style memory integration.
- [VulkanShaderCUDA](https://github.com/waefrebeorn/VulkanShaderCUDA) — reference for Vulkan/CUDA memory sharing.
- [DLSS-Updater](https://github.com/Recol/DLSS-Updater) — reference for DLSS version scanning and update workflows.
- [GreenWithEnvy](https://gitlab.com/leinardi/gwe) — reference for GPU profile, fan, clock, and power controls. The project author states that GreenBoost Gaming Suite is the successor/official continuation with permission from the relevant maintainer.
- [NVIDIA Image Scaling](https://github.com/NVIDIAGameWorks/NVIDIAImageScaling) — source of the NIS shaders used by the Vulkan layer.

If you maintain one of these projects and want an attribution changed, please open an issue.

---

## DLSS library files

The repository intentionally does not contain `.dll` files.

The `libraries/` directory is a runtime cache. When DLSS/Streamline updates are requested, the Suite downloads the required libraries from NVIDIA's official repositories, verifies them, and stores them under:

```text
~/.local/share/greenboost-gaming/libraries/
```

Previously downloaded versions are retained so that a game can be moved back to an earlier version if necessary.

See [`DLSS_UPDATER.md`](DLSS_UPDATER.md) for the details of the download sources and library handling.

---

🖥️ Scope, for context ; currently used hardware

This is being built, tested, and used primarily on two machines,
with the desktop being used by far the most:

desktop; RTX 5070 12Gb VRAM, PCIe 4.0 x16, 64GB DDR4, i9 14900KF

laptop; RTX mobile 5070 8Gb VRAM, PCIe 5.0 x16, 32GB DDR5, Ryzen AI9 365

** apart from the hardware of contributors and/or users that open issues (sometimes sharing logs)

---

## License

GreenBoost Gaming Suite is released under the **GNU General Public License v2.0**.

See [`LICENSE`](LICENSE) for the full license text.

Copyright (C) 2026 Ferran Duarri.
