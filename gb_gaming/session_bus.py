# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""
gb_gaming.session_bus , a tiny stdlib D-Bus client for the user session bus.

Why this exists: the Proton wrapper runs inside the Steam Linux Runtime
container, which has no gsettings, gnome-extensions, systemctl or qdbus
(checked against Steam Linux Runtime 4: none of them, no python3-dbus or
python3-gi either). It does have the session bus socket
(DBUS_SESSION_BUS_ADDRESS=unix:path=/run/pressure-vessel/bus) and a plain
python3, so the desktop can be driven directly over D-Bus with nothing but
socket + struct.

Scope is deliberately small: EXTERNAL auth, method calls, property Get/Set,
and a marshaller for the types those need (y b n q i u x t d s o g v a ( ) {}).
No signals, no name ownership, no async. Python 3.9-safe on purpose (the
wrapper's minimum, see tests/test_proton_min_python.py).

A connection is also a lifetime: org.gnome.SessionManager.Inhibit() is
released by the bus when the connection closes, including when the process
is killed, so a crash can never leave the screen blanking disabled.
"""
from __future__ import annotations

import os
import socket
import struct

METHOD_CALL, METHOD_RETURN, ERROR = 1, 2, 3
_ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8,
          "d": 8, "s": 4, "o": 4, "g": 1, "v": 1, "a": 4, "(": 8, "{": 8}
_FMT = {"y": "B", "n": "h", "q": "H", "i": "i", "u": "I", "x": "q", "t": "Q", "d": "d"}


class BusError(Exception):
    def __init__(self, name, message=""):
        super().__init__("%s: %s" % (name, message) if message else name)
        self.name = name


def _one_type(sig, i):
    """Index just past the single complete type starting at sig[i]."""
    c = sig[i]
    if c == "a":
        return _one_type(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        i += 1
        while sig[i] != close:
            i = _one_type(sig, i)
        return i + 1
    return i + 1


def _split(sig):
    out, i = [], 0
    while i < len(sig):
        j = _one_type(sig, i)
        out.append(sig[i:j])
        i = j
    return out


class _Writer:
    def __init__(self):
        self.b = bytearray()

    def align(self, n):
        self.b += b"\0" * ((-len(self.b)) % n)

    def put(self, t, v):
        c = t[0]
        self.align(_ALIGN[c])
        if c in _FMT:
            self.b += struct.pack("<" + _FMT[c], v)
        elif c == "b":
            self.b += struct.pack("<I", 1 if v else 0)
        elif c in "so":
            raw = v.encode()
            self.b += struct.pack("<I", len(raw)) + raw + b"\0"
        elif c == "g":
            raw = v.encode()
            self.b += bytes([len(raw)]) + raw + b"\0"
        elif c == "v":
            sig, val = v
            self.put("g", sig)
            self.put(sig, val)
        elif c == "a":
            elem = t[1:]
            self.align(4)
            at = len(self.b)
            self.b += b"\0\0\0\0"
            self.align(_ALIGN[elem[0]])
            start = len(self.b)
            for item in (v.items() if isinstance(v, dict) else v):
                self.put(elem, item)
            struct.pack_into("<I", self.b, at, len(self.b) - start)
        elif c in "({":
            for sub, item in zip(_split(t[1:-1]), v):
                self.put(sub, item)
        else:
            raise ValueError("cannot marshal type %r" % t)


class _Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def align(self, n):
        self.p += (-self.p) % n

    def get(self, t):
        c = t[0]
        self.align(_ALIGN[c])
        if c in _FMT:
            n = struct.calcsize(_FMT[c])
            (v,) = struct.unpack_from("<" + _FMT[c], self.d, self.p)
            self.p += n
            return v
        if c == "b":
            (v,) = struct.unpack_from("<I", self.d, self.p)
            self.p += 4
            return bool(v)
        if c in "so":
            (n,) = struct.unpack_from("<I", self.d, self.p)
            self.p += 4
            v = self.d[self.p:self.p + n].decode("utf-8", "replace")
            self.p += n + 1
            return v
        if c == "g":
            n = self.d[self.p]
            v = self.d[self.p + 1:self.p + 1 + n].decode()
            self.p += n + 2
            return v
        if c == "v":
            sig = self.get("g")
            return self.get(sig)
        if c == "a":
            elem = t[1:]
            (n,) = struct.unpack_from("<I", self.d, self.p)
            self.p += 4
            self.align(_ALIGN[elem[0]])
            end = self.p + n
            out = []
            while self.p < end:
                out.append(self.get(elem))
            if elem[0] == "{":
                return dict(out)
            return out
        if c in "({":
            vals = [self.get(s) for s in _split(t[1:-1])]
            return tuple(vals)
        raise ValueError("cannot unmarshal type %r" % t)


class SessionBus:
    def __init__(self, address=None, timeout=10.0):
        address = address or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        path = None
        for part in address.split(";"):
            if not part.startswith("unix:"):
                continue
            for kv in part[5:].split(","):
                k, _, v = kv.partition("=")
                if k == "path":
                    path = v
                elif k == "abstract":
                    path = "\0" + v
            if path:
                break
        if not path:
            raise BusError("NoSessionBus", "DBUS_SESSION_BUS_ADDRESS has no unix socket")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.serial = 0
        self.sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
        if not self._line().startswith(b"OK"):
            raise BusError("AuthFailed", "bus refused EXTERNAL authentication")
        self.sock.sendall(b"BEGIN\r\n")
        self.name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "Hello")[0]

    def _line(self):
        buf = b""
        while not buf.endswith(b"\r\n"):
            ch = self.sock.recv(1)
            if not ch:
                raise BusError("Disconnected")
            buf += ch
        return buf

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise BusError("Disconnected")
            buf += chunk
        return buf

    def call(self, dest, path, iface, member, sig="", args=()):
        self.serial += 1
        serial = self.serial
        body = _Writer()
        for t, v in zip(_split(sig), args):
            body.put(t, v)
        fields = _Writer()
        entries = [(1, "o", path), (3, "s", member), (6, "s", dest)]
        if iface:
            entries.append((2, "s", iface))
        if sig:
            entries.append((8, "g", sig))
        for code, t, v in entries:
            fields.align(8)
            fields.b.append(code)
            fields.put("g", t)
            fields.put(t, v)
        head = struct.pack("<cBBBIII", b"l", METHOD_CALL, 0, 1, len(body.b), serial, len(fields.b))
        msg = bytearray(head) + fields.b
        msg += b"\0" * ((-len(msg)) % 8)
        self.sock.sendall(bytes(msg) + bytes(body.b))
        while True:
            hdr = self._recv(16)
            _, mtype, _f, _v, blen, _s, flen = struct.unpack("<cBBBIII", hdr)
            fdata = self._recv(flen + ((-flen) % 8))
            body_bytes = self._recv(blen)
            if mtype not in (METHOD_RETURN, ERROR):
                continue                                  # signal or stray call
            r = _Reader(fdata[:flen])
            reply_to, err, rsig = None, "", ""
            while r.p < flen:
                r.align(8)
                code = r.d[r.p]
                r.p += 1
                vsig = r.get("g")
                val = r.get(vsig)
                if code == 5:
                    reply_to = val
                elif code == 4:
                    err = val
                elif code == 8:
                    rsig = val
            if reply_to != serial:
                continue
            rd = _Reader(body_bytes)
            vals = [rd.get(t) for t in _split(rsig)]
            if mtype == ERROR:
                raise BusError(err, vals[0] if vals else "")
            return vals

    def get_property(self, dest, path, iface, prop):
        return self.call(dest, path, "org.freedesktop.DBus.Properties", "Get",
                         "ss", (iface, prop))[0]

    def set_property(self, dest, path, iface, prop, sig, value):
        self.call(dest, path, "org.freedesktop.DBus.Properties", "Set",
                  "ssv", (iface, prop, (sig, value)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def connect():
    """A SessionBus, or None when there is no reachable session bus."""
    try:
        return SessionBus()
    except (OSError, BusError, ValueError):
        return None
