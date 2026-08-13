#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
#
# GreenBoost Proton , full source build from upstream Proton tree.
#
# Produces a self-contained `files/` tree (Wine + dxvk-gplasync + vkd3d-proton
# + dxvk-nvapi) that the wrapper at $SCRIPT_DIR/proton can delegate to,
# replacing the runtime-symlink approach for users who want a single
# pinned, reproducible Proton image.
#
# What this does (in order):
#   1. Confirms $GB_PROTON_SRC (default: ~/Dev/greenboost_all/Proton) is a Proton checkout.
#   2. `git submodule update --init --recursive` (~10 GB on first run).
#   3. Swaps the DXVK submodule URL to dxvk-gplasync's mirror (so the
#      built Proton ships gplasync DLLs in files/lib*/wine/dxvk/).
#   4. Pulls the Proton SDK Docker image (~3 GB) via `make protonsdk`.
#   5. Applies the GreenBoost patch series in patches/source/.
#   6. Runs `make dist` inside the SDK container (~2–6 h cold; ~30 min warm).
#   7. Installs the built tree as a Steam compat tool named
#      "greenboost-proton-source-built".
#
# Run it once.  Subsequent invocations skip the steps that already finished.
# Use `--clean` to nuke build/ and rebuild from scratch.
#
# Estimated cost (cold): 4–8 h wall-clock, ~25 GB disk, ~5 GB network.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTON_SRC="${GB_PROTON_SRC:-$HOME/Dev/greenboost_all/Proton}"
PATCH_DIR="$SCRIPT_DIR/patches/source"
BUILD_NAME="${GB_BUILD_NAME:-greenboost-source}"
BUILD_DIR="$PROTON_SRC/build/build-$BUILD_NAME"
DIST_NAME="greenboost-proton-source-built"

CLEAN=0
SKIP_DOCKER=0
for arg in "$@"; do
    case "$arg" in
        --clean)        CLEAN=1 ;;
        --skip-docker)  SKIP_DOCKER=1 ;;   # for re-using an existing SDK
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed -e 's/^# \{0,1\}//' -e '/^set -euo/d'
            exit 0 ;;
        *) echo "Unknown argument: $arg (try --help)" >&2; exit 2 ;;
    esac
done

require() {
    command -v "$1" >/dev/null || {
        echo "FATAL: '$1' not on PATH , install it first." >&2
        exit 1
    }
}

require git
require make
[[ $SKIP_DOCKER -eq 1 ]] || require docker

echo "── 1. Verify Proton source tree at $PROTON_SRC"
[[ -f "$PROTON_SRC/Makefile" && -f "$PROTON_SRC/.gitmodules" ]] || {
    echo "FATAL: $PROTON_SRC is not a Proton checkout." >&2
    echo "       Clone https://github.com/ValveSoftware/Proton into that path first." >&2
    exit 1
}

if [[ $CLEAN -eq 1 ]]; then
    echo "── --clean: removing $BUILD_DIR"
    rm -rf "$BUILD_DIR"
fi

echo "── 2. Initialise submodules (this is the slow first-run step)"
( cd "$PROTON_SRC" && git submodule update --init --recursive --jobs 4 )

echo "── 3. Swap DXVK submodule for dxvk-gplasync"
# We pin gplasync's branch tracking the same DXVK base as upstream Proton's
# checked-out commit.  If the branch doesn't exist (gplasync hasn't rebased
# yet for this DXVK rev), fall back to gplasync's `main` and accept the small
# DXVK delta.
DXVK_HEAD="$(git -C "$PROTON_SRC/dxvk" rev-parse HEAD 2>/dev/null || true)"
GPLASYNC_URL="https://gitlab.com/Ph42oN/dxvk-gplasync.git"
GPLASYNC_BRANCH="${GB_GPLASYNC_BRANCH:-main}"

if [[ -n "$DXVK_HEAD" ]]; then
    pushd "$PROTON_SRC/dxvk" >/dev/null
    git remote get-url gplasync >/dev/null 2>&1 \
        || git remote add gplasync "$GPLASYNC_URL"
    git fetch gplasync "$GPLASYNC_BRANCH" --depth 50
    if git checkout "gplasync/${GPLASYNC_BRANCH}" 2>/dev/null; then
        echo "   → DXVK submodule now tracking gplasync/${GPLASYNC_BRANCH}"
    else
        echo "   WARNING: gplasync branch unavailable; keeping upstream DXVK." >&2
    fi
    popd >/dev/null
fi

echo "── 4. Pull Proton SDK Docker image (~3 GB)"
if [[ $SKIP_DOCKER -eq 0 ]]; then
    ( cd "$PROTON_SRC" && make protonsdk )
fi

echo "── 5. Apply GreenBoost patch series"
mkdir -p "$PATCH_DIR"
shopt -s nullglob
patches=( "$PATCH_DIR"/*.patch )
if (( ${#patches[@]} == 0 )); then
    echo "   (no patches in $PATCH_DIR , skipping)"
else
    for p in "${patches[@]}"; do
        echo "   → $p"
        ( cd "$PROTON_SRC" && git apply --check "$p" ) && \
            ( cd "$PROTON_SRC" && git apply "$p" ) || {
                echo "   WARNING: $p did not apply; continuing (may already be applied)." >&2
            }
    done
fi

echo "── 6. Configure + build (this is the multi-hour step)"
mkdir -p "$BUILD_DIR"
( cd "$BUILD_DIR" && \
  "$PROTON_SRC/configure.sh" --build-name="$BUILD_NAME" )
( cd "$BUILD_DIR" && make -j"$(nproc)" dist )

echo "── 7. Install as Steam compat tool"
DIST_TARBALL="$BUILD_DIR/${BUILD_NAME}.tar.xz"
[[ -f "$DIST_TARBALL" ]] || {
    echo "FATAL: build finished but no tarball at $DIST_TARBALL" >&2
    exit 1
}

CANDIDATE_ROOTS=(
    "$HOME/.local/share/Steam"
    "$HOME/.steam/steam"
    "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"
    "$HOME/.steam/root"
)
installed=0
for ROOT in "${CANDIDATE_ROOTS[@]}"; do
    [[ -d "$ROOT" ]] || continue
    target="$ROOT/compatibilitytools.d/$DIST_NAME"
    rm -rf "$target"
    mkdir -p "$target"
    tar -xJf "$DIST_TARBALL" -C "$target" --strip-components=1
    # Replace the upstream `proton` script with our wrapper-aware variant
    # (renames it to upstream-proton so the wrapper can still delegate).
    if [[ -f "$target/proton" ]]; then
        mv "$target/proton" "$target/upstream-proton"
        install -m 0755 "$SCRIPT_DIR/proton" "$target/proton"
    fi
    # Per-variant identification , keep this build distinct from the wrapper
    # installs at greenboost-proton / greenboost-proton-experimental.
    install -m 0644 "$SCRIPT_DIR/compatibilitytool.vdf" "$target/"
    install -m 0644 "$SCRIPT_DIR/toolmanifest.vdf"      "$target/"
    sed -i "s|greenboost-proton|$DIST_NAME|g; \
            s|GreenBoost Proton|GreenBoost Proton (source-built)|g" \
        "$target/compatibilitytool.vdf"
    echo "source-built" > "$target/channel"
    installed=1
done

if [[ $installed -eq 0 ]]; then
    echo "WARNING: no Steam root found; tarball remains at $DIST_TARBALL" >&2
else
    echo "── Done.  Restart Steam , '$DIST_NAME' will appear in the compat picker."
fi
