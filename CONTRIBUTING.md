# Contributing

Thanks for looking. Short version: open an issue, or send a merge request.
There's no CLA and no template to fill in.

This project is Alpha, built and tested on a single machine (RTX 5070,
GNOME/Wayland). If you're running literally anything else, you already know
something I don't.

---

## The easiest useful contribution

**Add a per-game profile.** `profiles/per-game/` holds one JSON file per title,
named after the game. Copy an existing one, adjust it for a game you actually
play, send it in. That's it , no build required, no Rust, no C.

The second easiest: **tell me it broke**. Open an issue with your GPU, distro,
desktop session (Wayland or X11), and what happened. Given how narrow my test
hardware is, bug reports from different setups are worth more here than they
would be on a mature project.

---

## Building

You need: `gcc`, `make`, `python3`, and , for the default Tauri GUI , `cargo`,
`npm`, and the WebKitGTK dev packages. `install.sh` will install the OS packages
it can.

```bash
# Everything, the way a user would install it
sudo ./install.sh

# Dry run first if you want to see what it touches
./install.sh --check

# Just the Vulkan layer (also rebuilds NIS SPIR-V if stale)
make vulkan

# Just the NIS shaders (needs glslc from shaderc / the Vulkan SDK)
make nis-shaders

# Just the Tauri frontend
cd src && npm install && npm run tauri build
```

---

## Rules that actually matter

### 1. Fix the source, never the deployed copy

When you find a bug in *running* behaviour, fix the file in this repo that
produces it , not whatever copy happens to be deployed on your machine. Never
hand-edit anything under `/usr/local/`, `~/.local/`, `~/.steam/`, or Steam's
`compatibilitytools.d/` and call it fixed. Those are installer output.

This bites hardest with the Proton wrapper. Steam does **not** run
`greenboost_proton/proton` from this repo , it runs the deployed copy in
`~/.local/share/Steam/compatibilitytools.d/greenboost-proton/`. Editing the
repo file changes nothing at runtime until the installer redeploys it. Re-run
`sudo ./install.sh` (which now invokes `greenboost_proton/install.sh` for you)
to refresh it.

If you ever catch yourself asking "why doesn't my fix show up when I launch a
game", the answer is almost always a stale deployed artifact, not a wrong fix.

### 2. Wayland is the primary target, X11 must keep working

The app has to be fully functional on a pure Wayland session , GNOME plus the
NVIDIA open driver, no XWayland required. X11 also has to keep working.

| Job | Wayland approach | X11 fallback |
|---|---|---|
| Fan control | `gb_gaming/nvml_fan.py` via pkexec | `nvidia-settings` + Coolbits |
| Power limit | `gb_gaming/nvml_control.py set-power` or `nvidia-smi -pl` | same |
| Clock control | `nvml_control.py lock-clocks` | `nvidia-settings` offsets |
| Clock offsets (read) | return `","` when there's no X display | `nvidia-settings -q` |
| Display info | KMS/DRM or D-Bus | `xrandr` |

Never do on Wayland:

- Call `nvidia-settings` without first checking that an X display exists.
- Write an `xorg.conf` snippet as a substitute for NVML controls.
- Require the `DISPLAY` env var in a code path that should work headlessly.

Both NVML helper scripts talk to `libnvidia-ml.so.1` through ctypes, so they
have no X11 dependency at all. Prefer them.

### 3. Read the hardware, don't guess it

Anything that decides a thread count, a CPU affinity mask, a fan curve shape,
or a DLSS preset must read real topology , physical cores from `/proc/cpuinfo`,
NUMA nodes from `/sys/devices/system/node/`, L3 from sysfs, GPU details from
NVML. Never hard-code a core count, a GPU name, or a clock value.

`compute_auto_tune()` in `src/src-tauri/src/manager.rs` is the single source of
truth for topology-driven defaults. If you add a knob with a topology
dependency, implement it there.

The Vulkan layer reads `GREENBOOST_SHADER_THREADS` at `vkCreateDevice` time for
its PSO compile pool. It must never call `nproc` itself.

### 4. No absolute home paths in shipped code

No `/home/<username>/...` anywhere that gets installed. Use `$HOME`, `~`, or an
env-overridable default like `${GB_PROTON_SRC:-$HOME/Dev/...}`. Same for
secrets: read them from the environment, never inline them.

### 5. Don't commit build output or DLLs

`.gitignore` covers it, but for the record: no `*.so`, no `*.spv`, no
`__pycache__/`, no `src/dist/`, no `src/src-tauri/target/`, and **no `.dll`
files**. `libraries/` is a runtime cache the app fills from NVIDIA's GitHub ,
it stays empty in the repo.

---

## Verifying a change

**Vulkan layer:**

```bash
make vulkan
GREENBOOST_VULKAN=1 GREENBOOST_VK_DEBUG=1 vkcube &
journalctl --user --since "5 seconds ago" | grep -i greenboost
```

**NIS dispatch:**

```bash
GREENBOOST_VULKAN=1 GREENBOOST_NIS=1 GREENBOOST_NIS_DISPATCH=1 \
  GREENBOOST_VK_DEBUG=1 vkcube
```

**Proton wrapper, without launching a game:**

```bash
GREENBOOST_DRY_RUN=1 STEAM_APPID=123456 ./greenboost_proton/proton run /bin/true
```

**Live stats from a running game:**

```bash
kill -USR1 $(pgrep -f wine64-preloader | head -1)
journalctl --user -n 20 | grep -i gbvk
```

---

## Layout

| Path | What lives there |
|---|---|
| `greenboost_vulkan_layer.c` | The Vulkan implicit layer , memory tiering, NIS, Reflex |
| `greenboost_gl_layer.c` | The OpenGL layer (`make gl`) , same idea, less mature |
| `greenboost_ioctl.h` | IOCTL interface to the parent `greenboost.ko`. Copied from the core repo , keep it in sync, don't invent entries |
| `gb_gaming/` | Python helpers shared by the Tauri backend and the Proton wrapper |
| `greenboost_proton/proton` | The Proton wrapper |
| `src/src-tauri/src/` | Tauri Rust backend (manager, scanner, optimizer, dlss, profiles, live_stats, nvml_reader, global_settings) |
| `src/src/` | React/TS frontend |
| `profiles/per-game/` | Per-game JSON profiles |
| `install.sh` | Full system installer |
| `ui_guidelines.md` | Design reference for UI work , colors, spacing, layout |

Things that belong to the **parent** GreenBoost repo and should never appear
here: `greenboost.c`, `greenboost_cuda_shim.c`, `greenboost_netd.c`, `Kbuild`,
`dkms.conf`, or any CUDA inference code.

---

## Licensing

GPL v2. By contributing you're agreeing your contribution ships under it.

NVIDIA-authored DLLs fetched at runtime are not derivative works of this
project and are not redistributed by it.
