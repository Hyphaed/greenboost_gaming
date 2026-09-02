# DLSSNR fork notes

What this fork is, why it exists, and what has changed vs. upstream. The full
source tree (this file's own history, the strip commits, the VM setup log
`vm11_gaming.md`) lives outside this repo at `../../dlssnr` — only the built
runtime this file sits beside (`runtime/`) ships with the Suite.

## What this is

A fork of [`Dagherbou/OptiScaler_DLSSNR`](https://github.com/Dagherbou/OptiScaler_DLSSNR)
(itself a fork of [OptiScaler](https://github.com/optiscaler/OptiScaler)), chosen because
it already has a working DLSS Neural Rendering pass merged in. Baseline:
`v0.1.1.5-dlssnr`, commit `739fe86`, unmodified.

Purpose: get the full DLSS 5 feature set — Super Resolution, Frame Generation, Neural
Rendering, Reflex — working for Steam games under Proton, starting with **007 First
Light** (Glacier engine, appid `3768760`).

**Naming:** this project is **DLSS Neural Rendering / DLSSNR**. It is not called
"GreenBoost Neural" — DLSS and Neural Rendering are NVIDIA's technology, not GreenBoost's.
GreenBoost is the integrator. Window titles, product strings, and the `.ini` header are
not rebranded. Load-bearing NVIDIA names (`nvngx.dll`, `nvngx_dlssnr.dll`, `sl.*.dll`)
stay exactly as upstream defines them.

## Why this fork diverges from upstream

Upstream OptiScaler is vendor-agnostic: it can substitute AMD FSR2/FSR3.1, Intel XeSS, or
NVIDIA DLSS for each other, and carries AMD's FidelityFX frame-interpolation engine,
Intel's XeFG/XeLL, and AMD Anti-Lag 2 alongside the NVIDIA paths. GreenBoost targets
NVIDIA hardware exclusively, so none of that is needed — and NVIDIA already ships its own
frame generation (`nvngx_dlssg.dll` / `sl.dlss_g.dll`), so the AMD-derived substitute is
dead weight, not a feature gap.

**Owner decision (2026-09-02): remove all non-NVIDIA code**, including the `inputs/`
interception layer that lets a game which only shipped FSR2/XeSS receive DLSS instead of
its native upscaler. Known cost of that: this fork only works in games that already ship
DLSS/NVNGX or Streamline. 007 First Light qualifies.

One thing that looks like it should be casualty of the strip but isn't: OptiScaler's
bolt-on frame generation (`FGInput::Upscaler`) is not AMD-specific — it feeds a
vendor-neutral interface and works with NVIDIA's own `FGOutput::DLSSG` backend. Removing
AMD/Intel code does not remove OptiScaler's ability to add frame generation to games that
never shipped it themselves, as long as they're on NVIDIA output.

## Status as of 2026-09-02 — strip executed, build verified, wired into the Suite

- **Source: stripped and adapted.** Two commits landed on the fork's `master`:
  `7203c7e` (rename `OptiScaler.*` → `GreenBoostDLSS5.*`, delete AMD/Intel backends +
  the ImGui menu stack) and `759b8d0` (fix every surviving reference to the deleted
  code). `OptiScaler/backends/` and `OptiScaler/menu/` no longer exist in the tree.
- **Build: verified clean.** `MSBuild GreenBoostDLSS5.sln -p:Configuration=Release
  -p:Platform=x64` returned exit code 0 on a clean intermediate directory, on the
  Windows VM (MSVC 2022 / toolset 14.44.35207 / Windows SDK 10.0.26100.0). Never built
  before this session.
- **Export table checked against `Source.def`.** The built `GreenBoostDLSS5.dll`
  exports 902 names; all 848 `Source.def` entries are present, 0 missing — the
  "silent no-load" risk the handoff flagged as open is closed.
- **Wired into the Suite.** `gb_gaming/optiscaler_installer.py` now installs this
  fork's runtime (`libraries/dlssnr/runtime/`, next to this file) instead of fetching
  upstream `Dagherbou/OptiScaler_DLSSNR` from GitHub. The full source tree that builds
  it — this fork's own `.git`, `OptiScaler/`, `external/` submodules — lives outside
  the Suite repo at `../../dlssnr` (one level up from `greenboost_all/`), since only
  the ~7 MB build output is a runtime dependency.
- **007 First Light confirmed live , the full story, 2026-09-02.** The
  dispatch confirmation this section originally rested on
  (`DlssNr_Dx12::Dispatch DLSS-NR running at 2560x1440 …`, the per-frame
  parameter readback `DLSSNR.Intensity -> 1 (we wrote 1, result 0x1)`, both
  in the game's own log) was real but not sufficient: a real playtest turned
  up severe image corruption (blown highlights, a magenta cast, vertical comb
  artifacts) that no log line had caught. Fixed the same day, verified by a
  real playtest, not just a rebuild , the full chain below, kept because the
  two wrong turns in it are exactly the kind of thing worth not repeating.

  **Diagnosis.** DLSS Super Resolution's own preset (forced to Preset M) and
  DLSS Frame Generation (forced on) were both tried and reverted first, each
  confirmed via a real relaunch not to be the cause. That pointed at the
  strip itself. `git diff 739fe86 759b8d0` (unmodified baseline vs. the
  strip-adaptation commit) found a real, asymmetric change in
  `OptiScaler/hooks/Streamline_Hooks.cpp`'s `spoofArch()`: the baseline only
  spoofed GPU architecture to 0 when an FFX-backed NVNGX FG replacement
  (Nukems/Arturs/FFX/Combo , all AMD-derived, all deleted by the strip) was
  active; the strip dropped that guard but left the `setArch(0)` call it
  gated, so it now fires unconditionally for every `kFeatureDLSS_G` query on
  every game, whenever the game's own `nvngx_dlssg.dll` hasn't loaded yet.

  **Attempt 1 (wrong): delete the whole check.** Reasoning: the guard's
  condition (`activeFgNvngx != FGNvngxReplacement::None`) could never be true
  again once `FGNvngxReplacement` was deleted, so the branch looked entirely
  dead. Built and deployed via a Windows 11 VM (`z590i_w11`, VMware, reachable
  over SSH on the host's `vmnet8` NAT network , "no Windows environment"
  was never true, it just hadn't been used yet). Build succeeded, export
  table clean, but a real playtest **crashed** the game outright: `int3`
  breakpoint trap inside `007firstlight.exe` itself, not this DLL , the
  signature of an anti-tamper trip, not a code bug. Worse than the corruption
  it replaced. Reverted immediately, live game restored from the cached
  pre-strip build (`v0.1.1.5-dlssnr`, `~/.local/share/greenboost/optiscaler/`)
  to get a working game back while re-diagnosing.

  **Attempt 2 (correct): keep the check, change the spoofed value.** The
  crash showed attempt 1 changed too much at once (removed the availability
  check entirely, not just the bad value). Isolated the actual variable:
  kept the exact same `Nvngx_FG::isDx12Available()`/`isVulkanAvailable()`
  check and timing, but replaced `setArch(0)` with `setArch(currentArch,
  altSystemCaps)` , report the GPU's real, already-detected architecture
  instead of the literal value 0. `setArch()`'s own body sets
  `caps->hwsSupported = true` unconditionally regardless of what architecture
  value it's given, so `architecture=0` + `hwsSupported=true` is a
  combination no real GPU would ever report , the more likely source of the
  corruption than the check's timing or its side effects. Rebuilt on the same
  VM, same command, same checkout (`C:\temp\dlssnr`, commit `759b8d0` plus
  this fix). 0 errors, export table clean (0 of 848 `Source.def` entries
  missing), deployed via the Suite's real `enable_for_game()` path. **Real
  playtest result: no corruption, no crash.** Steam's own process log shows
  a clean exit; `GreenBoostDLSS5.log` (finally produced , `LogToFile` had
  been silently reset to `auto` by two intervening reinstalls, now fixed in
  the shipped ini template too) shows a handful of non-fatal Streamline
  exceptions in the first ~7 seconds of startup, followed by an unbroken run
  of successful `vkQueuePresentKHR` calls through to close.

  **"No ghosting" is not confirmed , correction, 2026-09-02, checked the log
  more carefully after the fact.** `DlssNr_Dx12::Dispatch` (the actual
  per-frame Neural Rendering call) appears zero times in that log.
  `CreateFeature`/`EvaluateFeature` for any DLSS feature , SR, RR, or NR ,
  also appear zero times. `nvngx_dlssnr.dll` is never mentioned at all, not
  found, not loaded. What the log shows 23,500 times instead:
  `hkvkQueuePresentKHR` , the game presenting frames through plain
  Vulkan/VKD3D, no DLSS feature involved. That's consistent with DLSS Super
  Resolution never having been turned on in the game's own graphics menu for
  that session (Neural Rendering does nothing without it , see the ini's own
  `[DlssNr]` comment). If DLSS never engaged, "no ghosting" doesn't mean the
  ghosting is fixed; it may just mean nothing DLSS-related ran. The
  `spoofArch` fix is still confirmed to have eliminated the crash and the
  corruption , those both happened during Streamline's own plugin
  negotiation, which runs regardless of whether the game later turns DLSS on
  , but whether Neural Rendering itself now runs correctly, or ghosts, is
  genuinely untested. **To actually confirm it: turn DLSS Super Resolution on
  in 007 First Light's own graphics settings before the next test, then check
  `GreenBoostDLSS5.log` for `DlssNr_Dx12::Dispatch` lines.**

  The fix is one line, in the fork's own tree at `../../dlssnr` (not yet
  committed there , see that repo's own `git status` before assuming it's
  clean). The built `GreenBoostDLSS5.dll` currently in
  `libraries/dlssnr/runtime/` already contains it.

## The strip — scope

**Delete outright** (alternate upscaler backends, AMD/Intel frame gen, AMD Anti-Lag 2):
- `upscalers/{xess,fsr2,fsr2_212,fsr31,ffx}/`, `include/{fsr2,fsr2_212,fsr3,fsr31}/`,
  `library/{fsr2,fsr2_212,fsr31}/`
- `inputs/{XeSS_*,FSR2_*,FSR3_Dx12.*,FfxApi_Dx12.*,FfxApi_Vk.*,FfxApiExe_Dx12.*}`
- `framegen/{ffx,xefg}/`, `inputs/FG/{FSR3_Dx12_FG.*,FfxApi_Dx12_FG.*}`
- the FFX-backed providers under `framegen/nvngx/` (`Nvngx_FFX`, `Nvngx_Nukems`,
  `Nvngx_Arturs`, `Nvngx_Combo`) — the NVIDIA-facing emulation shell (`Nvngx_FG`,
  `IFGNvngx`, `Nvngx_DllProxy`) stays and reduces to real `nvngx_dlssg` passthrough
- `low_latency/**/*antilag*`, `low_latency/**/*xell*`, `hooks/{Amdxc64,Xell}_Hooks.*`,
  `external/AntiLag2-SDK/`
- `fsr4/`, `proxies/{XeSS,XeFG,XeLL,FfxApi,IGDExt}_Proxy.h`
- `external/{xess,FidelityFX-SDK,FidelityFX-SDK-v2}` submodule entries

**Trim, don't delete** — shared dispatch/config files that reference AMD/Intel *alongside*
DLSS in enums, switches, or boolean predicates: `OptiTypes.*`, `State.h`, `Config.*`,
`DllNames.h`, `hooks/FG_Hooks.cpp` (the sole place FG backends are constructed),
`dllmain.cpp`, `hooks/LibraryLoad_Hooks.cpp`, `upscalers/FeatureProvider_*.cpp`,
`menu/menu_common.cpp` (the largest single pass — 7720 lines, ~160 references, mixing
standalone UI blocks with terms embedded in shared predicates).

**Confirmed unaffected by any of this:** DLSS Neural Rendering (`dlssnr/`,
`shaders/dlssnr/` — verified zero FFX/XeSS/AntiLag references), native DLSS Frame
Generation output (`framegen/dlssg/DLSSG_Dx12`) and input capture
(`hooks/Streamline_Hooks.cpp`, `inputs/FG/Streamline_Inputs_*`).

**Two hazards being handled explicitly, not silently:**
- `Upscaler::XeSS` is enumerator 0 and is `Dx12Upscaler`'s default in `Config.h` —
  deleting it shifts every other enum value, so the default gets explicitly repointed to
  `Upscaler::DLSS`.
- FSR 2.1.2 is the hardcoded universal fallback in every `FeatureProvider_*.cpp`. With no
  non-NVIDIA backend left, those fallback arms become a clean logged failure instead of a
  fabricated backend.

## The 007 First Light ghosting fix (after the strip builds)

Turned out smaller than first assumed: `GameQuirk::IgnoreTagsWithoutHudlessForFG`
**already exists** in this fork (`misc/Quirks.h`) and is fully wired into
`hooks/Streamline_Hooks.cpp`'s tag-capture path — it's just applied to
`zenlesszonezero.exe`, not to 007. Fix is config/quirk wiring, not new code:

1. Try config-only first: `[FrameGen] Enabled=true`, `FGInput=dlssg`, `FGOutput=dlssg` in
   `OptiScaler.ini` for this game.
2. If that's not enough, add `GameQuirk::IgnoreTagsWithoutHudlessForFG` to 007's existing
   `QUIRK_ENTRY` in `misc/Quirks.h`.

Verified by a real launch with DLSS-FG on and reading the OptiScaler log for FG/tag lines
— not by code inspection alone.

## FINAL FANTASY VII REBIRTH — "extremely dark" (2026-09-02)

Reported by the user after a real launch. Checked the real, fresh
`GreenBoostDLSS5.log` from that session before touching anything: zero
`GreenBoost DLSS5 Injected` / `Neural Render OFF` confirmation lines, zero
`DLSS-NR running` lines, and Streamline's own log shows
`NGX_*_GetFeatureRequirements returned 0xbad00001 for adapter: 0 feature: 18`
followed by `Ignoring plugin 'sl.dlss_nr' since it is was not requested by the
host`. **Neural Rendering never ran that session** — not because of a
GreenBoost bug, but because `sl.dlss` (DLSS Super Resolution itself) was also
never requested by the host; only `sl.dlss_g` (Frame Generation), `sl.pcl`,
and `sl.reflex` loaded. Neural Rendering runs after Super Resolution's own
evaluate call, by design (`DlssNr::EvaluateAfterUpscale` is only reached from
inside a successful DLSS SR passthrough — see `inputs/NVNGX_DLSS_Dx12.cpp`) —
with SR never evaluating a frame, NR had nothing to run on. So the darkness
is unrelated to Neural Rendering entirely.

Root cause found via the upstream OptiScaler project's own wiki, not
guesswork: <https://github.com/optiscaler/OptiScaler/wiki/FINAL-FANTASY-VII-REBIRTH>
documents that game update 1.004 broke Unreal Engine's own resource-state
reporting for its color target, causing "Xmas artifacts/dark glitches," fixed
by setting `Hotfix.ColorResourceBarrier=4`
(`D3D12_RESOURCE_STATE_RENDER_TARGET`) in place of the runtime's default
`auto`. A second, independent report of the same darkened-image symptom on
this exact game exists at
<https://github.com/optiscaler/OptiScaler/issues/764> (Linux, FSR path). This
is a real upstream game/engine bug that predates this fork.

Applied as an automatic per-appid hotfix in `gb_gaming/dlss5.py`
(`GAME_INI_HOTFIXES["2909400"]`), written into `GreenBoostDLSS5.ini`'s
`[Hotfix]` section every time Neural Rendering is enabled for this game — the
same pattern the Proton wrapper already uses for
`_inject_ue5_rt_disable()`. Applied directly to the live install for
real verification; **not yet confirmed by an in-game playtest** — that
confirmation is the next step, not something to claim done from log analysis
alone.

## 007 First Light — Frame Generation capped at 2x, and NR status unclear (2026-09-02)

Same real-log-first approach, prompted by the user reporting both symptoms
after a real launch (2m13s session, `GreenBoostDLSS5.log` fresh from 15:28).

**Neural Rendering**: same finding as FF7 Rebirth — zero confirmation lines,
zero `DLSS-NR running` lines, and zero `SuperSampling`
Create/EvaluateFeature calls in the entire session (only 5
`EvaluateFeature` calls total, all Frame Generation). So Neural Rendering did
not run this session either, for the same structural reason: DLSS Super
Resolution itself never evaluated a frame. The game's own Wine registry key
(`HKCU\Software\IO Interactive\007 First Light`) has `DLSSQuality=1` (a real
value, not "off") but a separate `AntiAliasing=1` key that most likely
selects the actual AA/upscaler *method* (TAA vs. DLSS vs. FSR) — `DLSSQuality`
is probably just the preserved quality-mode preference, inert unless
`AntiAliasing` (or whatever key is the real method selector) is set to DLSS.
Not fully confirmed without IOI's own enum mapping — the user should check
the in-game graphics menu's Anti-Aliasing/Upscaler dropdown specifically,
not just the DLSS quality slider.

**Frame Generation stuck at 2x**: confirmed directly from Streamline's own
log line — `Multi-frame not supported, max generated frames 1 (SL Plugin
supports 5, NGX feature supports 1)`. The plugin layer supports up to 5
generated frames (4x+ MFG); the actual `nvngx_dlssg.dll` file loaded
(`Nvngx_FG::EnsureLoaded ... loaded for direct-NVNGX Frame Generation`, real
path checked: the one staged from `/home/ferran/Documents/DLSS5/SL
2.13/nvngx_dlssg.dll`, dated 2026-08-27) only supports 1 generated frame
(2x). `Nvngx_FG` forwards straight through to this real DLL — it is not a
custom/lower-quality substitute — so this is a stale model DLL, not a
GreenBoost algorithm limitation. A newer, MFG-capable `nvngx_dlssg.dll`
(matching a current NVIDIA DLSS SDK release) placed in the same
user-supplied folder should unlock 3x/4x on this RTX 5070.

## FINAL FANTASY VII REBIRTH — the real cause of "Neural Rendering not loading" (2026-09-02)

The "extremely dark" entry above concluded Neural Rendering never ran because DLSS Super
Resolution was never turned on in-game. Wrong — checked the real, fresh `GreenBoostDLSS5.log`
from the actual failing session (`2026-09-02 15:14`, driver `610.43.02` finally live after the
reboot the previous handoff was waiting on) and found DLSS Super Resolution *was* requested and
*tried to create*, twice, and failed both times:

```
[E] FeatureProvider_Dx12::GetFeature DLSS unavailable (GPU DLSS-capable: false, nvngx_dlss.dll found: true)
[E] TryCreateFeature Failed to retrieve feature implementation for 'DLSS'
```

**Root cause, traced to one line.** `GpuInformation::dlssCapable`
(`OptiScaler/misc/IdentifyGpu.h`) is written exactly once in the whole codebase, in
`IdentifyGpu::queryNvapi()`, gated behind a live `NvAPI_Initialize()` session. That call failed
once, ~0.4s into the process. `IdentifyGpu::getAllGpus()`'s cache-validity check only looked at
whether an adapter's `deviceId` was resolved, never whether `dlssCapable` itself was a real
answer — so that one bad NvAPI roll got cached as `false` for the rest of the process, which
meant `Upscaler::DLSS` could never be created, which meant Super Resolution never evaluated a
frame, which meant Neural Rendering (confirmed to only run from inside a successful SR/GreenBoost
upscaler evaluate) never got a chance to run. `dlssnr/` itself was never involved.

**Confirmed as a real, transient race, not a driver limitation**, by the raw Proton/dxvk-nvapi
log for this exact process (`~/.local/share/greenboost/proton-logs/steam-2909400.log`):

```
NvAPI_Initialize: NVIDIA or other suitable device not found or initialization failed
```
...followed 2ms later, on the very next `nvapi64.dll` load, by:
```
NvAPI_Initialize: OK
NvAPI Device: NVIDIA GeForce RTX 5070 (610.43.2)
```
and six more successful `NvAPI_GPU_GetArchInfo: OK` calls through the rest of the 8-minute
session. dxvk-nvapi's `NvAPI_Initialize` needs a live Vulkan device to answer, and under
Wine/Proton's multi-stage D3D12→Vulkan bring-up, the game's very first `nvapi64.dll` load happens
before that device exists — `GreenBoostDLSS5`'s own capability probe (`dllmain`'s early
NvAPI check, and `IdentifyGpu::queryNvapi`) can land inside that exact window. `git diff 739fe86
HEAD` on `IdentifyGpu.cpp`/`FeatureProvider_Dx12.cpp` confirms the capability check itself is
byte-identical to unmodified upstream — the AMD/Intel strip didn't break this, it just removed
the FSR2 fallback that used to silently mask the same `dlssCapable=false` outcome by swapping in
FSR2 instead of returning an error.

**Fix, source only (never the deployed prebuilt copy):**

1. `OptiScaler/misc/IdentifyGpu.h` — new `GpuInformation::dlssCapableKnown` (default `false`),
   distinguishing "NvAPI never answered yet" from "NvAPI answered, this GPU isn't Turing+". Two
   new `IdentifyGpu` statics track a bounded retry budget.
2. `OptiScaler/misc/IdentifyGpu.cpp` — `queryNvapi()` sets `dlssCapableKnown = true` once it
   reaches a live NvAPI session (right before computing `dlssCapable`, unchanged otherwise).
   `getAllGpus()` no longer trusts a cached result while any Nvidia adapter's capability is still
   unknown: it re-runs `queryNvapi()` for just that adapter, capped at 5 retries or 3 seconds
   since the first retry (whichever comes first — a bounded workaround for a race measured at 2ms
   in the real log, not a general "keep trying forever" policy).
3. `[DLSS] ForceCapable` — new config key (`Config.h`/`Config.cpp`, default `false`), for the
   separate case of a GPU NvAPI never reports as capable on some other machine for real (not this
   session's race). Documented in the restored `GreenBoostDLSS5.ini`.
4. `GreenBoostDLSS5.ini`, `LICENSE`, `Licenses/RenoDX_ATTRIBUTION.txt` — these had been deleted
   from the fork's working tree (visible in `git status`, not yet committed) while the post-build
   copy step in `GreenBoostDLSS5.vcxproj` still referenced all three; restored from `git show
   HEAD:<path>` before building, otherwise the build's post-build `copy` commands for the missing
   files would have failed. `ForceCapable=auto` added to the restored ini's `[DLSS]` section.

**Build verified clean** on the same Windows 11 VM as the two ghosting fixes above (`z590i_w11`,
MSVC 2022 toolset 14.44.35207) — `MSBuild GreenBoostDLSS5.sln -p:Configuration=Release
-p:Platform=x64` returned exit code 0. Export table re-checked against `Source.def`: 902 exports,
0 of 850 entries missing, same clean bar as the prior two builds. Deployed into
`libraries/dlssnr/runtime/` (this repo) via the real VM build output, not hand-edited.

**Not yet confirmed by an in-game playtest** — that's the next step, same standard as every other
entry in this file. Checklist: `GreenBoostDLSS5.log` should show `FeatureProvider_Dx12::GetFeature
DLSS unavailable` zero times (an early `queryNvapi Failed to init NvApi` is fine as long as it
isn't the final word — look for a successful NvAPI session before the first `TryCreateFeature`),
followed by `TryCreateFeature Creating DLSS upscaler feature` actually succeeding, then
`DLSS-NR running at {}x{}` and `GreenBoost DLSS5 Injected: Neural Render ON`. DLSS Super
Resolution still needs to be turned on in the game's own graphics menu — that part of the original
diagnosis was correct, just not the whole story.

## DLSS-G "0xbad00002" storm — same session, investigated, not owned-source-fixable

The same `GreenBoostDLSS5.log` session shows 60,662 `NVSDK_NGX_D3D12_CreateFeature` calls for
Frame Generation over ~482 seconds (~126/sec, consistent with a doubled frame rate under 2x MFG),
and **every single one** is immediately followed by Streamline's own proprietary log line:

```
[sl.dlss_g] NGX create feature failed 0xbad00002   (FAIL_PlatformError)
```

That call happens inside `sl.dlss_g.dll`/`sl.common.dll` — closed-source NVIDIA Streamline
binaries, not `dlssnr/`. GreenBoost's own hook (`NVNGX_DLSS_Dx12.cpp:748-762`) correctly creates
its "modded DLSSG" handle and returns success every time, so frame generation keeps working (per
the "Frame Generation capped at 2x" entry above, confirmed on this same class of session) — this
is Streamline running its own internal native-DLSSG platform probe on every create call and
getting a real `FAIL_PlatformError` from the driver-backed NGX, 100% of the time, all session.

Same "platform capability answer wrong for the whole session after an early failure" signature as
the SR bug above, but there is no owned code path between `sl.dlss_g`'s internal probe and the
driver — nothing in this fork's source can patch it directly. Recorded here rather than
speculatively patched: the next real playtest (already needed to confirm the SR/NR fix) should
also record the new `0xbad00002` count. If the NvAPI-readiness race above is the same one
Streamline's internal probe loses, fixing our own timing might not move Streamline's — worth
knowing either way before writing any new code against a binary we don't control.

## Reference assets

`C:\temp\SL 2.13\` holds the real Streamline 2.13 binaries this build targets:
`nvngx_dlss.dll` (SR), `nvngx_dlssg.dll` (FG), `nvngx_dlssnr.dll` (Neural Rendering,
165 MB, always user-supplied — never bundled), and the matching `sl.*.dll` plugin set
(`common`, `dlss`, `dlss_g`, `dlss_nr`, `interposer`, `nis`, `pcl`, `reflex`).
