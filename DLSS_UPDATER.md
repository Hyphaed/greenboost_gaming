# DLSS Updater , how it works

The Gaming Suite's **DLSS panel** scans your installed Steam games for
NVIDIA upscaler DLLs and, on your explicit click, replaces them with
newer versions. This document explains exactly where the new DLLs come
from, why we chose this approach, and what guarantees you do (and do
not) get.

**Short version:**

- **Streamline DLLs** (`sl.dlss.dll`, `sl.dlss_g.dll`) are downloaded
  from **NVIDIA's official Streamline repository on GitHub**
  (`github.com/NVIDIAGameWorks/Streamline`). No community mirror is
  involved , these come straight from NVIDIA's own infrastructure.
- **DLSS DLLs** (`nvngx_dlss.dll`, `nvngx_dlssg.dll`, `nvngx_dlssd.dll`)
  are resolved at update time, in order: a local copy of NVIDIA's DLSS
  SDK if you have one, then **`github.com/NVIDIA/DLSS`**, then an
  optional in-tree mirror, then a community mirror. Details below.

**Nothing is shipped in this repository.** No `.dll` file is tracked in
git. Everything is fetched on demand and cached under
`~/.local/share/greenboost-gaming/libraries/`.

NVIDIA-only by design. The Suite does **not** offer to update AMD
FidelityFX (FSR) or Intel XeSS DLLs , those are out of scope.

---

## What the Suite manages

| Family | DLL filename | Source |
|---|---|---|
| DLSS Super Resolution    | `nvngx_dlss.dll`   | local SDK → **NVIDIA official GitHub** → mirror |
| DLSS Frame Generation    | `nvngx_dlssg.dll`  | local SDK → **NVIDIA official GitHub** → mirror |
| DLSS Ray Reconstruction  | `nvngx_dlssd.dll`  | local SDK → **NVIDIA official GitHub** → mirror |
| Streamline DLSS SR       | `sl.dlss.dll`      | **NVIDIA official GitHub** (always) |
| Streamline DLSS FG       | `sl.dlss_g.dll`    | **NVIDIA official GitHub** (always) |
| Streamline DLSS RR       | `sl.dlss_d.dll`    | **NVIDIA official GitHub** (always) |
| Streamline Reflex        | `sl.reflex.dll`    | **NVIDIA official GitHub** (always) |
| Streamline Loader        | `sl.common.dll`    | **NVIDIA official GitHub** (always) |
| Streamline Interposer    | `sl.interposer.dll`| **NVIDIA official GitHub** (always) |
| Streamline NIS           | `sl.nis.dll`       | **NVIDIA official GitHub** (always) |
| Streamline PCL           | `sl.pcl.dll`       | **NVIDIA official GitHub** (always) |

---

## Streamline DLLs , from NVIDIA's official repo

`sl.dlss.dll` and `sl.dlss_g.dll` are fetched at update time from:

> **`github.com/NVIDIAGameWorks/Streamline`**

This is NVIDIA's own GitHub organization (NVIDIAGameWorks). Releases
are public, the GitHub Releases API works without authentication, and
each tagged release publishes the Streamline DLLs as build artifacts.

How the Suite uses it:

1. `GET https://api.github.com/repos/NVIDIAGameWorks/Streamline/releases/latest`
2. Find the asset whose filename matches our DLL (either a direct
   `.dll` upload or extract from a `streamline_sdk_*.zip`).
3. Verify the downloaded bytes are a real PE before installing.

There is no community mirror in this path. Every byte the Suite
installs as `sl.dlss.dll` came from NVIDIA's GitHub.

**Rate limiting:** GitHub's unauthenticated API gives you 60 requests
per hour per IP. That's plenty for one user clicking "Update" but it
means our app deliberately does not poll on its own , manifest checks
happen only when you click *Refresh manifest*.

---

## DLSS DLLs (`nvngx_*.dll`) , resolved in priority order

The bare `nvngx_dlss.dll` / `nvngx_dlssg.dll` / `nvngx_dlssd.dll` files
are harder to source than the Streamline ones. NVIDIA distributes them
through the **DLSS SDK** at `developer.nvidia.com/dlss`, which requires
a Developer Program login and EULA acceptance , that can't be automated
inside an app. They are also published in the
**[`NVIDIA/DLSS`](https://github.com/NVIDIA/DLSS)** GitHub repository,
which *is* publicly reachable.

So `_read_bundled_dll()` in `gb_gaming/dlss_updater.py` walks four
sources and takes the first that answers:

1. **A local DLSS SDK**, if you have one , read from
   `NVIDIA_DLSS_SDK_DIR/lib/Windows_x86_64/rel/`. Override the root with
   the `NVIDIA_DLSS_SDK_DIR` environment variable. The SDK is its own
   provenance, so no hash check is applied.
2. **`github.com/NVIDIA/DLSS`** at the latest tag, via
   `raw.githubusercontent.com`. Pin a tag with `NVIDIA_DLSS_GH_TAG`, or
   skip this step entirely with `GREENBOOST_DLSS_DISABLE_GITHUB=1`.
3. **An in-tree `dlls/` mirror**, if one exists. This directory is *not*
   part of the repository , it's an optional local staging area for
   people who want to vet DLLs by hand or work fully offline. When
   present, each file is verified against `dlls/manifest.json`'s SHA-256
   and **refused** on mismatch.
4. **A community mirror**, as an automatic fallback when the steps above
   come up empty.

Whichever source wins, the install itself is the same:

1. Verify the bytes parse as a real PE.
2. Back up the in-game DLL to `<game>/<DLL>.dll.bak.<unix-ts>`.
3. Atomically replace the in-game DLL via `os.replace`.
4. Record version + SHA-256 + source in the runtime manifest, so the
   About view can show you where each cached DLL came from.

### Building your own offline bundle

If you want step 3 , a vetted, offline, hash-checked local bundle ,
create `dlls/` yourself:

1. Download the DLSS SDK from
   <https://developer.nvidia.com/dlss-getting-started>.
2. Extract the DLLs from `lib/Windows_x86_64/rel/` into `dlls/`.
3. Compute SHA-256 for each and write `dlls/manifest.json` as
   `{dll_name → {version, sha256, source}}`.
4. Set `GREENBOOST_DLSS_DISABLE_GITHUB=1` if you want the network path
   skipped entirely.

`dlls/` is gitignored , your bundle stays yours and never gets committed.

### Community mirrors

If you'd rather pull from a vetted third party, the Preferences page
lets you point `nvngx_source` at a **community mirror**:

| Name | URL | Maintained by |
|---|---|---|
| `recol` | `github.com/Recol/DLSS-Updater-DLLs` | The DLSS-Updater author |
| `custom` | Any base URL you provide | You |

Configuration lives in:

```
/etc/greenboost-gaming/sources.conf            # system-wide
~/.config/greenboost-gaming/sources.conf       # per-user (wins)
```

Format:

```ini
# one of: bundled (default), recol, custom
nvngx_source = bundled

# only used when nvngx_source = custom
nvngx_custom_url = https://my-internal-mirror.example/dlss
```

Two behaviours worth knowing about:

- **A hash mismatch is fatal, never routed around.** If a local bundle
  DLL fails its `dlls/manifest.json` check, the *Update* button shows an
  error and no DLL is installed. A tampered file never reaches a game.
- **A *missing* source does fall through to the next one.** No local
  SDK, no GitHub reach, no local bundle → the Suite tries the community
  mirror rather than dead-ending. The runtime manifest always records
  which source actually supplied the bytes, and the About view shows it.

---

## The full flow, step by step

When you click **Update** on an out-of-date DLL in the DLSS panel:

1. **Source resolution.**
   - Streamline DLL → `NVIDIAGameWorks/Streamline` GitHub.
   - nvngx DLL → local SDK, then `NVIDIA/DLSS` GitHub, then a local
     `dlls/` bundle, then the configured mirror.
2. **Fetch.** HTTP GET (NVIDIA GitHub / community mirror) or local
   disk read (SDK / bundle).
3. **Verify.**
   - Local bundle: SHA-256 against `dlls/manifest.json`.
   - Network: file size > 1 KB (404 sanity check).
   - All sources: PE FileVersion parse must succeed.
4. **Version-compare.** New version must be >= current in-game version.
   Equal is allowed so users can repair a corrupted DLL.
5. **Backup.** Current in-game DLL copied to `<DLL>.dll.bak.<unix-ts>`.
6. **Atomic replace.** `os.replace` swaps the new DLL in. Same
   filesystem, so the game's Proton prefix never sees a partial file.

If any step between 3 and 5 fails, the in-game DLL is untouched.

---

## What the Suite deliberately does NOT do

- **No auto-update.** The Suite never replaces a DLL without an
  explicit click.
- **No manifest poll on startup.** Manifest refresh happens only when
  you click *Refresh manifest* in the panel.
- **No silent overwrite.** Backups are always created before
  replacement.
- **No silent tamper-through.** A hash mismatch on a local bundle DLL
  aborts the update; it never falls through to another source to work
  around a bad file.
- **No redistribution.** No NVIDIA DLL is tracked in this repository or
  shipped in the install package. Everything is fetched at runtime from
  NVIDIA's own channels.
- **No telemetry.** We do not phone home with which game / which DLL
  version was updated.

---

## Attribution

The general idea (scan Steam libraries, compare PE FileVersion,
backup-then-replace) is adapted from the **DLSS-Updater** project:

> **DLSS-Updater** by [Recol](https://github.com/Recol)
> [github.com/Recol/DLSS-Updater](https://github.com/Recol/DLSS-Updater)
> Licensed GPLv3.

Our implementation is a pure-stdlib re-write (no `aiohttp`, no
`msgspec`, no `pefile`). The sourcing model differs from DLSS-Updater
upstream: they pull everything from their own community repo by
default; we prefer NVIDIA's own GitHub organizations
(`NVIDIAGameWorks/Streamline` and `NVIDIA/DLSS`) and treat the
community mirror as a fallback.

---

## Disclaimer

GreenBoost Gaming Suite is an independent open-source project and is
not affiliated with, endorsed by, or sponsored by NVIDIA Corporation.
NVIDIA, DLSS, GeForce, RTX, and Streamline are trademarks of NVIDIA
Corporation.

DLSS DLLs are NVIDIA-authored binaries. This project does **not**
redistribute them: none are tracked in the repository or shipped in the
install package. The Suite fetches them at runtime, on your explicit
click, from NVIDIA's own publicly-available channels (the `NVIDIA/DLSS`
and `NVIDIAGameWorks/Streamline` GitHub repositories, or a DLSS SDK you
installed yourself). They remain subject to the NVIDIA DLSS SDK EULA.
Their license is **not** GPL v2 , they are cached alongside this GPL v2
project but are not derivative works of ours.
