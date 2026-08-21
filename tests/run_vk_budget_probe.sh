#!/usr/bin/env bash
# Live check of the heapBudget[] headroom reserve (ps5enhance T1).
#
# IMPORTANT , what this can and cannot do.
#
# greenboost_vulkan_layer.c is an IMPLICIT Vulkan layer and its manifest has
# no `disable_environment` key, so the loader refuses to honour it as an
# explicit layer: forcing it with VK_LOADER_LAYERS_ENABLE loads the .so (its
# init line appears) and then fails vkCreateInstance. Verified 2026-08-20
# against BOTH the freshly-built .so and the already-installed one, so this
# is a property of the layer, not of any particular build.
#
# Consequence: this script measures the INSTALLED layer. Run
# `sudo ./install.sh` first if you want it to reflect your working tree ,
# that is the same "fix at the source, never at the deployed copy" rule this
# repo already enforces, applied to verification.
#
# For logic-level coverage that does NOT need an install, use
# tests/run_vk_budget_reserve_test.sh, which extracts gbvk_reserved_budget()
# straight out of the source and unit-tests it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT

cc -O2 -o "$out/probe" "$here/vk_budget_probe.c" -lvulkan

installed=/usr/local/lib/libVkLayer_greenboost.so
if [[ -f "$installed" && "$here/../libVkLayer_greenboost.so" -nt "$installed" ]]; then
    echo "NOTE: $here/../libVkLayer_greenboost.so is newer than the installed copy."
    echo "      These numbers describe the INSTALLED layer. Run 'sudo ./install.sh'"
    echo "      to measure your working tree instead."
    echo
fi

echo "── layer bypassed (real driver values) ─────────────────────────"
VK_LOADER_LAYERS_DISABLE=VK_LAYER_GREENBOOST_memory "$out/probe" 2>/dev/null

echo
echo "── layer active, reserve DISABLED (pre-2026-08-20 behaviour) ───"
GREENBOOST_VULKAN=1 GREENBOOST_VK_BUDGET_RESERVE_MAX_MB=0 "$out/probe" 2>/dev/null

echo
echo "── layer active, reserve at default (12.5%, capped at 1 GiB) ───"
GREENBOOST_VULKAN=1 "$out/probe" 2>/dev/null

echo
echo "Expected once the new layer is installed: the third block's device-local"
echo "budget sits ~1 GiB below the second's, and both sit far above the first."
