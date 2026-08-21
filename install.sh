#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
#
# GreenBoost Gaming Suite , installer
#
# What this installs:
#   1. The GreenBoost GVM Vulkan implicit layer (libVkLayer_greenboost.so +
#      manifest) so that Vulkan applications see GPU VRAM + System DDR as one
#      pool, overflowing through the GreenBoost CUDA path.
#   2. The Gaming Suite GUI (Tauri: React + Rust).
#   3. A `.desktop` entry + icon so the app shows up in the GNOME App Grid
#      and KDE/XFCE menus.
#   4. The `greenboost-gaming` CLI launcher.
#
# Pre-requisite:
#   GreenBoost (the kernel module + CUDA shim) must already be installed.
#   The Vulkan layer is a frontend onto the same memory pool.
#
# Usage:
#   sudo ./install.sh                # full install
#   sudo ./install.sh --uninstall    # remove everything this script installed
#   ./install.sh --check             # dry-run: report what would happen

set -euo pipefail

# ── colors ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_GRN=$'\033[32m'
    C_YEL=$'\033[33m'; C_CYA=$'\033[36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_BOLD=""; C_RED=""; C_GRN=""; C_YEL=""; C_CYA=""; C_DIM=""; C_OFF=""
fi

step()    { printf "%s==>%s %s\n"        "$C_CYA" "$C_OFF" "$*"; }
ok()      { printf "%s ✓%s %s\n"         "$C_GRN" "$C_OFF" "$*"; }
warn()    { printf "%s ⚠%s %s\n"         "$C_YEL" "$C_OFF" "$*" >&2; }
fail()    { printf "%s ✗%s %s\n"         "$C_RED" "$C_OFF" "$*" >&2; exit 1; }
# err() prints one plain line to stderr without a glyph or an exit , used to
# build the multi-line "what happened / what it costs / what still works /
# the one command that fixes it" explanations that precede a fail().
err()     { printf "%s%s%s\n"            "$C_RED" "$*" "$C_OFF" >&2; }
heading() { printf "\n%s%s%s\n%s\n"      "$C_BOLD" "$*" "$C_OFF" \
                                          "$(printf '─%.0s' $(seq 1 ${#1}))"; }

# ── paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# system install destinations (overridable via env for packagers)
PREFIX="${PREFIX:-/usr/local}"
DESTDIR="${DESTDIR:-}"
LIBDIR="${LIBDIR:-$PREFIX/lib}"
BINDIR="${BINDIR:-$PREFIX/bin}"

# ── hardware-aware build flags ──────────────────────────────────────────
# Same convention as GreenBoost core's Makefile (COMMON_CFLAGS /
# SHIM_CFLAGS): compile for the machine actually running the installer,
# not a portable baseline , this binary never leaves the box it's built
# on. HAS_AVX2 is a real /proc/cpuinfo probe, not assumed, mirroring
# core's `HAS_AVX2 := $(shell grep -qw avx2 /proc/cpuinfo ...)` guard.
HAS_AVX2=0
grep -qw avx2 /proc/cpuinfo 2>/dev/null && HAS_AVX2=1
GB_GAMING_CFLAGS=(
    -march=native -mtune=native -O3 -funroll-loops -std=gnu11
    -flto -fvisibility=hidden -ffunction-sections -fdata-sections
    -fomit-frame-pointer -fprefetch-loop-arrays
)
[[ "$HAS_AVX2" == "1" ]] && GB_GAMING_CFLAGS+=(-mavx2)

VULKAN_LAYER_DIR="${DESTDIR}/usr/share/vulkan/implicit_layer.d"
DESKTOP_DIR="${DESTDIR}/usr/share/applications"
ICON_DIR="${DESTDIR}/usr/share/icons/hicolor/scalable/apps"
ICON_FALLBACK_DIR="${DESTDIR}/usr/share/icons/hicolor/128x128/apps"
APP_LIB_DIR="${DESTDIR}${LIBDIR}/greenboost-gaming"

# Every remaining system artifact this script creates. These live here, not
# next to the install step that writes each one, because the uninstall path
# runs long before those steps and has to name the same paths. Adding a new
# installed artifact means adding it here AND to the uninstall block AND to
# checks/allowlists/install_manifest.txt , that is the parity discipline.
FAN_UNIT_DST="${DESTDIR}/usr/lib/systemd/user/gb-gaming-fan-daemon.service"
POLKIT_RULE_DST="${DESTDIR}/etc/polkit-1/rules.d/60-greenboost-fan.rules"
SUDOERS_DST="${DESTDIR}/etc/sudoers.d/60-greenboost-fan"
UDEV_RULE_DST="${DESTDIR}/etc/udev/rules.d/99-greenboost-gaming.rules"
TMPFILES_DST="${DESTDIR}/etc/tmpfiles.d/greenboost-gaming.conf"
# Keeps the tmpfiles rule above OUT of the initramfs. It group-scopes
# /sys/module/greenboost/parameters/gaming_mode, which cannot exist before the
# module is loaded , so in the initrd it has nothing to act on, and instead
# fails on every boot with "Failed to resolve group 'greenboost': Unknown
# group" (the initrd is a different root filesystem and the group is not in
# its /etc/group). GreenBoost core ships a broader exclusion hook; this one
# covers a Gaming-Suite-only install, where that hook is not present.
INITRAMFS_HOOK_DST="${DESTDIR}/etc/initramfs-tools/hooks/zz-greenboost-gaming-exclude"
PROFILES_DST="${DESTDIR}/usr/share/greenboost-gaming/profiles/per-game"
GB_GROUP="greenboost"
APP_NAME="greenboost-gaming"
APP_DISPLAY_NAME="GreenBoost Gaming Suite"
# Must match `identifier` in src/src-tauri/tauri.conf.json. With that config's
# app.enableGTKAppId=true, this is the app_id Tao/GTK reports to Wayland at
# runtime (xdg_toplevel.set_app_id) , GNOME Shell's window tracker looks up
# "<app_id>.desktop" to find the running app's icon (dock/taskbar), so the
# installed .desktop file's basename has to be this, not $APP_NAME. Without
# this match the dock/taskbar falls back to a generic icon while the app is
# running, even though the App Grid (which reads Icon= from the .desktop
# file directly, no app_id matching involved) still shows the right one.
APP_ID="com.ferran.greenboost-gaming-suite"

# ── arg parsing ───────────────────────────────────────────────────────
MODE="install"
NO_INSTALL_DEPS=0
for arg in "$@"; do
    case "$arg" in
        --uninstall)       MODE="uninstall" ;;
        --check)           MODE="check" ;;
        --no-install-deps) NO_INSTALL_DEPS=1 ;;
        -h|--help)
            sed -n '/^# What this installs:/,/^# Usage:/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) fail "Unknown argument: $arg (try --help)" ;;
    esac
done
export NO_INSTALL_DEPS

# ── helpers ───────────────────────────────────────────────────────────
need_root() {
    if [[ $EUID -ne 0 ]]; then
        fail "this step needs root; re-run with sudo"
    fi
}

# detect_greenboost , locates an installed GreenBoost.
# Sources, in order:
#   1. greenboost CLI on PATH
#   2. /usr/local/lib/libgreenboost_cuda.so present
#   3. /lib/modules/$(uname -r)/.../greenboost.ko present
detect_greenboost() {
    if command -v greenboost >/dev/null 2>&1; then
        GB_VERSION="$(greenboost --version 2>/dev/null | head -n1 || echo unknown)"
        GB_SOURCE="cli"
        return 0
    fi
    if [[ -f "${DESTDIR}/usr/local/lib/libgreenboost_cuda.so" \
       || -f "${DESTDIR}/usr/lib/libgreenboost_cuda.so" ]]; then
        GB_VERSION="shim-only"
        GB_SOURCE="shim"
        return 0
    fi
    local kver; kver="$(uname -r)"
    if find "/lib/modules/${kver}" -name 'greenboost.ko*' 2>/dev/null \
            | grep -q .; then
        GB_VERSION="kmod-only"
        GB_SOURCE="kmod"
        return 0
    fi
    return 1
}

# ── distro detection + system-deps install ────────────────────────────
# Auto-installs the OS packages this Suite needs.  Skipped when --check
# is passed (dry run) or when --no-install-deps is set.
#
# Packages installed:
#   - Vulkan loader + tools     (libvulkan1, vulkan-tools)
#   - WebKit + dev headers for Tauri's WebView backend
#   - Python GObject bindings (Gio/GLib) for the GNOME VRR + display helpers
#   - Compiler toolchain so the Vulkan layer can build from source
#
# We use the distro's package manager directly; no curl-piped scripts.

detect_distro() {
    if [[ -r /etc/os-release ]]; then
        . /etc/os-release
        echo "${ID,,}"
    elif command -v lsb_release >/dev/null 2>&1; then
        lsb_release -si | tr '[:upper:]' '[:lower:]'
    else
        echo "unknown"
    fi
}

install_system_deps() {
    [[ "$MODE" == "check" ]] && return 0
    [[ "${NO_INSTALL_DEPS:-0}" == "1" ]] && return 0

    local distro; distro="$(detect_distro)"
    step "installing system dependencies (distro: $distro)"

    case "$distro" in
        ubuntu|debian|linuxmint|pop|elementary|kali)
            local pkgs=(
                # Vulkan
                libvulkan1 vulkan-tools mesa-vulkan-drivers
                # Tauri WebView backend
                libwebkit2gtk-4.1-dev libjavascriptcoregtk-4.1-dev
                libsoup-3.0-dev libayatana-appindicator3-dev librsvg2-dev
                # GNOME VRR / DisplayConfig D-Bus helper , gb_gaming's
                # _vrr_gnome.py / _display_config.py need Gio + GLib only.
                python3-gi gir1.2-glib-2.0
                # Compiler + Tauri build toolchain (Rust + Node)
                build-essential pkg-config
                rustc cargo nodejs npm
                # Live Overlay backend (Games/Live view "performance overlay" toggle)
                mangohud
            )
            # Only install what's missing , saves time on repeat runs.
            local missing=()
            for p in "${pkgs[@]}"; do
                dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
            done
            if [[ ${#missing[@]} -eq 0 ]]; then
                ok "all .deb dependencies already installed"
                return 0
            fi
            warn "installing ${#missing[@]} missing packages: ${missing[*]}"
            apt-get update -qq
            apt-get install -y "${missing[@]}" \
                || warn "some packages failed to install , continuing anyway"
            ;;
        fedora|rhel|centos|rocky|almalinux)
            local pkgs=(
                vulkan-loader vulkan-tools mesa-vulkan-drivers
                webkit2gtk4.1-devel javascriptcoregtk4.1-devel
                libsoup3-devel libappindicator-gtk3-devel librsvg2-devel
                python3-gobject
                gcc gcc-c++ pkgconf-pkg-config
                rust cargo nodejs npm
                mangohud
            )
            dnf install -y "${pkgs[@]}" \
                || warn "some packages failed to install , continuing anyway"
            ;;
        arch|manjaro|endeavouros|cachyos)
            local pkgs=(
                vulkan-icd-loader vulkan-tools
                webkit2gtk-4.1 libsoup3 libappindicator-gtk3 librsvg
                python-gobject
                base-devel pkgconf
                rust nodejs npm
                mangohud
            )
            pacman -S --noconfirm --needed "${pkgs[@]}" \
                || warn "some packages failed to install , continuing anyway"
            ;;
        opensuse*|suse|sles)
            local pkgs=(
                libvulkan1 vulkan-tools
                webkit2gtk3-soup2-devel libsoup-devel
                python3-gobject
                gcc gcc-c++ pkgconf-pkg-config
            )
            zypper install -y "${pkgs[@]}" \
                || warn "some packages failed to install , continuing anyway"
            ;;
        *)
            warn "unrecognised distro '$distro' , skipping auto-install"
            warn "install Vulkan, WebKit2GTK 4.1, and a"
            warn "C compiler manually before re-running this installer."
            return 0
            ;;
    esac
    ok "system dependencies installed"
}

print_missing_greenboost() {
    cat >&2 <<EOF

${C_RED}${C_BOLD}GreenBoost is not installed.${C_OFF}

The Gaming Suite is a frontend onto the GreenBoost memory pool , the
kernel module and CUDA shim must be in place before the Vulkan layer
can do anything useful.

Install GreenBoost manually:

    ${C_CYA}git clone https://gitlab.com/IsolatedOctopi/greenboost.git
    cd greenboost
    sudo ./greenboost_setup.sh${C_OFF}

Then come back and run this installer again, or drop
GREENBOOST_SKIP_CORE_INSTALL=1 so this script does it for you.

EOF
    exit 2
}

# GreenBoost core (kernel module + CUDA shim) is a hard pre-requisite for
# the Vulkan/OpenGL layers to do anything useful , they're a frontend onto
# its memory pool. By default, when it's missing, fetch and install it
# from source instead of just telling the user to do it themselves.
# Set GREENBOOST_SKIP_CORE_INSTALL=1 to keep the old fail-fast behaviour
# (CI, packagers, or anyone managing GreenBoost core separately).
GREENBOOST_CORE_REPO="https://gitlab.com/IsolatedOctopi/greenboost.git"
GREENBOOST_CORE_SRC_DIR="${GREENBOOST_CORE_SRC_DIR:-/usr/local/src/greenboost-core}"

install_greenboost_core() {
    if [[ "$MODE" == "check" ]]; then
        warn "GreenBoost core not detected , full install would clone+build it from $GREENBOOST_CORE_REPO"
        return 0
    fi
    need_root
    heading "GreenBoost core not found , fetching and installing from source"
    command -v git >/dev/null 2>&1 \
        || fail "git is required to fetch GreenBoost core , install it and re-run"

    if [[ -d "$GREENBOOST_CORE_SRC_DIR/.git" ]]; then
        step "updating existing checkout at $GREENBOOST_CORE_SRC_DIR"
        git -C "$GREENBOOST_CORE_SRC_DIR" pull --ff-only \
            || warn "git pull failed , continuing with existing checkout"
    else
        step "cloning $GREENBOOST_CORE_REPO"
        install -d "$(dirname "$GREENBOOST_CORE_SRC_DIR")"
        git clone --depth 1 "$GREENBOOST_CORE_REPO" "$GREENBOOST_CORE_SRC_DIR" \
            || fail "clone failed , check network access to gitlab.com"
    fi

    [[ -x "$GREENBOOST_CORE_SRC_DIR/greenboost_setup.sh" ]] \
        || fail "greenboost_setup.sh not found in $GREENBOOST_CORE_SRC_DIR , repo layout changed?"

    step "running GreenBoost core's own full installer (kernel module + CUDA shim)"
    ( cd "$GREENBOOST_CORE_SRC_DIR" && ./greenboost_setup.sh --full-install ) \
        || fail "GreenBoost core install failed , see output above"

    ok "GreenBoost core installed from source"
}

# ── check ─────────────────────────────────────────────────────────────
heading "GreenBoost Gaming Suite installer  (mode: ${MODE})"

if ! detect_greenboost; then
    if [[ "${GREENBOOST_SKIP_CORE_INSTALL:-0}" == "1" ]]; then
        print_missing_greenboost
    fi
    install_greenboost_core
    if [[ "$MODE" != "check" ]] && ! detect_greenboost; then
        fail "GreenBoost core install finished but is still not detected , check the output above"
    fi
fi
if detect_greenboost; then
    ok "found GreenBoost (${GB_SOURCE}, ${GB_VERSION})"
fi

# Install system .deb / .rpm / pacman dependencies BEFORE any toolchain
# probes , this way the probes below pick up the freshly-installed
# webkit2gtk / pygobject / vulkan-tools packages.  Only runs in install
# mode and only with root.  Pass --no-install-deps to skip.
if [[ "$MODE" == "install" && $EUID -eq 0 && $NO_INSTALL_DEPS -ne 1 ]]; then
    install_system_deps
elif [[ "$MODE" == "install" && $EUID -ne 0 ]]; then
    warn "running unprivileged , system dependency install skipped"
    warn "  re-run with sudo to auto-install Vulkan, WebKit2GTK, etc."
fi

# Tauri toolchain , required. The GUI is Tauri (React + Rust); there is
# no second GUI to fall back to since the GTK4 one was removed.
HAS_TAURI=0
if command -v cargo >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    HAS_TAURI=1
fi

case "$MODE" in
    check)
        # Reuse the same syslib probe the install path uses.
        have_tauri_syslibs() {
            command -v pkg-config >/dev/null 2>&1 && \
            pkg-config --exists webkit2gtk-4.1 javascriptcoregtk-4.1 libsoup-3.0 2>/dev/null
        }
        heading "Pre-flight summary"
        printf "  GreenBoost:           %s\n" "${GB_VERSION} (${GB_SOURCE})"
        printf "  Vulkan loader:        %s\n" \
               "$(command -v vulkaninfo >/dev/null && echo yes || echo missing)"
        printf "  Tauri toolchain:      %s\n" \
               "$([[ $HAS_TAURI -eq 1 ]] && echo yes || echo missing)"
        printf "  Tauri system libs:    %s\n" \
               "$(have_tauri_syslibs && echo yes || echo 'missing , see warning above')"
        printf "\n  GUI:                  tauri (React + Rust)\n"
        printf "\nWould install to:\n"
        printf "  Vulkan layer:      %s/VkLayer_greenboost.json\n"  "$VULKAN_LAYER_DIR"
        printf "                     %s/libVkLayer_greenboost.so\n" "$LIBDIR"
        printf "  OpenGL layer:      %s/libgb_gl.so\n"              "$LIBDIR"
        printf "  GUI files:         %s/\n"                          "$APP_LIB_DIR"
        printf "  \$HOME mirror:      ~/.local/lib/{libVkLayer_greenboost.so,libgb_gl.so,greenboost-gaming/gb_gaming/}\n"
        printf "                     ~/.local/share/vulkan/implicit_layer.d/VkLayer_greenboost.json\n"
        printf "  Launcher:          %s/${APP_NAME}\n"               "$BINDIR"
        printf "  .desktop entry:    %s/${APP_ID}.desktop\n"       "$DESKTOP_DIR"
        printf "  Icon:              %s/${APP_NAME}.svg (or .png)\n" "$ICON_DIR"
        exit 0
        ;;
    install)   need_root ;;
    uninstall) need_root ;;
esac

# Resolve the invoking (non-root) user + home dir once , needed both by the
# Vulkan layer section and the runtime home-mirror section below, since
# Steam's Pressure Vessel container only bind-mounts $HOME, not /usr/local.
_REAL_USER="${SUDO_USER:-}"
_REAL_HOME=""
if [[ -n "$_REAL_USER" ]]; then
    _REAL_HOME="$(getent passwd "$_REAL_USER" 2>/dev/null | cut -d: -f6)"
fi

# Writes a Vulkan implicit-layer manifest with the given library_path baked
# in. Called once for the system destination and once for the user-home
# mirror , each needs a different library_path, so the manifest can't just
# be copied between them.
# Writes a Vulkan implicit-layer manifest.
#
# disable_environment is NOT optional. The Vulkan loader treats an implicit
# layer manifest without it as malformed and SKIPS THE LAYER ENTIRELY, with
# only a warning behind VK_LOADER_DEBUG:
#
#   Layer "VK_LAYER_GREENBOOST_memory" doesn't contain required layer object
#   disable_environment in the manifest JSON file, skipping this layer
#
# Nothing else reports a problem: the .so is installed, the Status view says
# "Vulkan Layer: Installed" (it checks the file exists), and games simply run
# without any GreenBoost involvement , no VRAM inflation, no T2/T3 overflow,
# no NIS, no Reflex, no telemetry. Found 2026-08-20; see CHANGELOG.md.
write_layer_manifest() {
    local dest_json="$1" lib_path="$2"
    cat > "$dest_json" <<EOF
{
    "file_format_version": "1.0.0",
    "layer": {
        "name": "VK_LAYER_GREENBOOST_memory",
        "type": "GLOBAL",
        "library_path": "$lib_path",
        "api_version": "1.3.0",
        "implementation_version": "1",
        "description": "GreenBoost virtual VRAM , inflates device-local heap and routes overflow allocations to T2/T3 DDR via DMA-BUF",
        "enable_environment": {
            "GREENBOOST_VULKAN": "1"
        },
        "disable_environment": {
            "GREENBOOST_VULKAN_DISABLE": "1"
        }
    }
}
EOF
}

# ── uninstall ─────────────────────────────────────────────────────────
if [[ "$MODE" == "uninstall" ]]; then
    heading "Uninstalling"

    _REAL_USER="${SUDO_USER:-}"
    _REAL_HOME=""
    if [[ -n "$_REAL_USER" ]]; then
        _REAL_HOME="$(getent passwd "$_REAL_USER" 2>/dev/null | cut -d: -f6)"
    fi

    # ── 1. the fan daemon, stopped before its unit file is removed ──────
    # Order matters: removing the unit while the service is running leaves a
    # daemon alive with no unit backing it, which `systemctl --user status`
    # then reports as "not-found" while it keeps driving the fans.
    if [[ -n "$_REAL_USER" ]]; then
        sudo -u "$_REAL_USER" -H systemctl --user disable --now \
            gb-gaming-fan-daemon.service >/dev/null 2>&1 || true
    fi
    rm -fv "$FAN_UNIT_DST" 2>/dev/null || true

    # ── 2. privilege grants ────────────────────────────────────────────
    # These come first among the file removals because leaving either one
    # behind is worse than leaving a binary behind: the polkit rule grants
    # passwordless root for the fan helper, and until 2026-08-20 it did so
    # via a substring match that any local user could abuse (see
    # scripts/60-greenboost-fan.rules). An uninstall that left it in place
    # left that grant on the machine after the software was gone.
    rm -fv "$POLKIT_RULE_DST" "$SUDOERS_DST" 2>/dev/null || true

    # ── 3. layers, launcher, desktop integration, system rules ─────────
    rm -fv "$VULKAN_LAYER_DIR/VkLayer_greenboost.json" \
           "$LIBDIR/libVkLayer_greenboost.so" \
           "$LIBDIR/libgb_gl.so" \
           "$BINDIR/$APP_NAME" \
           "$DESKTOP_DIR/$APP_ID.desktop" \
           "$ICON_DIR/$APP_NAME.svg" \
           "$ICON_FALLBACK_DIR/$APP_NAME.png" \
           "$UDEV_RULE_DST" \
           "$TMPFILES_DST" \
           "$INITRAMFS_HOOK_DST" 2>/dev/null || true
    # The hook is gone; rebuild so the image matches a never-installed machine.
    if [[ -z "$DESTDIR" ]] && command -v update-initramfs >/dev/null 2>&1; then
        update-initramfs -u -k all >/dev/null 2>&1 || true
    fi
    rm -rfv "$APP_LIB_DIR" 2>/dev/null || true

    # ── 4. shipped per-game profiles ───────────────────────────────────
    # Read-only reference data this installer wrote under /usr/share , not
    # user data. The user's own overrides live in ~/.config/greenboost-gaming
    # and are deliberately left alone (see the note at the end).
    rm -rfv "${DESTDIR}/usr/share/greenboost-gaming" 2>/dev/null || true

    # ── 5. the $HOME mirror (Pressure Vessel / Steam sandbox visibility) ─
    if [[ -n "$_REAL_HOME" ]]; then
        rm -fv "$_REAL_HOME/.local/share/vulkan/implicit_layer.d/VkLayer_greenboost.json" \
               "$_REAL_HOME/.local/lib/libVkLayer_greenboost.so" \
               "$_REAL_HOME/.local/lib/libgb_gl.so" \
            2>/dev/null || true
        rm -rfv "$_REAL_HOME/.local/lib/greenboost-gaming" 2>/dev/null || true
        # Game-session records written by the Proton wrapper
        # (gb_gaming/game_lifecycle.py's STATE_DIR). Created on demand at
        # launch, never by this installer , but an uninstall that left them
        # behind would leave the next install reading sessions for games that
        # no longer have a Suite. Config (~/.config/greenboost-gaming) is
        # deliberately kept, as noted at the end; this is runtime state, not
        # settings.
        rm -rfv "${XDG_STATE_HOME:-$_REAL_HOME/.local/state}/greenboost-gaming" \
            2>/dev/null || true
    fi

    # ── 6. the Steam compatibility-tool copy ───────────────────────────
    # Mirrors the install-side hand-off: install.sh calls
    # greenboost_proton/install.sh as the real user, so uninstall must too.
    # Steam runs that DEPLOYED copy, not the repo file , leaving it behind
    # means Steam keeps offering a GreenBoost Proton that points at layers
    # and helpers this uninstall just deleted.
    if [[ -n "$_REAL_USER" && -x "$PROJECT_ROOT/greenboost_proton/install.sh" ]]; then
        step "removing the Steam compatibility-tool copy"
        sudo -u "$_REAL_USER" -H bash \
            "$PROJECT_ROOT/greenboost_proton/install.sh" --uninstall || \
            warn "the Proton compatibility-tool removal reported an error , check above"
    fi

    # ── 7. the greenboost group ────────────────────────────────────────
    # Only when nobody else is still in it. The group is shared with the
    # core GreenBoost install (it also grants access to the kernel module's
    # sysfs parameters), so deleting it out from under a still-installed
    # core would break that, and any user listed in it presumably still
    # wants it.
    if getent group "$GB_GROUP" >/dev/null 2>&1; then
        _members="$(getent group "$GB_GROUP" | cut -d: -f4)"
        if [[ -z "$_members" ]]; then
            groupdel "$GB_GROUP" 2>/dev/null \
                && ok "removed the '$GB_GROUP' group" \
                || warn "could not remove the '$GB_GROUP' group , remove it manually with: sudo groupdel $GB_GROUP"
        else
            warn "left the '$GB_GROUP' group in place , still has members: $_members"
        fi
    fi

    # ── 8. reload the system databases we wrote into ───────────────────
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules 2>/dev/null || true
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -n "$_REAL_USER" ]]; then
        sudo -u "$_REAL_USER" -H systemctl --user daemon-reload 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -fq /usr/share/icons/hicolor 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
    fi

    ok "uninstalled"
    printf "\n"
    printf "Your own settings were left alone on purpose:\n"
    printf "  ~/.config/greenboost-gaming/   per-game overrides, GPU profiles, global settings\n"
    printf "  ~/.local/share/greenboost/     session history and the dataflux event log\n"
    printf "  %s/libraries/   downloaded DLSS/Streamline DLLs\n" "$PROJECT_ROOT"
    printf "Delete those by hand if you want a completely clean slate.\n"
    exit 0
fi

# ── install: Vulkan implicit layer ────────────────────────────────────
heading "Installing GVM Vulkan implicit layer"

# Rebuild whenever the .so is missing OR older than its sources , not just
# "missing". The previous check only fired on a missing file, so re-running
# `sudo ./install.sh` after editing greenboost_vulkan_layer.c silently kept
# shipping the stale prebuilt .so: this is the exact "deployed copy
# predates the source fix" incident class documented above under "Fix at
# the source, never at the deployed copy", just one file over. Mirrors the
# same dependency list `make vulkan` already tracks correctly.
_vk_so="$PROJECT_ROOT/libVkLayer_greenboost.so"
_vk_stale=0
if [[ ! -f "$_vk_so" ]]; then
    _vk_stale=1
else
    for _vk_src in "$PROJECT_ROOT/greenboost_vulkan_layer.c" \
                    "$PROJECT_ROOT/greenboost_ioctl.h"; do
        [[ -f "$_vk_src" && "$_vk_src" -nt "$_vk_so" ]] && _vk_stale=1
    done
fi
if [[ "$_vk_stale" == "1" ]]; then
    step "building libVkLayer_greenboost.so from source"
    if ! command -v cc >/dev/null && ! command -v gcc >/dev/null; then
        fail "no C compiler found , install gcc or clang"
    fi
    cc=${CC:-gcc}
    "$cc" -fPIC -shared "${GB_GAMING_CFLAGS[@]}" \
        -o "$_vk_so" \
        "$PROJECT_ROOT/greenboost_vulkan_layer.c" \
        -ldl -lpthread \
        || fail "Vulkan layer compile failed"
fi

install -d "$LIBDIR" "$VULKAN_LAYER_DIR"
install -m 0755 "$PROJECT_ROOT/libVkLayer_greenboost.so" \
                "$LIBDIR/libVkLayer_greenboost.so"

write_layer_manifest "$VULKAN_LAYER_DIR/VkLayer_greenboost.json" \
                      "$LIBDIR/libVkLayer_greenboost.so"
ok "Vulkan layer + manifest installed"

# ── install: OpenGL LD_PRELOAD interposer ─────────────────────────────
heading "Installing GreenBoost OpenGL interposer layer"

# Same staleness fix as the Vulkan layer above , rebuild on missing OR
# stale, not just missing.
_gl_so="$PROJECT_ROOT/libgb_gl.so"
_gl_stale=0
if [[ ! -f "$_gl_so" ]]; then
    _gl_stale=1
elif [[ -f "$PROJECT_ROOT/greenboost_gl_layer.c" && \
        "$PROJECT_ROOT/greenboost_gl_layer.c" -nt "$_gl_so" ]]; then
    _gl_stale=1
fi
if [[ "$_gl_stale" == "1" ]]; then
    step "building libgb_gl.so from source"
    cc=${CC:-gcc}
    "$cc" -fPIC -shared "${GB_GAMING_CFLAGS[@]}" \
        -o "$_gl_so" \
        "$PROJECT_ROOT/greenboost_gl_layer.c" \
        -ldl -lpthread \
        || { warn "OpenGL layer compile failed , skipping"; }
fi
if [[ -f "$PROJECT_ROOT/libgb_gl.so" ]]; then
    install -m 0755 "$PROJECT_ROOT/libgb_gl.so" "$LIBDIR/libgb_gl.so"
    ok "OpenGL layer installed at $LIBDIR/libgb_gl.so"
fi

# ── install: GUI ──────────────────────────────────────────────────────
heading "Installing GUI"

install -d "$APP_LIB_DIR"

# The GUI is Tauri (React + Rust + Tailwind), matching the NVIDIA-app
# aesthetic (left sidebar, dark cards, green accent). It requires cargo +
# npm + the system webkit2gtk libraries.
#
# There is no second GUI. The Python GTK4 fallback was removed (2026-08-20)
# along with its silent-downgrade path: when the Tauri toolchain was
# missing, this script used to quietly install the plainer GTK4 app
# instead, so a user who never read the scrollback ended up running a
# different program than the one the docs describe. Missing build
# dependencies now stop the install and say what to install.

# Check for the Tauri Linux system dependencies.
have_tauri_syslibs() {
    if ! command -v pkg-config >/dev/null 2>&1; then return 1; fi
    pkg-config --exists webkit2gtk-4.1 javascriptcoregtk-4.1 libsoup-3.0 2>/dev/null
}

if [[ $HAS_TAURI -ne 1 ]]; then
    err "The GUI can't be built: cargo or npm isn't on PATH."
    err ""
    err "What that costs you: nothing else is affected , the Vulkan and OpenGL"
    err "layers, the Proton wrapper, the fan daemon and the per-game profiles"
    err "all install and work without the GUI. You just won't get the desktop"
    err "app until the toolchain is present, and this install is stopping"
    err "before it changes anything rather than half-installing."
    err ""
    err "To fix it:"
    err "  Install Rust:  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    err "  Install Node:  your distro package manager, or nvm"
    err "Then re-run: sudo ./install.sh"
    fail "Tauri toolchain missing"
fi

if ! have_tauri_syslibs; then
    err "The GUI can't be built: the WebKit development libraries are missing."
    err ""
    err "What that costs you: same as above , everything except the desktop"
    err "app is unaffected, and nothing has been installed yet."
    err ""
    err "To fix it, install the package set for your distro:"
    err "  Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev \\"
    err "                                  libjavascriptcoregtk-4.1-dev \\"
    err "                                  libsoup-3.0-dev \\"
    err "                                  libayatana-appindicator3-dev \\"
    err "                                  librsvg2-dev"
    err "  Fedora:        sudo dnf install webkit2gtk4.1-devel \\"
    err "                                  javascriptcoregtk4.1-devel \\"
    err "                                  libsoup3-devel"
    err "  Arch:          sudo pacman -S webkit2gtk-4.1 libsoup3"
    err "Then re-run: sudo ./install.sh"
    fail "WebKit development libraries missing"
fi

step "building Tauri bundle (this can take several minutes)"
pushd "$PROJECT_ROOT/src" >/dev/null
# Build as the invoking user, not root , same reasoning as the
# greenboost_proton/install.sh hand-off further down. npm and cargo
# write into the source tree (src/dist/, src/node_modules/,
# src/src-tauri/target/) and into their own caches; doing that as root
# leaves root-owned build output behind in a user-owned checkout.
# Observed 2026-08-16: src/dist/assets/ owned by root:root from an
# earlier sudo install made every later unprivileged `npm run build`
# die with EACCES in vite's prepare-out-dir step, which reads as a
# broken toolchain rather than as a permissions leftover. Only the
# install(1) of the finished binary below needs to be root.
# Rust's equivalent of GB_GAMING_CFLAGS' -march=native , same
# never-leaves-this-box rationale as the C layers above.
if [[ -n "$_REAL_USER" ]]; then
    # Heal a checkout an older root build already poisoned. Without
    # this, switching to an unprivileged build just moves the EACCES
    # from a future `npm run build` into this one.
    # Test recursively, not just the top directory: the observed case
    # was a user-owned src/dist containing a root-owned dist/assets,
    # which a top-level ownership check walks straight past.
    for _d in dist node_modules src-tauri/target; do
        [[ -e "$_d" ]] || continue
        if [[ -n "$(find "$_d" ! -user "$_REAL_USER" -print -quit 2>/dev/null)" ]]; then
            warn "reclaiming root-owned files under src/$_d from an earlier privileged build"
            chown -R "$_REAL_USER" "$_d"
        fi
    done
    # PATH="$PATH" is load-bearing, not decoration. npm/node commonly
    # live under ~/.nvm/versions/node/*/bin (they do on the machine
    # this was written on), which is on nobody's secure_path , so a
    # plain `sudo -u ... npm` resolves to nothing and the GUI build
    # dies claiming npm is missing. We already hold a PATH that finds
    # it (this script got here via npm-adjacent checks), so forward
    # that rather than guessing at the user's shell rc files.
    sudo -u "$_REAL_USER" -H env \
        PATH="$PATH" \
        RUSTFLAGS="${RUSTFLAGS:-} -C target-cpu=native" \
        sh -c 'npm install --no-fund --no-audit && npm run tauri build'
else
    npm install --no-fund --no-audit
    RUSTFLAGS="${RUSTFLAGS:-} -C target-cpu=native" npm run tauri build
fi
popd >/dev/null
local_bin="$PROJECT_ROOT/src/src-tauri/target/release/tauri-app"
[[ -f $local_bin ]] || local_bin="$PROJECT_ROOT/src/src-tauri/target/release/greenboost-gaming"
install -m 0755 "$local_bin" "$APP_LIB_DIR/greenboost-gaming-gui"

# Install the gb_gaming/ backend package. It is imported at runtime by the
# GreenBoost Proton wrapper (global_settings, nvml_control) and consumed by
# the fan daemon, so it is not optional. It used to be gated behind the
# (now removed) GTK4 branch, which left it uninstalled on every default
# install and made the Proton wrapper's `from gb_gaming import
# global_settings` fail every time.
if [[ -d "$PROJECT_ROOT/gb_gaming" ]]; then
    install -d "$APP_LIB_DIR/gb_gaming"
    for f in "$PROJECT_ROOT"/gb_gaming/*.py; do
        install -m 0644 "$f" "$APP_LIB_DIR/gb_gaming/$(basename "$f")"
    done
    ok "gb_gaming backend modules installed"
fi

# ── install: runtime mirror into $HOME (Pressure Vessel containment) ──
# GreenBoost Proton runs inside Steam's pressure-vessel sandbox, which
# bind-mounts $HOME but not /usr/local. Every artifact the wrapper probes
# at runtime (Vulkan layer .so + manifest, OpenGL interposer, gb_gaming
# Python package) must therefore also exist under $HOME, not just under
# $LIBDIR. Confirmed live 2026-08-07: without this mirror the wrapper logs
# "libVkLayer_greenboost.so not found", "libgb_gl.so not found", and
# "global_settings unavailable: No module named 'gb_gaming'" simultaneously
# on every game launch, despite all three being correctly installed system-wide.
heading "Mirroring runtime artifacts into \$HOME (Steam sandbox visibility)"

if [[ -n "$_REAL_USER" && -n "$_REAL_HOME" && -d "$_REAL_HOME" ]]; then
    _USER_LIB_DIR="$_REAL_HOME/.local/lib"
    _USER_GB_GAMING_DIR="$_REAL_HOME/.local/lib/greenboost-gaming/gb_gaming"
    _USER_VK_DIR="$_REAL_HOME/.local/share/vulkan/implicit_layer.d"

    install -d -o "$_REAL_USER" "$_USER_LIB_DIR" "$_USER_VK_DIR" "$_USER_GB_GAMING_DIR"

    install -m 0755 -o "$_REAL_USER" \
        "$LIBDIR/libVkLayer_greenboost.so" "$_USER_LIB_DIR/libVkLayer_greenboost.so"

    if [[ -f "$LIBDIR/libgb_gl.so" ]]; then
        install -m 0755 -o "$_REAL_USER" \
            "$LIBDIR/libgb_gl.so" "$_USER_LIB_DIR/libgb_gl.so"
    fi

    if [[ -d "$PROJECT_ROOT/gb_gaming" ]]; then
        # Prune mirrored .py files that no longer exist in the source dir ,
        # otherwise a module renamed/removed from gb_gaming/ keeps ghosting
        # in $HOME forever, importable by the Proton wrapper's sys.path
        # insert even though it's gone from the repo. Same staleness class
        # as the Vulkan/GL .so fix above, just for stray files instead of
        # missed rebuilds.
        if [[ -d "$_USER_GB_GAMING_DIR" ]]; then
            for existing in "$_USER_GB_GAMING_DIR"/*.py; do
                [[ -e "$existing" ]] || continue
                [[ -f "$PROJECT_ROOT/gb_gaming/$(basename "$existing")" ]] || \
                    rm -f "$existing"
            done
        fi
        for f in "$PROJECT_ROOT"/gb_gaming/*.py; do
            install -m 0644 -o "$_REAL_USER" "$f" \
                "$_USER_GB_GAMING_DIR/$(basename "$f")"
        done
    fi

    # The home manifest needs its OWN library_path , copying the system
    # manifest verbatim (the previous approach) pointed at $LIBDIR, which
    # the sandboxed Vulkan loader can't see either.
    write_layer_manifest "$_USER_VK_DIR/VkLayer_greenboost.json" \
                          "$_USER_LIB_DIR/libVkLayer_greenboost.so"
    chown "$_REAL_USER" "$_USER_VK_DIR/VkLayer_greenboost.json"

    ok "runtime artifacts mirrored to $_USER_LIB_DIR and $_USER_VK_DIR"
else
    warn "could not resolve invoking user's \$HOME (run via sudo, not as root directly) , skipping \$HOME mirror; GreenBoost Proton will not find the Vulkan layer, OpenGL layer, or gb_gaming inside Steam's sandbox"
fi

# ── install: Steam compatibility-tool deployment ───────────────────────
# Steam does NOT run greenboost_proton/proton from this repo directly , it
# runs a deployed COPY under compatibilitytools.d/greenboost-proton/, put
# there by the separate greenboost_proton/install.sh (user-level, no root:
# it also downloads dxvk-gplasync and stages NIS shaders into $HOME).
# Confirmed live 2026-08-07: that second installer went un-run for days,
# so every fix landed in this repo's proton wrapper while Steam kept
# launching a stale copy , no error, no warning, just silently-unfixed
# behavior on every real game launch. Root-caused and fixed at the
# source: `sudo ./install.sh` must always re-run it too, so the deployed
# copy can never again drift from this repo's actual code.
heading "Deploying Steam compatibility tool (greenboost_proton/install.sh)"

GB_PROTON_INSTALLER="$PROJECT_ROOT/greenboost_proton/install.sh"
if [[ -x "$GB_PROTON_INSTALLER" ]]; then
    if [[ -n "$_REAL_USER" ]]; then
        if sudo -u "$_REAL_USER" -H "$GB_PROTON_INSTALLER"; then
            ok "Steam compatibility tool deployed/refreshed for $_REAL_USER"
        else
            warn "greenboost_proton/install.sh failed , Steam is still using the PREVIOUS GreenBoost Proton, not the one in this checkout"
            cat >&2 <<'EOF'

  What that costs you: the rest of this install succeeded, but your games
  will keep launching through whatever GreenBoost Proton was deployed last
  time. Any wrapper fix in this checkout will not reach them.

  Nothing is broken , Steam and your existing games are untouched, and the
  previous wrapper still works exactly as it did before this run.

  The output above says why it refused. The usual cause is Python syntax the
  Steam runtime cannot parse, which that installer now checks for on purpose
  rather than shipping and failing at launch time. Fix what it named, then:

    ./greenboost_proton/install.sh

EOF
        fi
    else
        warn "could not resolve invoking user , run 'greenboost_proton/install.sh' yourself (as your normal user, no sudo) to deploy/refresh the Steam compatibility tool"
    fi
else
    warn "greenboost_proton/install.sh not found or not executable , skipping compatibility-tool deployment"
fi

# ── install: fan daemon (systemd user unit) ───────────────────────────
heading "Installing fan-curve daemon"

FAN_UNIT_SRC="$PROJECT_ROOT/scripts/gb-gaming-fan-daemon.service"
if [[ -f "$FAN_UNIT_SRC" ]]; then
    install -d "$(dirname "$FAN_UNIT_DST")"
    install -m 0644 "$FAN_UNIT_SRC" "$FAN_UNIT_DST"
    ok "fan-curve daemon unit installed at $FAN_UNIT_DST"
    cat <<EOF
    Enable per-user with:
        ${C_CYA}systemctl --user daemon-reload
        systemctl --user enable --now gb-gaming-fan-daemon${C_OFF}

    The daemon idles until you set an active profile in the GUI
    (Profile → Profiles → Load).
EOF
else
    warn "fan-daemon unit not found at $FAN_UNIT_SRC , skipping"
fi

# ── install: polkit rule for passwordless NVML fan control (A9) ────────
heading "Installing NVML fan control polkit rule"

POLKIT_RULE_SRC="$PROJECT_ROOT/scripts/60-greenboost-fan.rules"
if [[ -f "$POLKIT_RULE_SRC" ]]; then
    install -d "$(dirname "$POLKIT_RULE_DST")"
    install -m 0644 "$POLKIT_RULE_SRC" "$POLKIT_RULE_DST"
    ok "polkit rule installed , fan control no longer requires a password prompt"
else
    warn "polkit rule not found at $POLKIT_RULE_SRC , skipping"
fi

# ── install: sudoers rule for non-interactive fan daemon (A9) ──────────
heading "Installing NVML fan control sudoers rule"

SUDOERS_SRC="$PROJECT_ROOT/scripts/60-greenboost-fan.sudoers"
if [[ -f "$SUDOERS_SRC" ]]; then
    # Validate the file before installing (visudo -c)
    if visudo -cf "$SUDOERS_SRC" 2>/dev/null; then
        install -d "$(dirname "$SUDOERS_DST")"
        install -m 0440 "$SUDOERS_SRC" "$SUDOERS_DST"
        ok "sudoers rule installed , fan daemon can set fan speed without a password"
    else
        warn "sudoers file failed validation , skipping (fan daemon will use pkexec)"
    fi
else
    warn "sudoers rule not found at $SUDOERS_SRC , skipping"
fi

# ── install: gaming_mode group-write access (A1) ───────────────────────
# greenboost.ko ships gaming_mode as root-only (0644). GreenBoost Proton
# writes it as the invoking user to signal "a game is active" to the
# kernel module; without this it fails silently every time. Group-scoped,
# never world-writable , see scripts/99-greenboost-gaming.rules.
heading "Granting the 'greenboost' group write access to gaming_mode"

if ! getent group greenboost >/dev/null 2>&1; then
    groupadd -r greenboost
    ok "created 'greenboost' group"
fi
if [[ -n "$_REAL_USER" ]] && ! id -nG "$_REAL_USER" 2>/dev/null | grep -qw greenboost; then
    usermod -aG greenboost "$_REAL_USER"
    ok "added $_REAL_USER to the 'greenboost' group (re-login required to take effect)"
fi

UDEV_RULE_SRC="$PROJECT_ROOT/scripts/99-greenboost-gaming.rules"
TMPFILES_SRC="$PROJECT_ROOT/scripts/greenboost-gaming.tmpfiles.conf"

if [[ -f "$UDEV_RULE_SRC" ]]; then
    install -d "$(dirname "$UDEV_RULE_DST")"
    install -m 0644 "$UDEV_RULE_SRC" "$UDEV_RULE_DST"
    ok "udev rule installed , future module loads get gaming_mode group-writable"
else
    warn "udev rule not found at $UDEV_RULE_SRC , skipping"
fi

if [[ -f "$TMPFILES_SRC" ]]; then
    install -d "$(dirname "$TMPFILES_DST")"
    install -m 0644 "$TMPFILES_SRC" "$TMPFILES_DST"
    ok "tmpfiles rule installed , covers greenboost.ko already loaded at boot"
else
    warn "tmpfiles rule not found at $TMPFILES_SRC , skipping"
fi

# Initramfs exclusion for the rule we just installed (see INITRAMFS_HOOK_DST).
if [[ -d "${DESTDIR}/etc/initramfs-tools/hooks" ]] || [[ -d "${DESTDIR}/etc/initramfs-tools" ]]; then
    install -d "$(dirname "$INITRAMFS_HOOK_DST")"
    cat > "$INITRAMFS_HOOK_DST" <<'HOOKEOF'
#!/bin/sh
# Installed by greenboost_gaming/install.sh , see INITRAMFS_HOOK_DST there.
# The tmpfiles rule this removes acts on /sys/module/greenboost/, which does
# not exist in early userspace; leaving it in the image only produces a
# "Failed to resolve group 'greenboost'" failure on every boot.
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "${1:-}" in
    prereqs) prereqs; exit 0 ;;
esac
[ -n "${DESTDIR:-}" ] || exit 0
rm -f "$DESTDIR"/etc/tmpfiles.d/greenboost-gaming.conf \
      "$DESTDIR"/usr/lib/tmpfiles.d/greenboost-gaming.conf 2>/dev/null || true
exit 0
HOOKEOF
    chmod 0755 "$INITRAMFS_HOOK_DST"
    ok "initramfs exclusion installed , the tmpfiles rule stays out of the boot image"
    if [[ -z "$DESTDIR" ]] && command -v update-initramfs >/dev/null 2>&1; then
        update-initramfs -u -k all >/dev/null 2>&1 \
            && ok "initramfs regenerated" \
            || warn "update-initramfs failed , run: sudo update-initramfs -u -k all"
    fi
fi

# Apply both immediately so this takes effect without a reboot.
if [[ -z "$DESTDIR" ]]; then
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload 2>/dev/null || true
    fi
    if command -v systemd-tmpfiles >/dev/null 2>&1; then
        systemd-tmpfiles --create "$TMPFILES_DST" 2>/dev/null || true
    fi
    if [[ -e /sys/module/greenboost/parameters/gaming_mode ]]; then
        chgrp greenboost /sys/module/greenboost/parameters/gaming_mode 2>/dev/null || true
        chmod 0664 /sys/module/greenboost/parameters/gaming_mode 2>/dev/null || true
        ok "gaming_mode is now group-writable (applied immediately, no reboot needed)"
    fi
fi

# ── install: per-game optimization profiles (A8) ─────────────────────
heading "Installing per-game optimization profiles"

PROFILES_SRC="$PROJECT_ROOT/profiles/per-game"
if [[ -d "$PROFILES_SRC" ]]; then
    install -d "$PROFILES_DST"
    for f in "$PROFILES_SRC"/*.json; do
        [[ -f "$f" ]] || continue
        install -m 0644 "$f" "$PROFILES_DST/$(basename "$f")"
    done
    ok "per-game profiles installed at $PROFILES_DST"
fi

# ── install: launcher script ──────────────────────────────────────────
heading "Installing launcher"

install -d "$BINDIR"
cat > "$BINDIR/$APP_NAME" <<EOF
#!/usr/bin/env bash
# GreenBoost Gaming Suite launcher
# Sets the env that enables the Vulkan layer + starts the GUI.
export GREENBOOST_VULKAN=1
export GREENBOOST_ACTIVE=1

# GDK / Wayland niceties
export GDK_BACKEND=\${GDK_BACKEND:-wayland,x11}

# Enable Proton Wayland backend when the session is Wayland
_XDG="\${XDG_SESSION_TYPE:-}"
if [[ "\$_XDG" == "wayland" ]] || [[ -n "\${WAYLAND_DISPLAY:-}" ]]; then
    export PROTON_ENABLE_WAYLAND=\${PROTON_ENABLE_WAYLAND:-1}
fi

APP_LIB_DIR="$LIBDIR/greenboost-gaming"

# Make the gb_gaming backend package importable from the install dir , the
# Proton wrapper and the fan daemon both import it, and so will the CLI.
export PYTHONPATH="\$APP_LIB_DIR:\${PYTHONPATH:-}"

exec "\$APP_LIB_DIR/greenboost-gaming-gui" "\$@"
EOF
chmod 0755 "$BINDIR/$APP_NAME"
ok "launcher installed at $BINDIR/$APP_NAME"

# ── install: .desktop + icon (GNOME App Grid) ─────────────────────────
heading "Installing desktop entry"

install -d "$DESKTOP_DIR" "$ICON_DIR" "$ICON_FALLBACK_DIR"

# Pick the best icon we have shipped.
ICON_SRC=""
if   [[ -f "$PROJECT_ROOT/icon.svg" ]]; then ICON_SRC="$PROJECT_ROOT/icon.svg"
elif [[ -f "$PROJECT_ROOT/src/src-tauri/icons/128x128.png" ]]; then
     ICON_SRC="$PROJECT_ROOT/src/src-tauri/icons/128x128.png"
elif [[ -f "$PROJECT_ROOT/src/src-tauri/icons/Square142x142Logo.png" ]]; then
     ICON_SRC="$PROJECT_ROOT/src/src-tauri/icons/Square142x142Logo.png"
fi

ICON_NAME="$APP_NAME"
if [[ -n $ICON_SRC ]]; then
    case "$ICON_SRC" in
        *.svg) install -m 0644 "$ICON_SRC" "$ICON_DIR/$APP_NAME.svg" ;;
        *.png) install -m 0644 "$ICON_SRC" "$ICON_FALLBACK_DIR/$APP_NAME.png" ;;
    esac
    ok "icon installed (from ${ICON_SRC##*/})"
else
    warn "no shipped icon found; the app will use the GNOME generic icon"
    ICON_NAME="applications-games"
fi

cat > "$DESKTOP_DIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_DISPLAY_NAME
GenericName=Gaming Optimization Suite
Comment=Manage Steam games, DLSS/FSR libraries, and GPU profile under GreenBoost
Exec=$BINDIR/$APP_NAME %U
Icon=$ICON_NAME
Terminal=false
Categories=Game;Settings;System;
Keywords=greenboost;gaming;steam;proton;dlss;vulkan;vram;gpu;
StartupNotify=true
StartupWMClass=$APP_ID
EOF
ok "desktop entry installed (id: $APP_ID , matches tauri.conf.json's identifier + enableGTKAppId so the dock/taskbar icon resolves correctly)"

# Refresh icon + desktop caches so the app appears immediately.
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -fq /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
fi

# ── done ──────────────────────────────────────────────────────────────
heading "Installation complete"

cat <<EOF
${C_GRN}GreenBoost Gaming Suite is installed.${C_OFF}

  Run from CLI:   ${C_CYA}$APP_NAME${C_OFF}
  Or:             search for "${APP_DISPLAY_NAME}" in the GNOME app grid.

  Vulkan layer:   ${VULKAN_LAYER_DIR}/VkLayer_greenboost.json
                  ${LIBDIR}/libVkLayer_greenboost.so
                  enabled when ${C_CYA}GREENBOOST_VULKAN=1${C_OFF}
                  (the launcher sets it; for raw Steam launches put
                   ${C_DIM}GREENBOOST_VULKAN=1 %command%${C_OFF} in Launch Options)
                  Also mirrored to ~/.local/lib + ~/.local/share/vulkan/
                  implicit_layer.d/ (own manifest, own library_path) since
                  Steam's Pressure Vessel container only bind-mounts \$HOME.

  Uninstall:      ${C_CYA}sudo $0 --uninstall${C_OFF}

Disclaimer: GreenBoost is an independent open-source project and is
not affiliated with, endorsed by, or sponsored by NVIDIA Corporation.
NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.
EOF
