# GreenBoost source-build patches

`build_from_source.sh` applies every `*.patch` in this directory after
submodule init and before `make dist`.  The patches use git's `am`/`apply`
format , generate them with:

```bash
cd "$GB_PROTON_SRC"   # e.g. ~/Dev/greenboost_all/Proton
# … make your changes …
git format-patch -1 -o greenboost_gaming/greenboost_proton/patches/source/
```

Intended patch series (numbered for order):

| # | Patch | Effect |
|---|---|---|
| 0001 | `proton-set-greenboost-defaults.patch` | Inject the GreenBoost env-var block (Reflex, VRR, fsync, GPL caches) into the upstream `proton` Python script's `init_session`. |
| 0002 | `vkd3d-proton-gpl-default.patch` | Force `pipeline_library_no_serialize_spirv` + `pipeline_library_app_cache` on by default. |
| 0003 | `dxvk-gplasync-vendored.patch` | (Only when `dxvk/` submodule URL has been swapped already , usually a no-op.) |
| 0004 | `bundle-greenboost-vulkan-layer.patch` | Copy `libVkLayer_greenboost.so` + manifest into `files/lib*/vulkan/implicit_layer.d/` so the layer ships with this Proton build (no separate install step). |

Don't add a patch unless you've reproduced the upstream behaviour without
it first , extra patches mean ongoing rebase cost on every Proton release.
