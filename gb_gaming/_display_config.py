#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
GNOME 47+ display configuration via org.gnome.Mutter.DisplayConfig.

Provides two sub-commands:
  get-state   , Print JSON array of DisplayInfo objects (one per monitor).
  apply-mode  <connector> <WxH> <rate>  , Change resolution/refresh rate.
  apply-vrr   <connector> <enable|disable> , Toggle VRR.

Exits 0 on success.
Exits 1 if python3-gi is unavailable (caller can fall back to gdbus text path).
Exits 2+ on D-Bus / mutter errors.

JSON shape emitted by get-state matches the Rust DisplayInfo struct:
  {name, connected, primary, current_mode, current_rate, modes, gsync_compatible, vrr, connector, width_mm, height_mm}
where modes is [{resolution, rates}].
"""
from __future__ import annotations

import json
import sys

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError) as e:
    sys.stderr.write(f"python3-gi unavailable: {e}\n")
    sys.exit(1)

DBUS_NAME   = "org.gnome.Mutter.DisplayConfig"
DBUS_OBJECT = "/org/gnome/Mutter/DisplayConfig"
DBUS_IFACE  = "org.gnome.Mutter.DisplayConfig"

METHOD_VERIFY    = 0
METHOD_TEMPORARY = 1
METHOD_PERSISTENT = 2


def _proxy() -> Gio.DBusProxy:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None,
        DBUS_NAME, DBUS_OBJECT, DBUS_IFACE, None,
    )


def _matches(connector: str, want: str) -> bool:
    if connector == want:
        return True
    def tail(s: str):
        parts = s.split("-")
        if len(parts) < 2:
            return None
        idx = parts[-1]
        kp = parts[:-1]
        if kp and len(kp[-1]) == 1:
            kp = kp[:-1]
        return ("-".join(kp).lower(), idx)
    a, b = tail(connector), tail(want)
    return bool(a and b and a == b)


def _unpack_variant_value(v):
    """Unwrap a GLib.Variant to a Python value."""
    if v is None:
        return None
    t = v.get_type_string()
    if t == 'b':
        return v.get_boolean()
    if t == 's':
        return v.get_string()
    if t == 'i':
        return v.get_int32()
    if t == 'u':
        return v.get_uint32()
    if t == 'd':
        return v.get_double()
    return None


def _props_dict(props_variant) -> dict:
    """Convert a GVariant a{sv} dict to plain Python dict."""
    result = {}
    if props_variant is None:
        return result
    for i in range(props_variant.n_children()):
        entry = props_variant.get_child_value(i)
        key = entry.get_child_value(0).get_string()
        val_variant = entry.get_child_value(1).get_variant()
        result[key] = _unpack_variant_value(val_variant)
    return result


def _current_mode_id(monitors_v, connector: str) -> str | None:
    """Find the mode id currently active on `connector` by scanning the
    monitors array. Needed because a logical monitor's outputs list only
    carries the monitor spec (connector, vendor, product, serial) , not
    a mode id , for any output other than the one actually being changed."""
    for mi in range(monitors_v.n_children()):
        mon = monitors_v.get_child_value(mi)
        ident_v = mon.get_child_value(0)
        conn = ident_v.get_child_value(0).get_string()
        if not _matches(conn, connector):
            continue
        modes_v = mon.get_child_value(1)
        for mdi in range(modes_v.n_children()):
            mode = modes_v.get_child_value(mdi)
            props = _props_dict(mode.get_child_value(6))
            if props.get("is-current"):
                return mode.get_child_value(0).get_string()
    return None


def cmd_get_state() -> int:
    try:
        proxy = _proxy()
    except GLib.Error as e:
        sys.stderr.write(f"D-Bus connect failed: {e.message}\n")
        return 2

    try:
        state = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"GetCurrentState failed: {e.message}\n")
        return 2

    # NOTE: deliberately NOT state.unpack() , that deep-converts the whole
    # reply into native Python types, but every accessor below (.n_children,
    # .get_child_value, .get_string, ...) is a GVariant-only method. Index
    # the top-level tuple's children directly to keep them as Variants.
    serial_v   = state.get_child_value(0)
    monitors_v = state.get_child_value(1)
    logical_v  = state.get_child_value(2)
    _props_v   = state.get_child_value(3)

    # Build sets of primary + enabled connector names from logical_monitors.
    # A monitor is "enabled" (powered on, part of the desktop) iff its
    # connector appears as an output of SOME logical monitor , GNOME
    # represents "off" as a physically-detected monitor (present in
    # `monitors_v` below) with no logical_monitor entry at all, not as an
    # explicit flag.
    primary_connectors: set[str] = set()
    enabled_connectors: set[str] = set()
    n_logical = logical_v.n_children()
    for li in range(n_logical):
        lm = logical_v.get_child_value(li)
        # logical monitor: (iidub a((ssa{sv})) a{sv})
        primary_v = lm.get_child_value(4)
        is_primary = primary_v.get_boolean()
        outputs_v = lm.get_child_value(5)
        for oi in range(outputs_v.n_children()):
            out_v = outputs_v.get_child_value(oi)
            connector_v = out_v.get_child_value(0)
            connector = connector_v.get_string()
            enabled_connectors.add(connector)
            if is_primary:
                primary_connectors.add(connector)

    result = []
    n_mons = monitors_v.n_children()

    for mi in range(n_mons):
        mon = monitors_v.get_child_value(mi)
        # monitor: ((ssss) a(siid da@da{sv}) a{sv})
        ident_v  = mon.get_child_value(0)
        modes_v  = mon.get_child_value(1)
        _mprops_v = mon.get_child_value(2)

        connector = ident_v.get_child_value(0).get_string()

        current_w = current_h = 0
        current_rate = 0.0
        has_vrr = False
        vrr_enabled = False

        # mode_map: (w,h) -> list of (rate, rrm)
        mode_map: dict[tuple[int, int], list[tuple[float, str]]] = {}

        n_modes = modes_v.n_children()
        for mdi in range(n_modes):
            mode = modes_v.get_child_value(mdi)
            # (s i i d d @ad a{sv})
            _mode_id = mode.get_child_value(0).get_string()
            w        = mode.get_child_value(1).get_int32()
            h        = mode.get_child_value(2).get_int32()
            rate     = mode.get_child_value(3).get_double()
            props    = _props_dict(mode.get_child_value(6))

            is_current = bool(props.get("is-current", False))
            rrm        = props.get("refresh-rate-mode") or "fixed"

            if rrm == "variable":
                has_vrr = True
                if is_current:
                    vrr_enabled = True

            if is_current and rrm == "fixed":
                current_w, current_h, current_rate = w, h, rate
            elif is_current and rrm == "variable":
                # VRR mode is current , record dimensions but expose fixed rate
                if current_w == 0:
                    current_w, current_h, current_rate = w, h, rate

            key = (w, h)
            if key not in mode_map:
                mode_map[key] = []
            if rrm == "fixed":
                mode_map[key].append((rate, rrm))

        # Build modes list sorted by resolution (descending pixels) then rate.
        modes_list = []
        for (w, h), rate_entries in sorted(mode_map.items(),
                                           key=lambda kv: kv[0][0] * kv[0][1],
                                           reverse=True):
            rates = sorted({round(r, 3) for (r, _) in rate_entries}, reverse=True)
            if rates:
                modes_list.append({"resolution": f"{w}x{h}", "rates": rates})

        result.append({
            "name":            connector,
            "connected":       True,
            "enabled":         connector in enabled_connectors,
            "primary":         connector in primary_connectors,
            "current_mode":    f"{current_w}x{current_h}" if current_w else "",
            "current_rate":    round(current_rate, 3),
            "modes":           modes_list,
            "gsync_compatible": has_vrr,
            "vrr":             vrr_enabled,
            "connector":       connector,
            "width_mm":        0,
            "height_mm":       0,
        })

    print(json.dumps(result))
    return 0


def cmd_apply_mode(connector: str, resolution: str, rate: float) -> int:
    parts = resolution.split("x")
    if len(parts) != 2:
        sys.stderr.write(f"invalid resolution '{resolution}', expected WxH\n")
        return 3
    try:
        target_w, target_h = int(parts[0]), int(parts[1])
    except ValueError:
        sys.stderr.write(f"invalid resolution '{resolution}'\n")
        return 3

    try:
        proxy = _proxy()
    except GLib.Error as e:
        sys.stderr.write(f"D-Bus connect failed: {e.message}\n")
        return 2

    try:
        state = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"GetCurrentState failed: {e.message}\n")
        return 2

    # See cmd_get_state() for why this indexes get_child_value() directly
    # instead of calling .unpack() , the rest of this function walks
    # GVariant accessors, which .unpack() would have already converted away.
    serial_v   = state.get_child_value(0)
    monitors_v = state.get_child_value(1)
    logical_v  = state.get_child_value(2)
    serial = serial_v.get_uint32()

    new_mode_id: str | None = None
    n_mons = monitors_v.n_children()
    for mi in range(n_mons):
        mon = monitors_v.get_child_value(mi)
        ident_v = mon.get_child_value(0)
        conn = ident_v.get_child_value(0).get_string()
        if not _matches(conn, connector):
            continue
        modes_v = mon.get_child_value(1)
        for mdi in range(modes_v.n_children()):
            mode = modes_v.get_child_value(mdi)
            mode_id = mode.get_child_value(0).get_string()
            w       = mode.get_child_value(1).get_int32()
            h       = mode.get_child_value(2).get_int32()
            r       = mode.get_child_value(3).get_double()
            props   = _props_dict(mode.get_child_value(6))
            rrm     = props.get("refresh-rate-mode") or "fixed"
            if w == target_w and h == target_h and abs(r - rate) < 0.5 and rrm == "fixed":
                new_mode_id = mode_id
                break
        if new_mode_id:
            break
    else:
        sys.stderr.write(
            f"no monitor matched connector '{connector}'\n")
        return 4

    if not new_mode_id:
        sys.stderr.write(
            f"mode {resolution}@{rate:.3f} not found for {connector}\n")
        return 5

    # Build new logical monitors list preserving layout, swapping only target mode.
    new_logical: list = []
    n_logical = logical_v.n_children()
    for li in range(n_logical):
        lm = logical_v.get_child_value(li)
        x         = lm.get_child_value(0).get_int32()
        y         = lm.get_child_value(1).get_int32()
        scale     = lm.get_child_value(2).get_double()
        transform = lm.get_child_value(3).get_uint32()
        primary   = lm.get_child_value(4).get_boolean()
        outputs_v = lm.get_child_value(5)

        new_outputs: list = []
        for oi in range(outputs_v.n_children()):
            out_v        = outputs_v.get_child_value(oi)
            # GetCurrentState's logical-monitor outputs entries are the
            # monitor-spec tuple (ssss) = (connector, vendor, product,
            # serial) , there is NO mode id here. Index 1 is the vendor
            # string (e.g. "PHL"), not a mode; using it as one is exactly
            # what made mutter reject the whole request with "Invalid mode
            # 'PHL' specified". To preserve an untouched output's current
            # mode, look it up in the monitors array instead (below).
            out_conn     = out_v.get_child_value(0).get_string()
            if _matches(out_conn, connector):
                new_outputs.append(
                    GLib.Variant("(ssa{sv})", (out_conn, new_mode_id, {})))
            else:
                cur_mode = _current_mode_id(monitors_v, out_conn)
                if cur_mode is None:
                    sys.stderr.write(f"could not find current mode for {out_conn}\n")
                    return 7
                new_outputs.append(
                    GLib.Variant("(ssa{sv})", (out_conn, cur_mode, {})))

        new_logical.append(GLib.Variant(
            "(iiduba(ssa{sv}))",
            (x, y, scale, transform, primary, new_outputs)))

    config = GLib.Variant(
        "(uua(iiduba(ssa{sv}))a{sv})",
        (serial, METHOD_PERSISTENT, new_logical, {}))
    try:
        proxy.call_sync("ApplyMonitorsConfig", config, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"ApplyMonitorsConfig failed: {e.message}\n")
        return 6

    print(f"Display {connector} set to {resolution} @ {rate:.0f} Hz")
    return 0


def cmd_set_enabled(connector: str, enable: bool) -> int:
    """Power a monitor on/off via ApplyMonitorsConfig , the primary GNOME
    47+ path. gnome-monitor-config (the Rust caller's fallback) isn't
    installed by default on most systems, same gap as scale.

    GNOME has no explicit "enabled" flag: a monitor is on iff its connector
    is an output of some logical_monitor, off iff it's absent from
    logical_monitors entirely (still present in the monitors array as
    physically detected). Disabling = drop the connector from whichever
    logical monitor lists it (dropping the whole logical monitor if that
    was its only output). Enabling = synthesize a new logical monitor
    placed to the right of the current layout, using the connector's
    preferred mode (falling back to its first mode)."""
    try:
        proxy = _proxy()
    except GLib.Error as e:
        sys.stderr.write(f"D-Bus connect failed: {e.message}\n")
        return 2

    try:
        state = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"GetCurrentState failed: {e.message}\n")
        return 2

    serial_v   = state.get_child_value(0)
    monitors_v = state.get_child_value(1)
    logical_v  = state.get_child_value(2)
    serial = serial_v.get_uint32()

    new_logical: list = []
    had_primary = False
    max_right_edge = 0
    already_enabled = False  # connector already has a logical monitor entry

    n_logical = logical_v.n_children()
    for li in range(n_logical):
        lm = logical_v.get_child_value(li)
        x         = lm.get_child_value(0).get_int32()
        y         = lm.get_child_value(1).get_int32()
        scale     = lm.get_child_value(2).get_double()
        transform = lm.get_child_value(3).get_uint32()
        primary   = lm.get_child_value(4).get_boolean()
        outputs_v = lm.get_child_value(5)

        outputs: list = []
        for oi in range(outputs_v.n_children()):
            out_v    = outputs_v.get_child_value(oi)
            out_conn = out_v.get_child_value(0).get_string()
            if _matches(out_conn, connector):
                if not enable:
                    continue  # drop this output , disabling it
                already_enabled = True  # already present; loop preserves it below
            cur_mode = _current_mode_id(monitors_v, out_conn)
            if cur_mode is None:
                sys.stderr.write(f"could not find current mode for {out_conn}\n")
                return 7
            outputs.append(GLib.Variant("(ssa{sv})", (out_conn, cur_mode, {})))

        if not outputs:
            continue  # this logical monitor only had the connector we dropped
        if primary:
            had_primary = True
        max_right_edge = max(max_right_edge, x + _logical_width_px(monitors_v, outputs_v))
        new_logical.append(GLib.Variant(
            "(iiduba(ssa{sv}))",
            (x, y, scale, transform, primary, outputs)))

    if enable and not already_enabled:
        # Only synthesize a new logical monitor when the connector didn't
        # already have one , restore_all_displays_impl calls this with
        # enable=True for EVERY display, not just disabled ones, so this
        # must be a no-op (not a duplicate entry) for already-enabled ones.
        mode_id, mode_w, mode_h = _preferred_mode(monitors_v, connector)
        if mode_id is None:
            sys.stderr.write(f"no monitor matched connector '{connector}'\n")
            return 4
        new_logical.append(GLib.Variant(
            "(iiduba(ssa{sv}))",
            (max_right_edge, 0, 1.0, 0, not had_primary,
             [GLib.Variant("(ssa{sv})", (connector, mode_id, {}))])))
    elif (not enable) and len(new_logical) == n_logical:
        # `enable=False` but nothing was actually dropped , connector wasn't
        # in any logical monitor to begin with (already off, or unknown).
        sys.stderr.write(f"'{connector}' is not currently enabled\n")
        return 4

    if not new_logical:
        sys.stderr.write("refusing to disable the only remaining display\n")
        return 8

    config = GLib.Variant(
        "(uua(iiduba(ssa{sv}))a{sv})",
        (serial, METHOD_PERSISTENT, new_logical, {}))
    try:
        proxy.call_sync("ApplyMonitorsConfig", config, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"ApplyMonitorsConfig failed: {e.message}\n")
        return 6

    print(f"Display {connector} {'enabled' if enable else 'disabled'}")
    return 0


def _preferred_mode(monitors_v, connector: str):
    """(mode_id, width, height) for `connector`'s preferred mode, falling
    back to its first available mode. None, 0, 0 if connector not found."""
    for mi in range(monitors_v.n_children()):
        mon = monitors_v.get_child_value(mi)
        ident_v = mon.get_child_value(0)
        conn = ident_v.get_child_value(0).get_string()
        if not _matches(conn, connector):
            continue
        modes_v = mon.get_child_value(1)
        first = None
        for mdi in range(modes_v.n_children()):
            mode = modes_v.get_child_value(mdi)
            mode_id = mode.get_child_value(0).get_string()
            w = mode.get_child_value(1).get_int32()
            h = mode.get_child_value(2).get_int32()
            props = _props_dict(mode.get_child_value(6))
            if first is None:
                first = (mode_id, w, h)
            if props.get("is-preferred"):
                return (mode_id, w, h)
        return first if first else (None, 0, 0)
    return (None, 0, 0)


def _logical_width_px(monitors_v, outputs_v) -> int:
    """Width (px) of a logical monitor, from its first output's current
    mode , used to place a newly-enabled monitor to the right without
    overlapping."""
    if outputs_v.n_children() == 0:
        return 0
    out_v = outputs_v.get_child_value(0)
    conn = out_v.get_child_value(0).get_string()
    for mi in range(monitors_v.n_children()):
        mon = monitors_v.get_child_value(mi)
        ident_v = mon.get_child_value(0)
        if not _matches(ident_v.get_child_value(0).get_string(), conn):
            continue
        modes_v = mon.get_child_value(1)
        for mdi in range(modes_v.n_children()):
            mode = modes_v.get_child_value(mdi)
            props = _props_dict(mode.get_child_value(6))
            if props.get("is-current"):
                return mode.get_child_value(1).get_int32()
    return 1920  # fallback , never blocks enabling, just a placement guess


def cmd_apply_vrr(connector: str, enable: bool) -> int:
    # Delegate to _vrr_gnome which already implements this correctly.
    from gb_gaming import _vrr_gnome  # noqa: PLC0415
    return _vrr_gnome.set_vrr(connector, enable)


def cmd_apply_scale(connector: str, percent: int) -> int:
    """Set a logical monitor's scale via the same ApplyMonitorsConfig path
    used for mode/VRR changes. This is the primary path on GNOME 47+ ,
    gnome-monitor-config is a separate CLI tool that isn't installed by
    default on most systems, so the Rust caller's fallback to it silently
    does nothing on a typical GNOME Wayland box."""
    try:
        proxy = _proxy()
    except GLib.Error as e:
        sys.stderr.write(f"D-Bus connect failed: {e.message}\n")
        return 2

    try:
        state = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"GetCurrentState failed: {e.message}\n")
        return 2

    serial_v   = state.get_child_value(0)
    monitors_v = state.get_child_value(1)
    logical_v  = state.get_child_value(2)
    serial = serial_v.get_uint32()
    new_scale = percent / 100.0

    found = False
    new_logical: list = []
    n_logical = logical_v.n_children()
    for li in range(n_logical):
        lm = logical_v.get_child_value(li)
        x         = lm.get_child_value(0).get_int32()
        y         = lm.get_child_value(1).get_int32()
        scale     = lm.get_child_value(2).get_double()
        transform = lm.get_child_value(3).get_uint32()
        primary   = lm.get_child_value(4).get_boolean()
        outputs_v = lm.get_child_value(5)

        is_target = False
        outputs: list = []
        for oi in range(outputs_v.n_children()):
            out_v    = outputs_v.get_child_value(oi)
            out_conn = out_v.get_child_value(0).get_string()
            if _matches(out_conn, connector):
                is_target = True
            cur_mode = _current_mode_id(monitors_v, out_conn)
            if cur_mode is None:
                sys.stderr.write(f"could not find current mode for {out_conn}\n")
                return 7
            outputs.append(GLib.Variant("(ssa{sv})", (out_conn, cur_mode, {})))

        if is_target:
            found = True
        new_logical.append(GLib.Variant(
            "(iiduba(ssa{sv}))",
            (x, y, new_scale if is_target else scale, transform, primary, outputs)))

    if not found:
        sys.stderr.write(f"no monitor matched connector '{connector}'\n")
        return 4

    config = GLib.Variant(
        "(uua(iiduba(ssa{sv}))a{sv})",
        (serial, METHOD_PERSISTENT, new_logical, {}))
    try:
        proxy.call_sync("ApplyMonitorsConfig", config, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        sys.stderr.write(f"ApplyMonitorsConfig failed: {e.message}\n")
        return 6

    print(f"Display {connector} scaled to {percent}%")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: _display_config.py <get-state|apply-mode|apply-vrr> ...\n")
        return 1

    cmd = args[0]

    if cmd == "get-state":
        return cmd_get_state()

    if cmd == "apply-mode":
        if len(args) < 4:
            sys.stderr.write("usage: apply-mode <connector> <WxH> <rate>\n")
            return 1
        return cmd_apply_mode(args[1], args[2], float(args[3]))

    if cmd == "apply-vrr":
        if len(args) < 3:
            sys.stderr.write("usage: apply-vrr <connector> <enable|disable>\n")
            return 1
        return cmd_apply_vrr(args[1], args[2] == "enable")

    if cmd == "apply-scale":
        if len(args) < 3:
            sys.stderr.write("usage: apply-scale <connector> <percent>\n")
            return 1
        return cmd_apply_scale(args[1], int(args[2]))

    if cmd == "set-enabled":
        if len(args) < 3:
            sys.stderr.write("usage: set-enabled <connector> <enable|disable>\n")
            return 1
        return cmd_set_enabled(args[1], args[2] == "enable")

    sys.stderr.write(f"unknown command: {cmd}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
