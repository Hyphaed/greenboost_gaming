# External DLL folders, DLSS 5, and ReShade add-on placement/install

Background for the `external_dll_dir` / `external_dll_targets` / `external_dll_disabled` /
`reshade_dir` / `reshade_addons_enabled` mechanism in `game_overrides.rs`, `dll_folders.rs`,
`gb_gaming/dlss_updater.py`, `gb_gaming/reshade_installer.py`, and
`greenboost_proton/gb_proton_main.py`'s `_external_dll_plan()`. Started 2026-08-28 after a
real FF7 Rebirth launch surfaced a placement bug and a round of research into whether DLSS 5
Neural Rendering could be wired up at all; extended the same day twice more , first once a
non-game-locked Neural Rendering add-on turned out to exist (closing the placement gap the
first pass had found), then again once the owner decided GreenBoost should fetch ReShade
itself rather than only detect it (closing the "nothing to place the add-on into" gap the
second pass had found). See "Neural Rendering needs a ReShade add-on" and "The standing rule
changed" below for what changed at each step and what's still unverified.

## The version floors in `dlss_updater.py`'s `KNOWN` table are measured, not guessed

`KNOWN["nvngx_dlss.dll"]` and friends carry a floor of `(310, 8, 0, 0)`; the Streamline
components carry `(2, 13, 0, 0)`. Both came from running `_read_pe_fileversion()` against a
real Streamline 2.13 bundle on disk (`~/Downloads/SL 2.13/`), not from a changelog
or a guess. If you bump these again, do the same: read the actual file version out of a real
downloaded bundle. `refresh_manifest()` and `scan_paths()` will keep widening the floor
against whatever's newest in the cache regardless, so a stale constant here isn't fatal — it
just means Sync won't flag an update it should until the next widening pass runs.

`nvngx_deepdvc.dll`, `sl.deepdvc.dll`, `sl.directsr.dll`, and `sl.nvperf.dll` were added the
same day, sourced from files "FINAL FANTASY VII REBIRTH" actually ships
(`Engine/Plugins/StreamlineSubset/Binaries/ThirdParty/Win64/`) — floors are that install's
shipped versions, not upstream releases we've verified exist.

## `nvngx_dlssnr.dll` / `sl.dlss_nr.dll`: recognised, not fetchable, and that's intentional

These two are DLSS 5 Neural Rendering's runtime files. They surfaced publicly via a leaked
NBA 2K27 early-access build in late August 2026 — NVIDIA has not shipped an official release
containing them. `KNOWN` lists them with `source="bundled-or-mirror"` like every other DLL,
which means Update/Sync will honestly fail on them (empty bundle, mirror 404) until NVIDIA
actually ships something. That's correct behavior, not a bug: GreenBoost never claims a file
is available when it isn't. If you have the leaked files yourself, the external-DLL-folder
mechanism below is the only path — point a folder at them and use the overlay, same as any
other user-supplied DLL.

## Why the exe directory isn't enough: FF7 Rebirth ships two copies of `nvngx_dlss.dll`

The original `external_dll_dir` mechanism (pre-2026-08-28) symlinked every `.dll` in the
user's folder straight into the game's exe directory. That's correct for a flat installer
layout like "007 First Light", and silently wrong for anything built on Unreal Engine.
Confirmed against a real install of "FINAL FANTASY VII REBIRTH": the exe Steam launches is a
thin top-level stub (`ff7rebirth.exe`) that execs a real, nested binary three directories
down (`End/Binaries/Win64/ff7rebirth_.exe`). Neither directory is where the game's DLSS files
actually live — those are under
`Engine/Plugins/StreamlineSubset/Binaries/ThirdParty/Win64/` (the Streamline set: `sl.dlss.dll`,
`sl.interposer.dll`, `sl.reflex.dll`, etc.) **and, separately**,
`Engine/Plugins/DLSSSubset/Binaries/ThirdParty/Win64/` (a second, independent copy of
`nvngx_dlss.dll`). Symlinking into the exe directory would have overlaid nothing the game
actually loads.

`external_dll_targets` fixes this: the Suite's own scanner already walks the install tree
looking for known DLL names (`scanner.rs`'s `nvidia_dll_target_map()`), so `Games.tsx`'s
`externalDllTargets()` turns those findings into a `{lowercased_name: [directories]}` map and
saves it on the per-game profile. `_external_dll_plan()` (`gb_proton_main.py`) and
`install_external_folder_into_game()` (`dlss_updater.py`) both consult it: a name with an
entry links into every listed directory instead of the exe directory. Verified on a real
launch, 2026-08-28 14:55 — journalctl logged `external_dll_dir: 12 of 12 DLL(s) overlaid`
(11 files, one of them — `nvngx_dlss.dll` — targeting two directories), and the filesystem
confirmed real symlinks landed in both `StreamlineSubset` and `DLSSSubset`, each with a
`.gb_bak` of the shipped original sitting next to it.

## The bug that measurement exposed: an unmapped name fell back to the wrong directory

The same real launch put `nvngx_dlssnr.dll` and `sl.dlss_nr.dll` — files FF7 Rebirth has
never shipped under any name, so the scanner had nothing to find — at the **top-level Steam
install root**, not next to the exe and not next to the Streamline interposer. The reason:
`_external_dll_plan()`'s old fallback for an unmapped name was `game_dir`, which is
`dirname(argv[2])` — the directory of whatever Steam actually invokes. For FF7 Rebirth that's
the top-level launcher stub's directory, not `End/Binaries/Win64/`. Windows' DLL search order
never looks in a raw install root, so those two files were placed somewhere the running
process could never load them from, no matter what.

Fixed by widening the fallback: when a `sl.*.dll` or `nvngx_*.dll` name has no explicit
target but the scanner found `sl.interposer.dll`'s own directory, an unmapped Streamline-family
name now falls back there instead of `game_dir` — Streamline's plugin loader is the one place
we can be confident actually gets probed for feature DLLs at runtime, and the scanner already
found it directly (the game ships `sl.interposer.dll` itself). A non-Streamline name, or a
game where the interposer's directory isn't known, keeps the old `game_dir` fallback
unchanged. Covered by
`test_unmapped_streamline_name_falls_back_to_interposer_directory` in both
`tests/test_external_dll_paths.py` (the launch overlay) and `tests/test_external_dll_install.py`
(copy-in).

This does **not** make Neural Rendering work — see below. It only means that if you drop
those files in yourself, they land next to the interposer instead of somewhere inert.

## Per-DLL enable/disable

Before 2026-08-28, every `.dll` in the configured folder was linked in unconditionally.
`GameOverrides.external_dll_disabled` (a list of lowercased filenames) lets a specific file
be excluded from both the launch overlay and copy-in while staying in the shared folder —
useful for keeping an experimental file like `nvngx_dlssnr.dll` around without it being
silently added to every game that shares the folder. Empty list (the default) reproduces the
original all-or-nothing behavior exactly.

## Neural Rendering needs a ReShade add-on, which the DLL overlay couldn't place until now

Researched in depth 2026-08-28 after a user asked for it directly, and revisited the same day
once the picture changed. The first pass concluded Neural Rendering couldn't be wired up in
FF7 Rebirth at all, because the only DLL 5 add-on anyone had seen, `renodx-control-rr.addon64`,
turned out to be locked to a different game. A `strings` pass on that binary found the literal
sequence `"not the game process "` immediately followed by `"Control_DX12.exe"` — it checks
the running process by name and refuses to attach to anything else. Every log string in it is
prefixed `control-rr:`, and its own description reads "DLSS Ray Reconstruction for **Control's**
ray tracing". It will not do anything for FF7 Rebirth, or for any other game.

RenoDX (`github.com/clshortfuse/renodx`) does have a generic, public, game-agnostic
Streamline-hooking utility (`src/utils/dlss_hook.hpp`, used by the `dlssfix` addon) —
GreenBoost's own `write_reshade_dll_paths_impl()` (`dll_folders.rs`) already targets exactly
that mechanism, writing `[RENODX-DLSSFIX]` `DLSSPath`/`StreamlinePath` into a detected
`ReShade.ini`. That part is real and was already correctly wired before this change.

What's different this time: a **generic**, non-game-locked Neural Rendering add-on exists ,
`renodx-dlss5.addon64`, distributed through the RenoDX Discord, described as "Generic
experimental DLSS Neural Rendering post-pass for DX12 games using NGX or Streamline DLSS".
It isn't merged upstream and its own description calls it experimental, but it isn't
process-locked the way the Control build is. The mechanism gap this document originally
flagged, no way to place a `.addon64` file anywhere useful, is now closed. What's still true,
and won't change here, is what's below.

## What GreenBoost actually added: `.addon64`/`.addon32` placement via `reshade_dir`

Both DLL mechanisms, the launch-time symlink overlay (`_external_dll_plan()` in
`gb_proton_main.py`) and the permanent copy-in (`install_external_folder_into_game()` in
`dlss_updater.py`), now accept ReShade add-on files alongside `.dll`. They target a new
per-game field, `reshade_dir`: the directory a `ReShade.ini` was actually found in
(`detect_reshade_setup_impl()`, `dll_folders.rs`), because that's the one directory this
project has real evidence a Windows process loads from , the user's own ReShade install put
it there. An add-on with no `reshade_dir` known is skipped, with a stderr note, never placed
at `game_dir` , for an Unreal title `game_dir` is the install root, and Windows' DLL search
order never looks there. That's the same reasoning that motivated `external_dll_targets`
below, applied to a case the target map itself can't answer (a game scanner can't find a
directory for a file the game never shipped).

An unmapped `nvngx_*.dll` also now lands in `reshade_dir`, in addition to the interposer
fallback, when both are known: several NGX loader shims, including this addon, call
`LoadLibrary` on the bare filename, which searches the *executable's* directory first, not
the Streamline plugin directory , confirmed by reading `renodx-dlss5.addon64`'s own strings:
`"LoadLibrary(nvngx_dlssnr.dll) failed, error "`. `sl.*` plugin DLLs are deliberately not
duplicated there , the interposer loads its own plugins from its own directory, and a second
copy at a different version next to the exe is exactly the ABI split the existing
`sl.*.dll` version-consistency warning (`gb_proton_main.py`) exists to catch.

Turning the feature on (`reshade_addons_enabled`) also makes the wrapper append
`<proxy>=n,b` to `WINEDLLOVERRIDES` (default proxy `dxgi`) so Wine loads ReShade's own proxy
DLL natively before its Wine builtin. This needed its own code path: the existing per-game
`env` block only ever does `setdefault`, so a `WINEDLLOVERRIDES` Steam already exported would
silently swallow whatever the profile asked for. `WINEDLLOVERRIDES` now always appends
(`;`-joined), whether it comes through `env` or through this toggle.

## The standing rule changed: GreenBoost now installs ReShade itself

Everything above was written on the assumption that GreenBoost would only ever *detect* an
existing ReShade install, never fetch one. That was a deliberate standing rule
(`GreenBoost never installs ReShade or RenoDX`), and it held right up until the owner looked
at what it actually cost: with no ReShade on the machine, the mechanism above has nothing to
place an add-on *into*. Asked directly whether to keep the rule or fetch ReShade, the owner
chose to fetch it , see the decision recorded in the session, 2026-08-28.

**What changed:** `gb_gaming/reshade_installer.py` fetches ReShade , the neutral, open-source
(BSD-3-clause) post-processing injector, not RenoDX and not any add-on , directly from
reshade.me, on explicit user action (the "Install ReShade" button, or `install_reshade`).
Nothing is bundled or redistributed: this is the same live fetch-and-cache pattern already
used for dxvk-gplasync and RenderDoc, extended to one more upstream project. reshade.me's own
terms ask for exactly this ("Do NOT share the binaries or shader files. Link users to this
website instead.", read live 2026-08-28) , GreenBoost fetches from their site at use time, it
never mirrors or ships the binary itself.

**What didn't change:** GreenBoost still never installs RenoDX, still never authors the code
that talks to any add-on's own feature API. That boundary was never about ReShade specifically
, ReShade is a neutral host runtime with no opinion about DLSS, Neural Rendering, or any other
effect; RenoDX (and any other add-on) is the thing that actually does something game-specific,
and authoring or installing *that* remains out of scope.

**Mechanism, verified against the real ReShade 6.8.0 "with full add-on support" build on
2026-08-28** , not assumed from the community reference script this was checked against
(`github.com/kevinlekiller/reshade-steam-proton`, GPL-2.0, 778 stars, which does the same job
via `7z`):

- The installer `.exe` (`file` reports it as a ".NET Mono assembly") is *also* a valid ZIP
  archive , confirmed with `python3 -m zipfile`. This module extracts `ReShade64.dll` /
  `ReShade32.dll` with the stdlib `zipfile` module directly , no `7z`/`p7zip` dependency for
  this piece, unlike the reference script.
- `d3dcompiler_47.dll`, which ReShade needs and Windows doesn't ship, was already present in a
  real FF7 Rebirth wine prefix (`compatdata/2909400/pfx/drive_c/windows/{system32,syswow64}/`)
  , Proton/DXVK stages it. That's checked first, no network involved. Only when it's genuinely
  absent does the module fall back to the same community-vetted source the reference script
  uses (a Firefox 62.0.3 installer that legitimately bundles the DLL), hash-verified against
  that script's hardcoded SHA-256 for both architectures , both re-checked live against the
  real file on 2026-08-28, not trusted blindly from a multi-year-old script. That fallback path
  does need `7z`/`7zz`/`7za` (the Firefox NSIS installer is *not* zipfile-readable , verified,
  `zipfile.BadZipFile`) and is skipped, not fatal, when none is on PATH , this machine doesn't
  have one installed, and didn't need it for the real FF7 Rebirth install.
- A real end-to-end run on this machine, 2026-08-28, installed `dxgi.dll` (5,592,064 bytes, the
  real `ReShade64.dll`) plus `d3dcompiler_47.dll` and a minimal `ReShade.ini` into
  `End/Binaries/Win64/`, then a real uninstall removed exactly those three files and nothing
  else, confirmed by directory listing before and after.

## What this still doesn't close, and can't

- **Placement is not the same as it working.** Nothing here proves `renodx-dlss5.addon64`
  actually attaches to FF7 Rebirth's DX12 renderer, still less that it produces a working
  frame. The add-on is described as experimental by its own author, and it still isn't on this
  machine , only the Control-locked build is (see above).
- **Driver floor.** The addon's own strings state it needs "driver 616+ and an RTX 50 series
  GPU". An RTX 50-series card qualifies; the driver version is whatever's actually installed,
  and this project makes no attempt to verify or enforce that floor.
- **vkd3d-proton is untested against this add-on's D3D12 hook path.** ReShade's add-on API
  hooks a real D3D12 device; vkd3d-proton translates that to Vulkan. Whether the hook survives
  that translation is unverified.

If NVIDIA ships an official Streamline release with Neural Rendering, or RenoDX merges real
per-game support, `nvngx_dlssnr.dll` / `sl.dlss_nr.dll` are already recognised by `KNOWN` and
the scanner (`nvngx_dlssnr.dll` → "DLSS Neural Rendering", `sl.dlss_nr.dll` → "Streamline DLSS
Neural Rendering") , Update/Sync will start working on them the day a real source exists,
with no code change needed here.

## DLSS 5 becomes a one-toggle Suite feature, targeting "007 First Light" , 2026-08-31

Everything above built the *mechanism*. This section covers turning it into a *feature*:
`gb_gaming/dlss5.py`, `src/src-tauri/src/dlss5.rs`, the `dlss5_enabled` / `dlss5_addon` fields on
`GameOverrides`, and a new "DLSS 5 (Neural Rendering)" panel in `Games.tsx`.
`_external_dll_plan()` (`gb_proton_main.py`) and
`install_external_folder_into_game()` (`dlss_updater.py`) did **not** need to change , they
already place `*.addon64` and an unmapped `nvngx_*.dll` into `reshade_dir` exactly as this
feature needs; `dlss5.py` is a configurator that computes the same fields the External DLLs
panel fills in by hand.

### The process-gate patch, now automated and disassembly-confirmed

The manual `dd` patch from the FF7 Rebirth session (byte offset `0x172b75`) is now
`gb_gaming.dlss5.patch_addon_process_gate()`. Disassembling the add-on (`objdump -d -M intel`)
confirmed exactly what that offset is for: at `0x1800163dc`,

```
lea rdx, [rip+0x15e192]        # 0x180174575 , "Control_DX12.exe"
mov rcx, r14                    # the running process's own exe name
call 0x180118460                 # byte-at-a-time case-folding strcmp, no length arg
test eax, eax
```

The callee at `0x180118460` is a plain `_stricmp`-shaped loop , no length prefix, no hash,
just a byte compare until a mismatch or both strings hit NUL. That means the patch is exactly
as safe as it looked from the hex dump alone: overwrite the NUL-terminated name in place, pad
with zeros, nothing else about the binary's control flow needs to change. The slot itself is
19 bytes (from immediately after the anchor string `"ing for NGX runtime)\0"` to the first
non-zero byte of the next field, a pointer). `patch_addon_process_gate()` locates that slot by
anchor rather than hard-coded offset, so it survives the add-on being relinked at a different
address as long as the anchor log string itself is unchanged; it refuses to patch (clear error,
no partial write) if the anchor isn't found exactly once, or if the target name doesn't fit in
19 bytes , `007FirstLight.exe` is 17 characters + NUL = 18 bytes, one byte to spare.

### 007 First Light is the easy layout the mechanism was already built for

Unlike FF7 Rebirth's launcher-stub-plus-nested-exe Unreal layout, 007 First Light (Glacier
engine, Steam appid `3768760`) is flat: `Retail/007FirstLight.exe` sits next to the entire
Streamline set the game ships natively (`sl.interposer.dll`, `sl.dlss_g.dll`, `sl.dlss_d.dll`,
`nvngx_dlssd.dll`, and others , confirmed present on disk). `reshade_dir` and the interposer's
own directory are the same directory for this game, so the add-on and `nvngx_dlssnr.dll` both
land in `Retail/` with no fallback-chain ambiguity to reason about.

One thing that surprised this pass: 35 files in `Retail/` already carry `.gdlss_bak` /
`.gdlss_original` / `.bak.<epoch>` suffixes from an unrelated third-party DLSS swapper the
owner had run before this session. `dlss5.preflight()`'s "No foreign DLL-swapper backups"
check surfaces this as a warning (never touches those files, never claims they're a problem ,
just states they're there and aren't ours).

### Every file placed in the asset folder is used , no silent disabling

First cut of this feature had `enable_for_game()` disable every Streamline DLL except
`nvngx_dlssnr.dll` by default (an "ABI-split safety" call this project made on its own, since
the add-on's own string, `"DLSS-NR direct: LoadLibrary(nvngx_dlssnr.dll) failed, error "`,
shows it calls NGX directly and doesn't need the rest of Streamline). The owner corrected this
2026-08-31: GreenBoost never fetches or bundles any of these files , the user places them by
hand , and a file placed there being silently inert contradicts what anyone doing that would
reasonably expect. `enable_for_game()` now symlinks **every** file actually present in
`asset_dir()`, full stop; there is no partial-overlay default and no separate opt-in toggle for
"the rest of it".

The ABI-split risk is real and unchanged , 007 First Light ships its own, different-version
Streamline set and native Ray Reconstruction files (`sl.dlss_d.dll`, `nvngx_dlssd.dll`, which
SL 2.13 doesn't even have) , but it's now disclosed, not enforced: `Dlss5FilesModal.tsx` shows
the exact folder path and every known filename with a checkmark for what's already there,
states plainly that everything placed is used, and separates the two files the feature actually
needs (`renodx-control-rr.addon64`, `nvngx_dlssnr.dll`) from everything else, which is optional
and used-if-present. `list_assets()` (`gb_gaming/dlss5.py`) backs both that modal and the
`dlss5_list_assets` Tauri command , it's a live read of the folder, not a static list.

### Preflight is measured, not a checklist that always passes

`dlss5.preflight()` runs `nvidia-smi` for real (GPU name against an `RTX 50\d\d` pattern,
driver major version against the add-on's own stated `616+` requirement) rather than assuming
the machine it's running on satisfies either. The driver check is a **warning, not a block**:
"616+" is the add-on author's human-readable requirement string, not something this project has
independently confirmed is an enforced gate versus aspirational text , and this machine's real
driver (`610.43.02` at the time this was written) is below it either way, so treating it as a
hard block would make the feature untestable on the only hardware available to verify it on.

### What this still doesn't prove, restated for 007 specifically

The FF7 Rebirth run got the patched add-on to attach and hook NGX
(`NGX D3D12 exports hooked (CreateFeature + EvaluateFeature)`), then stopped at
`RR: HOOKED (no SR feature yet)` , the Neural Rendering pass itself never fired, because no NGX
DLSS feature had been created in-game yet. `NeuralUplift=1` was already set in that run's
preset; it didn't matter, because DLSS itself was never turned on inside the game. That's now a
first-class precondition this document states plainly rather than a footnote: **enabling DLSS 5
in the Suite does not turn DLSS on inside 007 First Light's own graphics menu, and Neural
Rendering cannot do anything until that separate step happens.** `seed_reshade_preset()` sets
`NeuralUplift=1` and the rest of FF7's observed NR defaults in `[renodx-preset1]`; it cannot
reach into the game's own settings.

The add-on also remains fingerprinted to Control's own shaders and root signature past the
process-name check (`PROBE(diffuse 0x87EDDD47)`, `b0 is not a root CBV in this layout`,
`sys_constants captured from b0`) , nothing in this patch touches that. Getting the add-on to
*attach* to 007 First Light is now one toggle. Getting its Control-specific probes to produce a
correct frame against Glacier engine's actual render targets is a different, unverified
question this patch does not answer, and the Suite's own UI says so at the point of the toggle.

### Building the real, official RenoDX add-on for 007 First Light was attempted and abandoned

Upstream RenoDX (`github.com/clshortfuse/renodx`, checked out at
`~/Dev/greenboost_all/greenboost-sources/renodx`) turned out to already have a dedicated,
official `src/games/007firstlight/` module , real shaders matched to this game's actual root
signatures, doing HDR tonemapping/color grading and fixing a DLSS Frame Generation HUD-ghosting
bug via the game's own native Streamline pipeline. It has no Neural Rendering code anywhere in
the tree; NR was never merged upstream for any game, and only exists in the leaked Control
build this document is otherwise about.

Building it from source was attempted 2026-08-31 and abandoned: the top-level `CMakeLists.txt`
unconditionally `find_program`s `fxc.exe` (`REQUIRED`, before `project()` is even declared),
the legacy DirectX HLSL compiler Microsoft has never shipped a Linux build of, ever , not via
any package, not via the Windows SDK's redistributable pieces, nothing. Every one of RenoDX's
own CI lanes (`clang-x64.yml`, `ninja-x64.yml`, `visual-studio-x64.yml`, and the rest) runs on
`windows-latest` / `windows-2022` , there is no Linux build path anywhere in this project, and
it has evidently never been exercised on one. `xwin` (the MSVC CRT + Windows SDK header/lib
splatter used for `clang-cl` cross-compilation) covers the pieces needed to *link* a Windows
binary from Linux and was confirmed reachable (`xwin 0.10.0`, static Linux binary, sysroot splat
started successfully before being stopped), but it does not and cannot provide `fxc.exe` itself
, that tool ships only inside the Windows SDK's own Windows-native installer. Making this
buildable would need Wine plus a legally sourced `fxc.exe`/`dxc.exe`/`slangc.exe`, plus a
`CMAKE_CROSSCOMPILING_EMULATOR`-style wrapper so the build's own host tools
(`embed_file`, `analyze_shader_deps`, `decomp`) , which the build compiles with the *same*
cross toolchain as the addon itself and then runs mid-build to generate embedded shader headers
, could execute at all on a non-Windows host. That's a materially larger, EULA-sensitive,
open-ended undertaking than the xwin sysroot alone, and was explicitly not pursued further; a
real Windows machine remains the practical way to build `renodx-007firstlight.addon64` today.

## Superseded 2026-09-02: the RenoDX mechanism above never got past its own stated ceiling

Everything above this line describes the ReShade / `renodx-*.addon64` mechanism. It is no
longer what the Suite installs. Kept for the record of what was tried and why, not as current
behavior , see `CHANGELOG.md`'s 2026-09-02 entry for the full pivot writeup.

The RenoDX add-on's own process-name-patch ceiling, stated two sections up
("Getting its Control-specific probes to produce a correct frame against Glacier engine's
actual render targets is a different, unverified question"), turned out to be exactly right and
final: a real 007 First Light launch with the patched add-on hooked NGX and progressed to
`RR: WAITING FOR GUIDES (game SR (re)created)`, and never went further, on any run. The reason
is structural, not a missing step , Control's Neural Rendering pass finds its depth/motion
guides by matching **Control's own shader signatures and root-CBV layout**. Renaming the target
process changes what game the DLL attaches to; it does nothing about the shader-level matching
underneath, which was never written for any other title. No amount of config or quirk wiring
fixes that , even the real, official RenoDX 007 First Light module
(`src/games/007firstlight/`, see the abandoned-build section above) has zero Neural Rendering
code in it.

**The replacement mechanism: OptiScaler's own Neural Rendering pass**
(`Dagherbou/OptiScaler_DLSSNR`, later replaced in turn by GreenBoost's own NVIDIA-only stripped
fork , see `libraries/dlssnr/FORK_NOTES.md`). It runs generically over the upscaler's own final
output buffer and never touches a game's internal shaders at all, which is exactly why it needs
no per-game shader-matched module. `gb_gaming/optiscaler_installer.py` installs it as a DLL
proxy (`winmm.dll` and similar), and `gb_gaming/dlss5.py` writes `Enabled=true` directly into
its own `[DlssNr]` ini section , the Suite's one toggle is now the only control surface; there
is no in-game overlay, no ReShade dependency, and (see below) no in-game-menu precondition
either.

**The "DLSS must be turned on in-game first" precondition stated above (007-specific section) is
now cleared, confirmed by a real log, not by inspection.** That precondition was true for the
RenoDX/Control mechanism, which could only ever hook an already-running NGX feature. Under
OptiScaler it does not apply the same way: a real 007 First Light launch with DLSS 5 enabled via
the Suite produced, in the game's own log,

```
DlssNr_Dx12::Dispatch DLSS-NR running at 2560x1440, guides 1706x960 (preset 0, intensity 1, style 0)
DLSS-NR readback DLSSNR.Intensity -> 1 (we wrote 1, result 0x1)
```

, the pass dispatches every frame and echoes back the exact parameter values the ini set. That
readback is the verification mechanism going forward: it distinguishes "the Suite wrote a
number" from "the model received that number", the same distinction this document's FF7 Rebirth
section spent several paragraphs establishing was still missing for the old mechanism.

**Still unmeasured, same caveat as before, just relocated:** whether Neural Rendering changes
the picture on 007 First Light , that needs an A/B capture (`[DlssNr] AutoCapture=true` writes
matched before/after frames to a `dlssnr-capture/` folder) this pivot doesn't include. The driver
floor is unchanged in kind, if not in number , OptiScaler's own Neural Rendering pass needs an
RTX 50-series GPU, and this machine's driver (`610.43.02`) is treated as a warning, not a hard
block, for the same reason the old `616+` check was: it's the only hardware available to verify
against, and it already demonstrably runs.
