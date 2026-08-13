# GreenBoost Gaming Suite , Changelog

---

## 2026-08-12 , first public release (Alpha/Beta)

Nothing about the software changed here , this entry records the
publication and the repository hygiene that went with it.

- **Status is Alpha / early Beta**, and the README now says so plainly.
  Everything is built and tested on a single machine (RTX 5070,
  GNOME/Wayland). Other GPUs, other distros, and X11 sessions are
  supported by design but genuinely under-tested.
- **No NVIDIA DLLs in the repository.** The eleven DLSS / Streamline
  DLLs that had been committed under `libraries/` were untracked and
  gitignored, along with `libraries/manifest.json` and
  `libraries/versions/`. That directory was always documented as a
  runtime cache; now it behaves like one. The in-app **Update DLSS**
  fetches from NVIDIA's official GitHub repositories on demand. Repo
  size drops from ~118 MiB to ~20 MiB.
- **`CONTRIBUTING.md`, build instructions, the fix-at-the-source
  rule, the Wayland-first display rules, and the verification recipes.