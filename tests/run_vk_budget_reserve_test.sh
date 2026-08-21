#!/usr/bin/env bash
# Extract gbvk_reserved_budget() from the real layer source and test it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/../greenboost_vulkan_layer.c"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

# Everything from the function's opening line to its closing brace at col 0.
awk '/^static uint64_t gbvk_reserved_budget\(uint64_t capacity\)$/,/^}$/' \
    "$src" > "$out/extracted_reserve.inc"

if ! grep -q 'return capacity - reserve;' "$out/extracted_reserve.inc"; then
    echo "FAIL: could not extract gbvk_reserved_budget() from $src" >&2
    echo "      (did the signature change? update the awk pattern)" >&2
    exit 1
fi

cc -O2 -Wall -Wextra -Werror -I"$out" -o "$out/t" "$here/vk_budget_reserve_test.c"
"$out/t"
