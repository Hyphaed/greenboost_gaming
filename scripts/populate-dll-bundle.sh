#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
#
# scripts/populate-dll-bundle.sh , extract NVIDIA DLSS DLLs from the
# official SDK ZIP and populate ../dlls/ with a verified bundle that
# `gb_gaming.dlss_updater` can serve to the Gaming Suite users.
#
# What you need first
# -------------------
# 1. A NVIDIA Developer Program account (free).  Sign in once, accept
#    the DLSS SDK EULA, then download the latest SDK ZIP from:
#       https://developer.nvidia.com/dlss-getting-started
# 2. The ZIP file path , typically `~/Downloads/dlss-sdk-*.zip`.
#
# What this script does
# ---------------------
# • Extracts the SDK ZIP to a temp directory.
# • Locates the bare Windows-x86_64 DLLs we ship:
#       nvngx_dlss.dll      (DLSS SR)
#       nvngx_dlssg.dll     (DLSS Frame Generation)
#       nvngx_dlssd.dll     (DLSS Ray Reconstruction)
# • Optionally extracts Streamline DLLs too (sl.dlss.dll, sl.dlss_g.dll)
#   when the SDK ZIP contains a Streamline sub-folder.  These are also
#   available from NVIDIAGameWorks/Streamline GitHub releases , the
#   bundle copy is provided as an offline-friendly fallback.
# • Computes SHA-256 for each DLL.
# • Reads the existing dlls/manifest.json (if any) and merges new
#   entries while preserving older ones.
# • Writes dlls/manifest.json with: version, sha256, source (a free-text
#   provenance string identifying the SDK release), and added (ISO date).
#
# Usage
# -----
#   ./scripts/populate-dll-bundle.sh /path/to/dlss-sdk-X.Y.Z.zip
#   ./scripts/populate-dll-bundle.sh --dry-run /path/to/sdk.zip
#   ./scripts/populate-dll-bundle.sh --clean   # remove existing bundle
#
# Idempotent: re-running with the same ZIP overwrites with identical
# bytes (and a refreshed `added` date).  No network access.

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_BOLD=$'\033[1m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_RED=$'\033[31m'; C_CYA=$'\033[36m'; C_OFF=$'\033[0m'
else
    C_BOLD=""; C_GRN=""; C_YEL=""; C_RED=""; C_CYA=""; C_OFF=""
fi

step()   { printf "%s==>%s %s\n" "$C_CYA"  "$C_OFF" "$*"; }
ok()     { printf "%s ✓%s  %s\n" "$C_GRN"  "$C_OFF" "$*"; }
warn()   { printf "%s ⚠%s  %s\n" "$C_YEL"  "$C_OFF" "$*" >&2; }
die()    { printf "%s ✗%s  %s\n" "$C_RED"  "$C_OFF" "$*" >&2; exit 1; }

# ── paths ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE_DIR="$PROJECT_ROOT/dlls"
MANIFEST="$BUNDLE_DIR/manifest.json"

# Which filenames we recognise.  Must stay in sync with KNOWN in
# gb_gaming/dlss_updater.py.
DLSS_DLLS=(
    nvngx_dlss.dll
    nvngx_dlssg.dll
    nvngx_dlssd.dll
    sl.dlss.dll
    sl.dlss_g.dll
)

# ── arg parsing ──────────────────────────────────────────────────────
DRY_RUN=0
CLEAN=0
SDK_ZIP=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --clean)   CLEAN=1 ;;
        -h|--help)
            sed -n '/^# What you need first/,/^# Usage/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        -*)        die "unknown flag: $arg" ;;
        *)         SDK_ZIP="$arg" ;;
    esac
done

# ── clean mode ───────────────────────────────────────────────────────
if [[ $CLEAN -eq 1 ]]; then
    step "removing existing bundle at $BUNDLE_DIR"
    for d in "${DLSS_DLLS[@]}"; do
        if [[ -f "$BUNDLE_DIR/$d" ]]; then
            ((DRY_RUN)) || rm -v "$BUNDLE_DIR/$d"
        fi
    done
    if [[ -f $MANIFEST ]]; then
        ((DRY_RUN)) || rm -v "$MANIFEST"
    fi
    ok "bundle cleared"
    exit 0
fi

# ── validate ZIP ─────────────────────────────────────────────────────
[[ -n $SDK_ZIP ]] || die "usage: $0 [--dry-run] /path/to/dlss-sdk-X.Y.Z.zip"
[[ -f $SDK_ZIP ]] || die "ZIP not found: $SDK_ZIP"
[[ -d $BUNDLE_DIR ]] || mkdir -p "$BUNDLE_DIR"

step "inspecting $SDK_ZIP"
zip_basename="$(basename "$SDK_ZIP")"
zip_size="$(stat -c%s "$SDK_ZIP" 2>/dev/null || stat -f%z "$SDK_ZIP")"
printf "    %-12s %s\n" "size:"     "$((zip_size / 1024 / 1024)) MB"
printf "    %-12s %s\n" "filename:" "$zip_basename"

# Best-effort version extraction from the filename , DLSS SDK ZIPs are
# named like `dlss-sdk-3.7.20.zip` or `dlss_310.2.1.0.zip`.
sdk_version="$(echo "$zip_basename" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1)"
[[ -z $sdk_version ]] && sdk_version="unknown"
printf "    %-12s %s\n" "version:"  "$sdk_version"

# ── extract to temp ──────────────────────────────────────────────────
TMP="$(mktemp -d -t gb-dll-bundle.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

step "extracting to $TMP"
if ((DRY_RUN)); then
    warn "dry-run: skipping extraction"
else
    if command -v unzip >/dev/null 2>&1; then
        unzip -q "$SDK_ZIP" -d "$TMP"
    elif command -v bsdtar >/dev/null 2>&1; then
        bsdtar -xf "$SDK_ZIP" -C "$TMP"
    else
        die "neither unzip nor bsdtar found , install one"
    fi
fi

# ── find each DLL inside the extracted tree ──────────────────────────
declare -A DLL_PATH
for d in "${DLSS_DLLS[@]}"; do
    if ((DRY_RUN)); then continue; fi
    # NVIDIA's SDK lays the bare DLLs under lib/Windows_x86_64/{rel,dev}/.
    # Streamline ships them under bin/x64/.  Search broadly and pick
    # the first hit.
    hit="$(find "$TMP" -type f -iname "$d" -print -quit 2>/dev/null || true)"
    if [[ -n $hit ]]; then
        DLL_PATH[$d]="$hit"
        printf "    %-22s → %s\n" "$d" "${hit#$TMP/}"
    else
        warn "$d not found in this SDK ZIP , skipping (not all SDKs ship every variant)"
    fi
done

# ── copy + hash ──────────────────────────────────────────────────────
step "installing DLLs into $BUNDLE_DIR"
declare -A SHA256
for d in "${!DLL_PATH[@]}"; do
    src="${DLL_PATH[$d]}"
    dst="$BUNDLE_DIR/$d"
    if ((DRY_RUN)); then
        printf "    would copy %s\n" "$d"
        continue
    fi
    install -m 0644 "$src" "$dst"
    h="$(sha256sum "$dst" | awk '{print $1}')"
    SHA256[$d]="$h"
    printf "    %-22s sha256=%s\n" "$d" "${h:0:16}…"
done

# ── manifest.json ────────────────────────────────────────────────────
if ((DRY_RUN)); then
    ok "dry-run complete; no files written"
    exit 0
fi

step "writing $MANIFEST"

# Build a JSON object via python3 so we don't have to hand-quote things.
python3 - "$MANIFEST" "$sdk_version" "$zip_basename" <<'PYEOF'
import json, os, sys
from datetime import date

manifest_path, sdk_version, zip_filename = sys.argv[1], sys.argv[2], sys.argv[3]
existing = {"dlls": {}}
if os.path.exists(manifest_path):
    try:
        existing = json.load(open(manifest_path))
        existing.setdefault("dlls", {})
    except json.JSONDecodeError:
        pass

# Read freshly-computed hashes off the bundle dir.
bundle_dir = os.path.dirname(manifest_path)
today = date.today().isoformat()
import subprocess
for dll in os.listdir(bundle_dir):
    if not dll.lower().endswith(".dll"):
        continue
    full = os.path.join(bundle_dir, dll)
    sha = subprocess.check_output(["sha256sum", full]).decode().split()[0]
    existing["dlls"][dll] = {
        "version":  sdk_version,
        "sha256":   sha,
        "source":   f"extracted from NVIDIA DLSS SDK {sdk_version} ({zip_filename})",
        "added":    today,
    }

with open(manifest_path, "w") as f:
    json.dump(existing, f, indent=2)
print(f"  wrote {len(existing['dlls'])} entries")
PYEOF

ok "bundle populated"

cat <<EOF

${C_GRN}Done.${C_OFF}  The Gaming Suite will now serve these DLLs out of
${C_CYA}${BUNDLE_DIR}${C_OFF} as long as users have ${C_CYA}nvngx_source = bundled${C_OFF}
in their ${C_CYA}sources.conf${C_OFF}.

To verify, run:
    ${C_CYA}cd ${PROJECT_ROOT}
    python3 -c 'from gb_gaming import dlss_updater as d; \\
        ok, res = d.download_latest("nvngx_dlss.dll"); print(ok, res)'${C_OFF}

Reminder: these DLLs are NVIDIA-authored binaries shipped under the
DLSS SDK EULA.  Their license is NOT GPL v2 , they are bundled alongside
this GPL v2 project but are not derivative works of ours.  If you fork
the Gaming Suite for distribution, ship your own bundle or strip this
directory.  See DLSS_UPDATER.md for details.
EOF
