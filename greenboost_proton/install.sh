#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
#
# GreenBoost Proton , install into Steam's compatibilitytools.d directory.
#
# What this does:
#   1. Detects which Steam root the user has (native, Flatpak, or both).
#   2. Copies the wrapper + manifests into compatibilitytools.d/greenboost-proton/
#      AND compatibilitytools.d/greenboost-proton-experimental/.
#   3. Pre-seeds the `channel` sidecar so the two installs route to:
#         greenboost-proton              → channel=stable
#         greenboost-proton-experimental → channel=experimental
#   4. Reminds the user to restart Steam so the tools appear in the
#      per-game compatibility picker.
#
# Idempotent: re-running overwrites the existing install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Candidate Steam roots , order matters; we install into every one we find.
CANDIDATE_ROOTS=(
    "$HOME/.local/share/Steam"
    "$HOME/.steam/steam"
    "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"     # Flatpak
    "$HOME/.steam/root"
)

# PR-UUU: support `--uninstall` so the Gaming Suite can offer an
# Uninstall button symmetric with Install.
MODE="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE="uninstall" ;;
        -h|--help)
            echo "Usage: $0 [--uninstall]"
            echo "  install (default): install greenboost-proton + experimental"
            echo "                     into every detected Steam root"
            echo "  --uninstall:       remove both variants from every Steam root"
            exit 0 ;;
        *) echo "Unknown argument: $arg (try --help)" >&2; exit 2 ;;
    esac
done

if [[ "$MODE" == "uninstall" ]]; then
    found_any=0
    for ROOT in "${CANDIDATE_ROOTS[@]}"; do
        [[ -d "$ROOT" ]] || continue
        for name in greenboost-proton greenboost-proton-experimental; do
            target="$ROOT/compatibilitytools.d/$name"
            if [[ -d "$target" ]]; then
                echo "Removing $target ..."
                rm -rf "$target"
                found_any=1
            fi
        done
    done
    if [[ $found_any -eq 0 ]]; then
        echo "GreenBoost Proton was not installed in any Steam root."
    else
        echo "Done.  Restart Steam to refresh the compatibility-tool list."
    fi
    exit 0
fi

PAYLOAD=( proton compatibilitytool.vdf toolmanifest.vdf version channel )

# Confirm every payload file is present (fail early, not mid-install).
for f in "${PAYLOAD[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] \
        || { echo "FATAL: missing payload file '$f'" >&2; exit 1; }
done

# PR-GGGG: stage NVIDIA Image Scaling (NIS) shader source into the
# GreenBoost runtime cache.  Today this only copies the GLSL / HLSL /
# header files into ~/.local/share/greenboost/nis/ so a future Vulkan-
# layer iteration can compile + dispatch NIS as a swapchain post-process
# without re-vendoring the SDK at runtime.  No effect at runtime yet.
GB_NIS_SRC="${GB_NIS_SRC:-$HOME/Dev/greenboost_all/NVIDIAImageScaling/NIS}"
GB_NIS_DST="$HOME/.local/share/greenboost/nis"
if [[ -d "$GB_NIS_SRC" && -f "$GB_NIS_SRC/NIS_Main.glsl" ]]; then
    mkdir -p "$GB_NIS_DST"
    cp -f "$GB_NIS_SRC"/NIS_Main.glsl "$GB_NIS_DST/" 2>/dev/null || true
    cp -f "$GB_NIS_SRC"/NIS_Main.hlsl "$GB_NIS_DST/" 2>/dev/null || true
    cp -f "$GB_NIS_SRC"/NIS_Scaler.h  "$GB_NIS_DST/" 2>/dev/null || true
    cp -f "$GB_NIS_SRC"/NIS_Config.h  "$GB_NIS_DST/" 2>/dev/null || true
    echo "Staged NVIDIA Image Scaling shaders → $GB_NIS_DST"

    # PR-GGGG: pre-compile NIS sharpen + upscale variants to SPIR-V so the
    # Vulkan layer can mmap them directly , no runtime glslc dependency.
    # Use glslc (preferred , better SPIR-V optimisation) or fall back to
    # glslangValidator.  NVIDIA-recommended block geometry for sharpen
    # mode is 32×32×256 (NIS_Config.h table); for upscale 32×24×128.
    SPV_COMPILER=""
    if command -v glslc >/dev/null; then
        SPV_COMPILER=glslc
    elif command -v glslangValidator >/dev/null; then
        SPV_COMPILER=glslangValidator
    fi
    if [[ -n "$SPV_COMPILER" ]]; then
        _nis_compile() {
            local variant="$1" defines="$2" out="$3"
            if [[ "$SPV_COMPILER" == "glslc" ]]; then
                glslc -fshader-stage=compute -O \
                      $defines \
                      "$GB_NIS_DST/NIS_Main.glsl" \
                      -I "$GB_NIS_DST" \
                      -o "$GB_NIS_DST/$out" 2>/dev/null
            else
                # glslangValidator: -V (Vulkan), -S comp (compute stage),
                # define flags via -D, includes via --include-dir.
                glslangValidator -V -S comp \
                      $defines \
                      --include-dir "$GB_NIS_DST" \
                      "$GB_NIS_DST/NIS_Main.glsl" \
                      -o "$GB_NIS_DST/$out" >/dev/null 2>&1
            fi
            if [[ -s "$GB_NIS_DST/$out" ]]; then
                echo "  → $variant: $GB_NIS_DST/$out ($(stat -c %s "$GB_NIS_DST/$out") bytes)"
                return 0
            fi
            rm -f "$GB_NIS_DST/$out"
            return 1
        }
        _nis_compile "sharpen-only" \
            "-DNIS_SCALER=0 -DNIS_HDR_MODE=0 -DNIS_BLOCK_WIDTH=32 -DNIS_BLOCK_HEIGHT=32 -DNIS_THREAD_GROUP_SIZE=256" \
            "nis_sharpen.spv" || echo "  → sharpen variant compile failed (non-fatal)"
        _nis_compile "upscale+sharpen" \
            "-DNIS_SCALER=1 -DNIS_HDR_MODE=0 -DNIS_BLOCK_WIDTH=32 -DNIS_BLOCK_HEIGHT=24 -DNIS_THREAD_GROUP_SIZE=128" \
            "nis_upscale.spv" || echo "  → upscale variant compile failed (non-fatal)"
    else
        echo "Skipping NIS SPIR-V compile , neither glslc nor glslangValidator on PATH"
    fi
fi

# PR-GGGG: download dxvk-gplasync into a shared cache so the wrapper can
# stage its DLLs into each Wine prefix.  Pinned to a known-good release;
# can be overridden by exporting GB_GPLASYNC_VERSION before running.
#
# Skipped if --no-gplasync is passed, the cache already has the version,
# or curl/unzip is missing (the wrapper falls back to stock DXVK).
GB_GPLASYNC_VERSION="${GB_GPLASYNC_VERSION:-2.4-1}"
GB_GPLASYNC_CACHE="$HOME/.local/share/greenboost/dxvk-gplasync/${GB_GPLASYNC_VERSION}"
GB_GPLASYNC_SKIP=0
for arg in "$@"; do
    [[ "$arg" == "--no-gplasync" ]] && GB_GPLASYNC_SKIP=1
done

if [[ "$MODE" == "install" && $GB_GPLASYNC_SKIP -eq 0 ]]; then
    if command -v curl >/dev/null && command -v tar >/dev/null; then
        # Check both arch dirs, not just x64/d3d11.dll , a `cp -r` that
        # copied x64/ but failed partway into x32/ (disk full, permission
        # change mid-copy) previously left x32/ empty forever: the single-
        # file check saw x64/d3d11.dll present and called the cache good on
        # every subsequent install run, with no re-fetch and no warning.
        if [[ ! -f "$GB_GPLASYNC_CACHE/x64/d3d11.dll" || \
              ! -f "$GB_GPLASYNC_CACHE/x32/d3d11.dll" ]]; then
            mkdir -p "$GB_GPLASYNC_CACHE"
            tmp="$(mktemp -d)"
            url="https://gitlab.com/Ph42oN/dxvk-gplasync/-/raw/main/releases/dxvk-gplasync-v${GB_GPLASYNC_VERSION}.tar.gz"
            echo "Fetching dxvk-gplasync ${GB_GPLASYNC_VERSION} into ${GB_GPLASYNC_CACHE} ..."
            if curl --fail --silent --show-error --location \
                    --output "$tmp/gplasync.tar.gz" "$url"; then
                if tar -xzf "$tmp/gplasync.tar.gz" -C "$tmp"; then
                    # Release lays out as dxvk-gplasync-vX.Y-Z/{x32,x64}/*.dll
                    inner="$(find "$tmp" -maxdepth 2 -type d -name "x64" \
                              -printf '%h\n' | head -n1)"
                    if [[ -n "$inner" && -d "$inner/x64" ]]; then
                        cp -r "$inner/x64" "$inner/x32" "$GB_GPLASYNC_CACHE/"
                        echo "Installed dxvk-gplasync DLLs at $GB_GPLASYNC_CACHE/"
                    else
                        echo "WARNING: dxvk-gplasync archive layout unexpected , skipping." >&2
                    fi
                else
                    echo "WARNING: failed to extract dxvk-gplasync archive , skipping." >&2
                fi
            else
                echo "WARNING: could not download dxvk-gplasync from $url" >&2
                echo "         The wrapper will fall back to stock DXVK; you can" >&2
                echo "         retry with: GB_GPLASYNC_VERSION=$GB_GPLASYNC_VERSION $0" >&2
            fi
            rm -rf "$tmp"
        else
            echo "dxvk-gplasync ${GB_GPLASYNC_VERSION} already cached at $GB_GPLASYNC_CACHE"
        fi
        # Drop a "default version" symlink the wrapper reads when the env
        # var isn't set explicitly , keeps the runtime path stable across
        # versions.
        ln -sfn "$GB_GPLASYNC_CACHE" \
            "$HOME/.local/share/greenboost/dxvk-gplasync/current"
    else
        echo "Skipping dxvk-gplasync download , curl/tar not on PATH." >&2
    fi
fi

found_any=0
for ROOT in "${CANDIDATE_ROOTS[@]}"; do
    [[ -d "$ROOT" ]] || continue
    found_any=1
    COMPAT_DIR="$ROOT/compatibilitytools.d"
    mkdir -p "$COMPAT_DIR"

    for variant in stable experimental; do
        if [[ "$variant" == "stable" ]]; then
            name="greenboost-proton"
            display="GreenBoost Proton"
        else
            name="greenboost-proton-experimental"
            display="GreenBoost Proton Experimental"
        fi
        target="$COMPAT_DIR/$name"
        echo "Installing $display into $target ..."
        mkdir -p "$target"
        for f in "${PAYLOAD[@]}"; do
            install -m "$([[ "$f" == proton ]] && echo 0755 || echo 0644)" \
                    "$SCRIPT_DIR/$f" "$target/$f"
        done

        # Per-variant edits , VDF rename + channel selection.
        sed -i "s|greenboost-proton|$name|g; s|GreenBoost Proton|$display|g" \
            "$target/compatibilitytool.vdf"
        echo "$variant" > "$target/channel"
    done
done

if [[ $found_any -eq 0 ]]; then
    cat >&2 <<EOF
No Steam installation detected.

Checked:
  ${CANDIDATE_ROOTS[*]}

Install Steam first, run it at least once so it creates the directory
tree, then re-run this installer.
EOF
    exit 2
fi

cat <<EOF

Done.  Restart Steam, then in any game's Properties → Compatibility,
pick "GreenBoost Proton" or "GreenBoost Proton Experimental".

The wrapper exports GREENBOOST_VULKAN=1 before launching upstream Proton,
which is what activates the GreenBoost Vulkan layer for that game.

EOF
