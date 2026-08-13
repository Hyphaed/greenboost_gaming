# Runtime DLL cache

This is the **Gaming Suite's runtime DLL cache** , the on-disk staging
area where downloaded DLLs live between the moment they're fetched
from a source and the moment they're written into a game's install
directory.

> **In a fresh clone this directory contains nothing but this file.**
> No `.dll` and no `manifest.json` is tracked in git , both are
> gitignored. They appear the first time you click **Update DLSS**, when
> the Suite fetches the libraries from NVIDIA's official GitHub
> repositories. An installed copy of the Suite keeps its live cache at
> `~/.local/share/greenboost-gaming/libraries/`.

Contrast with the sibling **`dlls/`** directory:

| Directory | Purpose | Populated by |
|---|---|---|
| `dlls/`      | **Optional offline bundle.** Not in the repo, not in the install package , you build it yourself if you want vetted offline DLLs. See `DLSS_UPDATER.md`. | You, by hand |
| `libraries/` | **Runtime cache.** Empty in a fresh clone. Filled lazily as you update games. | The Suite itself, on each *Update* click |

## Layout

```
libraries/
├── README.md                ← this file (the only tracked entry)
├── manifest.json            ← gitignored · {dll_name → {version, sha256, fetched_at, source}}
├── nvngx_dlss.dll           ← gitignored · cached after first fetch
├── nvngx_dlssg.dll
├── sl.dlss.dll              ← gitignored · cached from NVIDIA Streamline GitHub
├── versions/                ← gitignored · every version ever fetched
│   └── <dll>/<version>.dll
└── ...
```

`manifest.json` is rewritten after every successful fetch. Each entry
records *which source* the DLL came from so the Status panel can show
the chain of custody for what's currently in the cache:

```json
{
  "dlls": {
    "nvngx_dlss.dll": {
      "version": "310.2.1.0",
      "sha256":  "<64-hex>",
      "fetched_at": 1716308400,
      "source": "bundled"
    },
    "sl.dlss.dll": {
      "version": "2.6.4.0",
      "sha256":  "<64-hex>",
      "fetched_at": 1716309000,
      "source": "github://NVIDIAGameWorks/Streamline@v2.6.4"
    }
  }
}
```

## Update flow

When the user clicks **Update** on a game in the Games tab:

1. The Suite checks `libraries/<dll>.dll` first.
   - If present and version matches the active manifest's `latest`,
     it's a cache hit , no network call.
   - If missing or stale, fetch from the configured source
     (bundled / Recol / custom URL / NVIDIA Streamline GitHub for
     `sl.dlss.dll`), write into `libraries/`, then update
     `manifest.json`.
2. The cached file is copied into the game's install directory.
   A timestamped backup of the previous in-game DLL is created first.
3. The cache survives across game updates , when the user installs
   the same DLL into a different game, no re-fetch happens.

## Cleanup

The cache only grows when new DLL versions appear. A future
**Settings → Storage → Clear DLL cache** action will let users delete
the cache to reclaim disk space. For now, manual cleanup is
`rm libraries/*.dll libraries/manifest.json` , the Suite will
re-populate on the next *Update* click.

## Disclaimer

DLLs cached here are NVIDIA-authored binaries fetched at runtime from
NVIDIA's own distribution channels. They are not derivative works of
this GPL v2 project and are not redistributed by it. See
`DLSS_UPDATER.md` for the full chain of custody.
