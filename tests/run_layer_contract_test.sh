#!/usr/bin/env bash
# Guards the two defects found 2026-08-20 that between them made the
# GreenBoost Vulkan layer completely inert. Neither produced an error at
# runtime, which is why both survived a public release.
#
#   A. The implicit-layer manifest had no `disable_environment`. The Vulkan
#      loader treats that as malformed and skips the layer, warning only
#      under VK_LOADER_DEBUG.
#   B. The HOOK macro in gbvk_GetInstanceProcAddr / gbvk_GetDeviceProcAddr
#      compared against the stringified argument ("CreateInstance") instead
#      of the real Vulkan name ("vkCreateInstance"), so every macro-declared
#      hook failed to resolve. With A fixed, this surfaced as
#      "loader_create_instance_chain: Failed to find 'vkCreateInstance'"
#      and every Vulkan app failed to start.
#
# Static checks only , no GPU, no install, safe in CI.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$here/.."
fail=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf 'FAIL    %s\n' "$1"; fail=$((fail+1)); }

# ── A: every manifest we ship or generate carries disable_environment ──
python3 - "$root" <<'PY' || exit 1
import json, sys
root = sys.argv[1]
d = json.load(open(f"{root}/VkLayer_greenboost.json"))["layer"]
if "enable_environment" in d and "disable_environment" not in d:
    print("FAIL    VkLayer_greenboost.json: enable_environment without "
          "disable_environment , the loader will SKIP this layer")
    sys.exit(1)
print("  ok    VkLayer_greenboost.json declares disable_environment")
PY

if grep -A25 'write_layer_manifest() {' "$root/install.sh" | grep -q 'disable_environment'; then
    ok "install.sh's write_layer_manifest() emits disable_environment"
else
    bad "install.sh's write_layer_manifest() omits disable_environment"
fi

# ── B: the HOOK macro must prepend "vk" before comparing ──────────────
hooks=$(grep -c '#define HOOK(fn)' "$root/greenboost_vulkan_layer.c" || true)
prefixed=$(grep -c '#define HOOK(fn)  if (strcmp(name, "vk" #fn)' "$root/greenboost_vulkan_layer.c" || true)
if [[ "$hooks" -gt 0 && "$hooks" == "$prefixed" ]]; then
    ok "all $hooks HOOK macro(s) compare against \"vk\" #fn"
else
    bad "HOOK macro compares against bare #fn ($prefixed/$hooks prefixed) , every hook will fail to resolve"
fi

# The layer must still export the loader's negotiation entry point.
if grep -q 'vkNegotiateLoaderLayerInterfaceVersion' "$root/greenboost_vulkan_layer.c"; then
    ok "vkNegotiateLoaderLayerInterfaceVersion present"
else
    bad "vkNegotiateLoaderLayerInterfaceVersion missing"
fi

echo
[[ $fail -eq 0 ]] && echo "layer contract OK" || echo "$fail failure(s)"
exit $fail
