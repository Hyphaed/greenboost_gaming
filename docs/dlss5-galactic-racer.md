# DLSS 5 Neural Rendering in STAR WARS: Galactic Racer (Proton, RTX 50)

Researched 2026-10-31 for GreenBoost Gaming. Everything below is labelled
**confirmed**, **other game**, **plausible, untested** or **unsupported**. Nobody
has published a Galactic Racer DLSS 5 report that I could find, so the honest
verdict is *experimentally possible*. This is not a "works" claim.

## 1. Executive summary

The game is a 64-bit Direct3D 12 title with its own NGX-based DLSS and no
Streamline, which is the shape the community add-ons are built for. The
shortest route through the Suite is the DLSS 5 panel that already exists
(OptiScaler fork + model). The ReShade add-on route is the documented
alternative. Neither has been run on this game by anyone who reported it.

Two things matter more than the route:

* **The model file.** The Recol community mirror's `nvngx_dlssnr.dll`
  (SHA-256 `dcc0dc24...`) has the same size as the NVIDIA-signed 310.8.0 build
  (SHA-256 `e16bcf15...`, the one in `~/.local/share/greenboost/dlss5/`) but a
  different hash. It matches none of the three builds the community tracks. The
  Suite must not fetch it automatically.
* **Denuvo.** GamingOnLinux says the game ships Denuvo Anti-Tamper. A proxy
  `dxgi.dll` next to a Denuvo game is not covered by any report I found.

## 2. Confirmed Galactic Racer compatibility reports

* The game runs on Proton and is Steam Deck Verified (GamingOnLinux review,
  2026-10-06, Proton 11, AMD GPU). **Confirmed, no DLSS 5.**
* ProtonDB (app 4078430) could not be read (page is script-rendered). A user
  quoted by search results calls DLSS Balanced "extremely blurry" at default
  temporal upscaling. **No DLSS 5 report.**
* The in-game social overlay doesn't work on Proton 11 or Experimental.
* No GitHub issue, pull request, Reddit thread or wiki page mentions Galactic
  Racer plus DLSS 5. `24high/DLSS5-Reshade-Linux-Steam` matched "Galactic"
  only as *Galactic Civilizations IV*, a different game.

## 3. Community implementations

| Repo | What it is | State (2026-10-31) |
|---|---|---|
| `RianGoossens/reshade-dlss5-linux` | ReShade add-on + `nvngx.dll_nrfwd.dll` forwarder, native DLSS hook or standalone | v0.10.0 (2026-10-19), GPL-3.0, 2 open issues |
| `Endebert/reshade-dlss5-linux` | Fork of the same | pushed 2026-10-28 |
| `24high/DLSS5-Reshade-Linux-Steam` | Steam/Proton installer, `nr` and `dlaa` modes | last push 2026-09-09 |
| `pantsoftime/dlss5-linux` | Route picker, installer, log verifier, six routes | 2026-09-26, 14 stars |
| `Dagherbou/OptiScaler_DLSSNR` | OptiScaler fork with neural rendering for D3D12 | 853 stars, last push 2026-09-04 |
| `optiscaler/OptiScaler` | Upstream | pushed 2026-10-31 |

Confirmed working **in other games** (not evidence for this one): High on
Life, Control, Elden Ring (native D3D12 DLSS); Dead Island 2, Dark Souls
Remastered, Detroit (standalone); 007 First Light and Final Fantasy VII Rebirth
through OptiScaler. Reported failures: RTX 5060 Ti on driver 610.57.04 hits
Xid 31 / Xid 69 right after the first evaluate in 007 First Light
(RianGoossens #1, pantsoftime #2). That card is a different die from the RTX
5070 here.

## 4. Technical compatibility assessment

Measured on the installed game (`SWGR-Win64-Shipping.exe`):

| Question | Result |
|---|---|
| 64-bit? | **Yes.** PE machine `0x8664`. |
| Direct3D 12? | **Very likely.** 246 `D3D12` strings. Not run-time confirmed. |
| Native DLSS through NGX? | **Plausible.** 77 `NGX` and 312 `DLSS` strings, no `sl.interposer` / Streamline, and no `nvngx_dlss.dll` in the game folder. Whether it creates an SR session is untested; `ReShade.log` will say. |
| Free `dxgi.dll` slot? | `Griffin/Binaries/Win64/` already holds `dwmapi.dll` and a `ue4ss/` folder (a proxy mod loader). I didn't check where those came from. |
| Anti-cheat | No EasyAntiCheat or BattlEye strings. Denuvo not visible in strings either, which proves nothing for a protected binary. The folder has PlayFab Party and Epic Online Services. |
| NGX feature 18 on this driver? | **Unverified.** Driver is 615.71.09. The only check is the add-on's own log line (`nr-fwd: snippet init => 0x1`). |
| Proton config | Steam runs `greenboost-proton`; `proton-gb-nr` is also installed. Neither is Valve Proton Experimental, so the Valve launch option below needs `PROTON_ENABLE_NVAPI`, which GE-style builds ignore. |

DLSS 4.5 does not imply DLSS 5. The add-on only works if the game's own DLSS
session exists, or if standalone mode can supply depth and motion vectors.

## 5. Recommended installation recipe

1. Confirm the game runs on Proton with DLSS on and no add-on (baseline).
2. **Route 1, through the Suite (preferred).** Open *Games → This Game → DLSS 5
   (Neural Rendering)*, pick the DLSS5 folder, enable. This installs the
   Suite's OptiScaler fork proxy and the model.
3. **Route 2, ReShade add-on (Recipe A).** Only if Route 1 doesn't create an NR
   session. Native hook first. Standalone (Recipe B) only if the log shows the
   game never starts an NGX SR session.

Recipe B is unsuitable until A fails: Unreal Engine uses reversed depth
(`RESHADE_DEPTH_INPUT_IS_REVERSED=1`, as in Dead Island 2), motion vectors
come from iMMERSE Launchpad's estimate rather than the engine, and a racing
game at speed is the worst case for approximated motion.

## 6. Exact Ubuntu terminal commands (Route 2 files)

```bash
mkdir -p ~/dlss5-gr && cd ~/dlss5-gr
gh release download v0.10.0 -R RianGoossens/reshade-dlss5-linux \
  -p dlssnr-linux.addon64 -p nvngx.dll_nrfwd.dll
sha256sum -c <<'EOF'
5bacf247e5236164dc67d97e6fed89aa0a073978d629861a896b23259a648f21  dlssnr-linux.addon64
8b9c518b13da25311b9982114961acbb1f2b98828843740817073a6bcae809dd  nvngx.dll_nrfwd.dll
EOF
```

ReShade itself (with add-on support) is not covered here: download it only
from reshade.me and verify what the installer reports.

## 7. Steam launch options

Valve Proton builds:

```
PROTON_ENABLE_NVAPI=1 WINEDLLOVERRIDES="dxgi=n,b" %command%
```

GE-Proton / proton-cachyos use `PROTON_FORCE_NVAPI=1` instead. Don't mix them.
Through the Suite, set these in the per-game launch wrapper, never by hand
over the Suite's own variables.

## 8. File locations

All beside `Griffin/Binaries/Win64/SWGR-Win64-Shipping.exe`:

`dxgi.dll` (ReShade), `ReShade.ini`, `dlssnr-linux.addon64`,
`nvngx.dll_nrfwd.dll`, `nvngx_dlssnr.dll`.

The add-on author warns the model version must match: the add-on is tested
with **310.8.0**, and a mismatched model "reported Success on every evaluate
and then crashed the game minutes into gameplay".

## 9. ReShade configuration

Open the overlay with Home, then *Add-ons → DLSSNR Linux*. F10 toggles the
pass for A/B. Start with Encoding Auto, Model resolution Full, Pass Count 1.
For this engine try Model resolution Render first if the frame time is too high.

## 10. Model download and hash verification

There is no trusted download. NVIDIA doesn't publish `nvngx_dlssnr.dll`, and
the NGX update server has no `dlssnr` feature (probed 2026-10-31).

| SHA-256 prefix | What it is (per pantsoftime/dlss5-linux) |
|---|---|
| `e16bcf15...` | NVIDIA-signed 310.8.0 for RTX 50. **The only build the Suite accepts.** |
| `e67dee20...` | Cross-generation build for RTX 20/30/40 |
| `8270b350...` | Patched build carrying NVIDIA's signature over different content |
| `dcc0dc24...` | Recol mirror copy. Same size as `e16bcf15...`, matches none of the above. **Rejected.** |

A valid Authenticode signature isn't enough, because the patched build has one.
Compare the full hash:

```bash
sha256sum ~/.local/share/greenboost/dlss5/nvngx_dlssnr.dll
# expect e16bcf15e16e13f527491cdf7845b2fe6521a738d8f7c9c721866a8496e1fc8e
```

## 11. Testing and diagnostics

```bash
G=~/.local/share/Steam/steamapps/common/"STAR WARS Galactic Racer"/Griffin/Binaries/Win64
grep -E "Registered add-on|nr-fwd:" "$G/ReShade.log" | tail
```

* `Registered add-on "DLSSNR Linux"`: ReShade's dxgi loaded.
* `nr-fwd: snippet init => 0x1 (Success)`: NVAPI/NGX is exposed.
* `nr-fwd: EvaluateFeature #N => 0x1`: the pass is running.
* No `CreateFeature` at all: the game doesn't use an NGX SR session; go to
  Recipe B or stop.
* `nvidia-smi` and `dmesg | grep -i Xid`: an Xid 31 or 69 right after the first
  evaluate is the failure reported on 5060 Ti. Stop and roll back.

## 12. Performance tuning

No Galactic Racer numbers exist. Reference points from other games: the
standalone bridge measured 0.66 ms/frame in Lord of the Rings Online; NR took
4.9 to 6.3 ms at 3840x1440 in FF7 Rebirth; about 6 ms at 5120x1440 in 007 First
Light. Each extra pass costs a full evaluation and its own VRAM. On a 12 GB
RTX 5070, keep Pass Count at 1 and watch `nvidia-smi` for VRAM over 10 GB.
Latency, ghosting and speed perception are untested here: judge them with F10.

## 13. Rollback and recovery

Delete from the Win64 folder: `dxgi.dll`, `ReShade.ini`, `ReShade.log`,
`dlssnr-linux.addon64`, `nvngx.dll_nrfwd.dll`, `nvngx_dlssnr.dll`; remove the
launch option; run Steam's *Verify integrity of game files*. Through the Suite,
*DLSS 5 → Restore* does this and puts back anything it replaced.

## 14. Multiplayer and anti-cheat risks

Denuvo Anti-Tamper is reported, and the game uses PlayFab Party and Epic Online
Services for online play. I found no report on whether a proxy `dxgi.dll` trips
either. Treat online races as **at risk**, test offline or in a private lobby
first, and don't use this on an account you can't afford to lose. The add-on
authors give the same warning for any online game.

## 15. Final verdict

**Experimentally possible, unconfirmed.** Technically plausible on the facts
above (64-bit D3D12, NGX-based DLSS, RTX 50 supported by the model, no
Streamline). Unproven: an NGX SR session in this game, NGX feature 18 on driver
615.71.09, behaviour under Denuvo. Don't call it working until `ReShade.log`
or the Suite's verifier shows `EvaluateFeature ... Success` for this game.

Sources: [RianGoossens/reshade-dlss5-linux](https://github.com/RianGoossens/reshade-dlss5-linux),
[its tested-games wiki](https://github.com/RianGoossens/reshade-dlss5-linux/wiki/Tested-Games),
[NapXDD launch options](https://github.com/NapXDD/addon-dlssnr-linux/wiki/Launch-Options),
[pantsoftime/dlss5-linux](https://github.com/pantsoftime/dlss5-linux),
[24high/DLSS5-Reshade-Linux-Steam](https://github.com/24high/DLSS5-Reshade-Linux-Steam),
[Dagherbou/OptiScaler_DLSSNR](https://github.com/Dagherbou/OptiScaler_DLSSNR),
[GamingOnLinux review](https://www.gamingonlinux.com/2026/10/star-wars-galactic-racer-is-out-now-running-quite-nicely-on-steamos-linux/),
[GamingOnLinux release article](https://www.gamingonlinux.com/2026/04/star-wars-galactic-racer-releases-october-6/).
