#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
GNOME 47+ VRR toggle via org.gnome.Mutter.DisplayConfig.

GNOME ≤46 used `gsettings set org.gnome.mutter experimental-features
['variable-refresh-rate', ...]` to enable VRR globally.  In GNOME 47+ that
flag was removed , VRR is now a per-monitor mode like any other.  Each
monitor exposes both "fixed" and "variable" variants of its modes (same
width × height × refresh-rate, different `refresh-rate-mode` property);
the user picks the variable one to enable VRR for that output.

This script reproduces what gnome-control-center's Display panel does
when the user flips the Variable Refresh Rate switch:

  1.  call DisplayConfig.GetCurrentState to read serial + monitors +
      logical-monitors
  2.  find the target output by its connector name (HDMI-1, DP-3, …)
  3.  find an alternative mode for that output with the same dimensions
      and refresh rate as the current one but `refresh-rate-mode` set to
      "variable" (or "fixed" when disabling)
  4.  call DisplayConfig.ApplyMonitorsConfig with method=2 (PERSISTENT)
      and a logical_monitors array that mirrors the current layout but
      with the target output swapped to the new mode id

Usage:
    python3 -m gb_gaming._vrr_gnome --connector DP-3 --enable
    python3 -m gb_gaming._vrr_gnome --connector DP-3 --disable

Exits 0 on success, non-zero on failure with a human-readable message on
stderr.  Stdout receives a one-line confirmation message that the caller
can surface to the user.
"""
from __future__ import annotations

import argparse
import sys

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError) as e:
    sys.stderr.write(f"python3-gi (PyGObject) is required: {e}\n")
    sys.exit(1)


DBUS_NAME   = "org.gnome.Mutter.DisplayConfig"
DBUS_OBJECT = "/org/gnome/Mutter/DisplayConfig"
DBUS_IFACE  = "org.gnome.Mutter.DisplayConfig"

# ApplyMonitorsConfig.method enum.
METHOD_VERIFY    = 0  # only validate; don't apply
METHOD_TEMPORARY = 1  # apply, revert on next session
METHOD_PERSISTENT = 2 # save to monitors.xml


def _proxy() -> Gio.DBusProxy:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None,
        DBUS_NAME, DBUS_OBJECT, DBUS_IFACE, None,
    )


def _matches_connector(connector: str, want: str) -> bool:
    """xrandr-style names vary slightly from mutter's connector strings
    (DP-1 vs DP-3, HDMI-1 vs HDMI-A-1).  Match by full string OR by the
    trailing numeric index after the connector type."""
    if connector == want:
        return True
    # Normalise: drop the "kind-letter-index" tail down to "kind-index".
    def tail(s: str) -> tuple[str, str] | None:
        parts = s.split("-")
        if len(parts) < 2:
            return None
        idx = parts[-1]
        kind_parts = parts[:-1]
        # Drop single-letter sub-index (the "-A" in "HDMI-A-1").
        if kind_parts and len(kind_parts[-1]) == 1:
            kind_parts = kind_parts[:-1]
        return ("-".join(kind_parts).lower(), idx)
    a, b = tail(connector), tail(want)
    return bool(a and b and a == b)


def _current_mode_by_connector(monitors) -> dict:
    """Map connector -> currently active mode id, from the monitors array.

    Needed because a logical monitor's `outputs` entries are the monitor
    spec tuple (connector, vendor, product, serial) , not a mode id , for
    any output other than the one actually being toggled."""
    result: dict = {}
    for mon in monitors:
        ident, modes, _mprops = mon
        connector_name, _vendor, _product, _serial = ident
        for mode in modes:
            mode_id, _w, _h, _rate, _pref, _scales, props = mode
            if props.get("is-current"):
                result[connector_name] = mode_id
                break
    return result


def set_vrr(connector: str, enable: bool) -> int:
    target_mode = "variable" if enable else "fixed"
    proxy = _proxy()

    # GetCurrentState() -> (u serial, monitors, logical_monitors, properties)
    state = proxy.call_sync(
        "GetCurrentState", None,
        Gio.DBusCallFlags.NONE, -1, None,
    )
    serial, monitors, logical_monitors, _props = state.unpack()

    # Find the target monitor + the new mode id to use.
    new_mode_id = None
    current_mode_id = None
    for mon in monitors:
        ident, modes, _mprops = mon
        connector_name, _vendor, _product, _serial = ident
        if not _matches_connector(connector_name, connector):
            continue

        # Find the currently-active mode (it has 'is-current': True).
        cur_w = cur_h = 0
        cur_rate = 0.0
        for mode in modes:
            mode_id, w, h, rate, _pref, _scales, props = mode
            if props.get("is-current"):
                current_mode_id = mode_id
                cur_w, cur_h, cur_rate = w, h, rate
                break

        # If we couldn't identify the current mode, abort early , applying
        # a config without anchoring to the user's current resolution risks
        # bumping the desktop into something unexpected.
        if not current_mode_id:
            sys.stderr.write(
                f"could not find current mode for {connector_name}\n")
            return 2

        # Find a candidate mode that matches dimensions + rate but uses
        # the requested refresh-rate-mode.  Tolerate tiny rate jitter
        # (mutter reports floats; some panels report 59.94, 60.0, etc).
        for mode in modes:
            mode_id, w, h, rate, _pref, _scales, props = mode
            if w != cur_w or h != cur_h:
                continue
            if abs(rate - cur_rate) > 0.5:
                continue
            rrm = props.get("refresh-rate-mode") or "fixed"
            if rrm == target_mode:
                new_mode_id = mode_id
                break

        if not new_mode_id:
            sys.stderr.write(
                f"{connector_name} does not advertise a '{target_mode}' "
                f"variant of the current mode ({cur_w}x{cur_h}@{cur_rate:.2f}).  "
                f"This typically means the monitor / driver / cable doesn't "
                f"support VRR , verify with: cat /sys/class/drm/card0-*/vrr_capable\n")
            return 3
        break
    else:
        sys.stderr.write(f"no monitor matched connector '{connector}'\n")
        return 4

    # Build a new logical_monitors config that preserves every output's
    # current geometry but swaps the target's mode.
    current_by_connector = _current_mode_by_connector(monitors)
    new_logical: list = []
    for lm in logical_monitors:
        x, y, scale, transform, primary, outputs, _lprops = lm
        new_outputs: list = []
        for out in outputs:
            # (connector, vendor, product, serial) , see
            # _current_mode_by_connector's docstring for why this isn't
            # (connector, mode_id, properties).
            out_connector, _vendor, _product, _serial = out
            if _matches_connector(out_connector, connector):
                # Replace the mode id for the target output only.
                new_outputs.append(
                    GLib.Variant("(ssa{sv})", (out_connector, new_mode_id, {})))
            else:
                cur = current_by_connector.get(out_connector)
                if cur is None:
                    sys.stderr.write(f"could not find current mode for {out_connector}\n")
                    return 5
                new_outputs.append(
                    GLib.Variant("(ssa{sv})", (out_connector, cur, {})))
        new_logical.append(GLib.Variant(
            "(iiduba(ssa{sv}))",
            (x, y, scale, transform, primary, new_outputs)))

    config = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})",
                          (serial, METHOD_PERSISTENT, new_logical, {}))
    try:
        proxy.call_sync(
            "ApplyMonitorsConfig", config,
            Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:  # noqa: PERF203
        # Common reasons mutter rejects: serial out of date (race with
        # another panel applying simultaneously), invalid scale, monitor
        # disconnected mid-call.  Surface mutter's own message verbatim.
        sys.stderr.write(f"ApplyMonitorsConfig failed: {e.message}\n")
        return 5

    print(f"VRR {'enabled' if enable else 'disabled'} for {connector} "
          f"via org.gnome.Mutter.DisplayConfig")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--connector", required=True,
                    help="xrandr connector name (e.g. DP-3, HDMI-1)")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--enable",  action="store_true")
    grp.add_argument("--disable", action="store_true")
    args = ap.parse_args()

    try:
        return set_vrr(args.connector, args.enable)
    except GLib.Error as e:
        sys.stderr.write(f"D-Bus call to mutter failed: {e.message}\n")
        sys.stderr.write(
            "Verify GNOME shell is running and your session is active. "
            "GNOME 47+ exposes VRR via DisplayConfig; older versions need "
            "the experimental-features fallback.\n")
        return 6


if __name__ == "__main__":
    sys.exit(main())
