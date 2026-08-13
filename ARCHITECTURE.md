# GreenBoost Gaming Suite , Architecture

This document covers the Gaming Suite specifically: the Vulkan implicit
layer, the Tauri/React desktop application, the Proton
wrapper, and how they interact with the upstream **GreenBoost** memory
pool.

For the memory-pool architecture itself (kernel module, three-tier
hierarchy, DMA-BUF, CUDA shim) read the parent project,
[GreenBoost core](https://gitlab.com/IsolatedOctopi/greenboost) , its
`ARCHITECTURE.md` and `DOCUMENTATION.md`.

The Suite does **not** reimplement any of that. It is a frontend that
exposes the existing pool to a second client API: Vulkan.

---

## Component map

```
┌─────────────────────────────────────────────────────────────────┐
│  Steam game (Proton / DXVK / VKD3D-Proton / native Vulkan)     │
│                │ vkAllocateMemory                               │
│                │ vkQueuePresentKHR  ◄──── NIS post-process      │
│                ▼                                                │
│  libVkLayer_greenboost.so   (GREENBOOST_VULKAN=1)               │
│   • memory tier (T1→T2→T3 overflow via DMA-BUF)                │
│   • NIS sharpen / upscale (embedded SPIR-V)                     │
│   • Reflex  (VK_NV_low_latency2)                                │
│   • pipeline-cache snapshot thread                              │
└────────────────┬────────────────────────────────────────────────┘
                 │ ioctl GB_IOCTL_*  (optional , requires kmod)
                 ▼
  greenboost.ko  (parent project , DMA-BUF T2/T3 pinned DDR)

┌─────────────────────────────────────────────────────────────────┐
│  GreenBoost Gaming Suite (Tauri/React)                          │
│                                                                 │
│  Tauri Rust backend  ←→  gb_gaming/*.py  (Python helpers)       │
│    scanner   optimizer   manager   dlss                         │
│    sources   profiles    global_settings                        │
│    nvml_reader           live_stats                             │
│                                                                 │
│  Fan daemon (systemd user unit, gb_gaming/fan_daemon.py)        │
└─────────────────────────────────────────────────────────────────┘

  GreenBoost Proton wrapper
    greenboost_proton/proton  (1 687-LOC Python)
    ├── per-game JSON profiles
    ├── pre/post hook runner
    ├── MangoHud / gamescope / gamemode argv chain
    ├── SIGUSR1 stats harvest + JSONL telemetry
    └── GREENBOOST_DRY_RUN linter
```

---

## The Vulkan implicit layer

Source: `greenboost_vulkan_layer.c` (3 504 LOC).
Manifest: `VkLayer_greenboost.json` → `/usr/share/vulkan/implicit_layer.d/`.

**Activation:** `GREENBOOST_VULKAN=1` in the game environment.
Steam launch option `GREENBOOST_VULKAN=1 %command%` is the cleanest
per-game scope. GreenBoost Proton sets this automatically.

### Hook groups

**Memory tier**

| Hook | Purpose |
|---|---|
| `vkGetPhysicalDeviceMemoryProperties[2]` | Inflate DEVICE_LOCAL heap to advertise T1+T2+T3 |
| `vkAllocateMemory` | T2 DMA-BUF import on `VK_ERROR_OUT_OF_DEVICE_MEMORY` |
| `vkFreeMemory` | Mark backing buffer COLD via `GB_IOCTL_MADVISE` |
| `vkBindImageMemory[2]` | Track image→memory bindings for burst detector |
| `vkSetDeviceMemoryPriorityEXT` | Relay priority hint to kernel eviction heuristic |

**Pipeline cache (PR-GGGG)**

| Hook | Purpose |
|---|---|
| `vkCreateGraphics/ComputePipelines` | Intercept for cache injection |
| `vkCreate/Destroy/GetData/MergePipelineCaches` | Manage per-app pipeline cache |

Cache lives at `~/.cache/greenboost/<SteamGameId>/`. A background thread
snapshots every `GREENBOOST_VK_CACHE_SNAPSHOT_SEC` seconds (default 60).
Opt-out: `GREENBOOST_VK_PIPELINE_CACHE=0`.

**NIS post-process dispatch**

| Hook | Purpose |
|---|---|
| `vkCreate/DestroySwapchainKHR` | Allocate per-swapchain `GbNisSwapState` |
| `vkGetSwapchainImagesKHR` | Register swapchain images for NIS |
| `vkCreate/DestroyShaderModule` | Intercept for internal NIS pipelines |
| `vkQueuePresentKHR` | Inject NIS compute dispatch before present |
| `vkQueueSubmit` | Re-order NIS semaphore signaling |

**Reflex (VK_NV_low_latency2)**

| Hook | Purpose |
|---|---|
| `vkCreateDevice` | Resolve `VK_NV_low_latency2` PFNs if extension present |
| `vkAcquireNextImageKHR` | `SetLatencyMarkerNV(SIMULATION_START)`, increment frame id |
| `vkQueuePresentKHR` | `SetLatencyMarkerNV(PRESENT_START/END)` |

Gate: `GREENBOOST_REFLEX=1`. `reflex_frame_id` incremented per-acquire
under `g_mutex`.

**Present-hitch telemetry**

Counters `g_gbvk_present_count / total / worst / hitches` maintained per
present call. Dumped on SIGUSR1.

### Signal handlers

| Signal | Effect |
|---|---|
| `SIGUSR1` | Dump stats to syslog within ~1 s (next present cycle) |
| `SIGUSR2` | Reload NIS sharpness + scale by remapping the HOST_COHERENT uniform buffer |

### Env-var inventory

| Variable | Default | Effect |
|---|---|---|
| `GREENBOOST_VULKAN` | `0` | Master enable for the layer |
| `GREENBOOST_VK_DEBUG` | `0` | Verbose syslog |
| `GREENBOOST_VK_OVERFLOW_MIN_MB` | `32` | Min allocation size for T2 overflow |
| `GREENBOOST_VK_T3_MIN_MB` | (unset) | Min size to route to T3 NVMe tier |
| `GREENBOOST_VIRTUAL_VRAM_MB` | (auto) | Override reported VRAM size |
| `GREENBOOST_VK_MEMORY_PRIORITY` | `1` | Enable `VK_EXT_memory_priority` hints |
| `GREENBOOST_VK_QUEUE_PRIORITY` | `1` | Request high-priority compute queue |
| `GREENBOOST_VK_PIPELINE_CACHE` | `1` | Enable pipeline cache snapshot |
| `GREENBOOST_VK_CACHE_SNAPSHOT_SEC` | `60` | Pipeline cache snapshot interval |
| `GREENBOOST_NIS` | `0` | Build NIS pipeline (prerequisite for dispatch) |
| `GREENBOOST_NIS_DISPATCH` | `0` | Activate NIS post-process on present |
| `GREENBOOST_NIS_SHARPNESS` | `0.5` | NIS sharpness (0.0–1.0) |
| `GREENBOOST_NIS_SCALE` | `1.0` | NIS render scale (0.5–1.0) |
| `GREENBOOST_NIS_SHADERS_DIR` | (embedded) | Override SPIR-V from disk instead of embedded blobs |
| `GREENBOOST_REFLEX` | `0` | Enable VK_NV_low_latency2 Reflex |

---

## NIS post-process pipeline

SPIR-V blobs are compiled from `../NVIDIAImageScaling/NIS/NIS_Main.glsl`
and embedded into `libVkLayer_greenboost.so` via `.incbin` in `nis_blobs.S`:

```
nis_blobs.S
  .incbin "build/nis_sharpen.spv"   → nis_sharpen_spv_{start,end}
  .incbin "build/nis_upscale.spv"   → nis_upscale_spv_{start,end}
```

Compile (requires `glslc` from shaderc or Vulkan SDK):

```bash
make nis-shaders   # writes build/nis_sharpen.spv + build/nis_upscale.spv
```

Shader parameters:

| Shader | `NIS_SCALER` | Block size | Thread group |
|---|---|---|---|
| `nis_sharpen.spv` | 0 | 32×32 | 256 |
| `nis_upscale.spv` | 1 | 32×24 | 256 |

**Storage-view requirement:** swapchain images must have a UNORM alias
(`B8G8R8A8_SRGB → UNORM`, `R8G8B8A8_SRGB → UNORM`). The layer injects
`VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR` +
`VkImageFormatListCreateInfo` at swapchain creation.

Dispatch path: NIS sampler descriptors + intermediate images + pre-recorded
command buffers are built per-swapchain in `GbNisSwapState`.
`vkQueuePresentKHR` is intercepted to prepend the NIS compute submission
before the present, signaling via a dedicated semaphore.

---

## GUI , Tauri

**Frontend:** `src/src/` (React + TypeScript + Vite).
Views: `Status`, `Games`, `Displays`, `Profile`, `About`, `Live`.

**Backend:** `src/src-tauri/src/` (Rust).

| Module | Role |
|---|---|
| `manager.rs` | Core commands, install flow, feeder orchestration |
| `scanner.rs` | Steam library + appmanifest parser |
| `optimizer.rs` | Per-game profile application |
| `dlss.rs` | DLSS/FSR/XeSS DLL update via `gb_gaming.dlss_updater` |
| `sources.rs` | DLSS source registry (`/etc/greenboost-gaming/sources.conf`) |
| `profiles.rs` | GPU profile read/write via `gb_gaming.gpu_profile` |
| `global_settings.rs` | Persistent settings JSON bridge to Python side |
| `nvml_reader.rs` | NVML GPU metrics |
| `live_stats.rs` | Real-time stats channel to frontend |

The Tauri backend shells out to `gb_gaming/*.py` for operations that need
the Python stack (NVML fan, NVAPI DRS mapping, VRR D-Bus, DLSS patching).

---

## `gb_gaming/` Python package

Shared Python helpers used by the Tauri backend.
Installed to `$APP_LIB_DIR/gb_gaming/` by `install.sh`.

| Module | Consumer(s) | Role |
|---|---|---|
| `game_scanner.py` | `ui/main.py` | Steam libraryfolders.vdf + appmanifest parser |
| `dlss_updater.py` | Tauri `dlss.rs` + `sources.rs` | DLSS/FSR/XeSS DLL update |
| `gpu_profile.py` | Tauri `profiles.rs` | GPU clock / power / fan profile r/w |
| `global_settings.py` | Proton wrapper, Tauri `global_settings.rs` | Persistent JSON at `~/.config/greenboost-gaming/global_settings.json` |
| `fan_daemon.py` | systemd `gb-gaming-fan-daemon.service` | Long-running fan curve follower |
| `nvml_fan.py` | Tauri `manager.rs` | NVML fan control (pkexec-elevated) |
| `nvapi_linux.py` | Tauri `manager.rs` | NVAPI DRS IDs → `__GL_*` / `DXVK_*` / `VKD3D_*` env vars |
| `_vrr_gnome.py` | Tauri `manager.rs` | GNOME 47+ VRR via `org.gnome.Mutter.DisplayConfig` D-Bus |
| `__init__.py` | implicit | Package init |

---

## Display backend

Wayland-aware display handling is split across three layers:

- **`gb_gaming/_vrr_gnome.py`** , GNOME 47+ Mutter `DisplayConfig` D-Bus
  (`org.gnome.Mutter.DisplayConfig.GetCurrentState` → `ApplyMonitorsConfig`).
- **`src/src-tauri/src/manager.rs`** , coordinates `kscreen-doctor` (KDE)
  and `gnome-monitor-config` (GNOME) for resolution / refresh changes.
- **`gb_gaming/global_settings.py` + `src/src-tauri/src/global_settings.rs`** ,
  detect `XDG_SESSION_TYPE` / `WAYLAND_DISPLAY`; set `PROTON_ENABLE_WAYLAND`
  in the exported environment.

---

## GreenBoost Proton wrapper

Directory: `greenboost_proton/`.
Installed into `~/.steam/root/compatibilitytools.d/` either manually or
via the Tauri "Install Proton" command.

Core file: `greenboost_proton/proton` , 1 687-LOC Python program.

### Feature surface

| Feature | Detail |
|---|---|
| Per-game JSON profiles | `~/.config/greenboost-gaming/per-game/<AppID>.json` , keys: `env`, `nis`, `hdr`, `fps_cap`, `wrappers`, `hooks` |
| Pre/post hooks | `~/.config/greenboost-gaming/hooks/{pre,post}.d/*.sh` , 30 s timeout, `STEAM_APPID` + `GREENBOOST_GAME_NAME` in env |
| Wrapper chain | `gamemoderun` → `gamescope` → `mangohud` prepended to argv per profile |
| SIGUSR1 stats harvest | Scrapes wine64-preloader descendants + journald; dumps to stdout within ~1 s |
| Session JSONL telemetry | `~/.local/share/greenboost/sessions.jsonl` , one record per launch |
| Dry-run linter | `GREENBOOST_DRY_RUN=1` , prints resolved env without launching |
| Channel sidecar | `greenboost_proton/channel` , `stable` or `experimental`; selects upstream Proton binary |
| Log rotation | `GREENBOOST_LOG_TTL_DAYS` (default `14`) , prunes old proton-logs at startup |
| GREENBOOST_VULKAN | Always set to `1` on Proton launch |

### Proton wrapper env vars (selected)

| Variable | Default | Effect |
|---|---|---|
| `GREENBOOST_VULKAN` | `1` | Enable Vulkan layer |
| `GREENBOOST_DRY_RUN` | `0` | Print resolved env instead of launching |
| `GREENBOOST_DISABLE` | `0` | Bypass all GreenBoost shims for this launch |
| `GREENBOOST_LOG_TTL_DAYS` | `14` | Prune proton logs older than N days |
| `GREENBOOST_PERF_LOCK` | `1` | Lock GPU clocks to boost during launch |
| `GREENBOOST_MEMLOCK_UNLIMITED` | `1` | Set `RLIMIT_MEMLOCK=unlimited` |
| `GREENBOOST_COMPOSITOR_SUSPEND` | `1` | Suspend compositor during gameplay |
| `GREENBOOST_DDR_PREWARM` | `1` | Pre-fault T2 pool before game start |
| `GREENBOOST_GPLASYNC` | `1` | Enable DXVK GPLAsync pipeline compilation |
| `GREENBOOST_GPLASYNC_VERSION` | `current` | GPLAsync build to use |
| `GREENBOOST_SHADER_THREADS` | (auto) | DXVK shader compiler thread count |
| `GREENBOOST_SHADER_CACHE_GB` | `8` | DXVK shader cache size cap |
| `GREENBOOST_NO_DXR` | `0` | Disable DXR (ray tracing) for problematic games |
| `GREENBOOST_VKD3D_CONFIG` | (empty) | Extra comma-separated vkd3d-proton flags |
| `GREENBOOST_DEBUG_SHADER_COMPILE` | `0` | Verbose shader compile logging |
| `GREENBOOST_HEAP_DELAY_FREE` | `0` | Delay heap frees (debug) |

---

## Per-game profiles

`profiles/per-game/*.json` , ~300 profiles auto-generated by
`scripts/generate_profiles.py`. Installed to
`/usr/share/greenboost-gaming/profiles/per-game/`. Read by Tauri
`optimizer.rs` to apply recommended settings for each game on first launch.

---

## Fan daemon

Source: `gb_gaming/fan_daemon.py`.
Unit: `scripts/gb-gaming-fan-daemon.service` → installed as a systemd user
service.
Access control: `scripts/60-greenboost-fan.rules` (polkit) +
`scripts/60-greenboost-fan.sudoers`.

The daemon follows a user-defined fan curve and adjusts fan speeds via
`gb_gaming/nvml_fan.py` (NVML, pkexec-elevated).

---

## Signal contracts (layer + Proton wrapper)

| Signal | Target | Effect |
|---|---|---|
| `SIGUSR1` | Vulkan layer | One-line stats dump to syslog within ~1 s |
| `SIGUSR2` | Vulkan layer | Reload `GREENBOOST_NIS_SHARPNESS` + `GREENBOOST_NIS_SCALE` live |
| `SIGUSR1` | Proton `proton` | Harvest stats from wine64-preloader descendants |

---

## Build

```bash
make vulkan         # builds libVkLayer_greenboost.so (re-uses build/*.spv if fresh)
make nis-shaders    # recompiles SPIR-V from NIS_Main.glsl
sudo ./install.sh   # full system install
```

NIS shaders require `glslc` (from `shaderc` or the Vulkan SDK).
`make nis-shaders` looks for `../NVIDIAImageScaling/NIS/NIS_Main.glsl`
(NVIDIA Image Scaling SDK, cloned as a sibling directory).

---

## What this Suite explicitly does NOT do

- Does **not** intercept DirectX directly , DXVK and VKD3D-Proton translate
  DX to Vulkan first; the Suite hooks the resulting Vulkan calls.
- Does **not** replace or modify the NVIDIA driver or `nvidia.ko`.
- Does **not** ship NVIDIA proprietary code. DLSS DLLs are downloaded at
  the user's request from NVIDIA's CDN or community mirrors.
- Does **not** auto-update DLSS DLLs without consent.
- Does **not** overclock GPU firmware , the Profile panel uses documented
  `nvidia-smi` / sysfs / NVML knobs only.

---

## License

GPL v2. Copyright © 2026 Ferran Duarri.

GreenBoost is an independent open-source project and is not affiliated
with, endorsed by, or sponsored by NVIDIA Corporation. NVIDIA, CUDA,
GeForce, and RTX are trademarks of NVIDIA Corporation.
