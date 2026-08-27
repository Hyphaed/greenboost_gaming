# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""The stop message must say WHY, not just that it happened.

Regression for the night of 2026-08-27: FINAL FANTASY VII REBIRTH would not
launch. GreenBoost's own wrapper ran several times, each ending in

    [greenboost-proton] stopping game , 1 process(es) asked to exit, 1 force-killed
    [greenboost-proton] session stopped on request

with zero session telemetry that session (`gb_dataflux` wasn't importable
either, a separate and already-understood limitation , see `_df_emit`'s own
docstring), so there was no way to tell from the log alone whether Steam's
own process supervision, the Suite's "stop game on quit" setting, or the
tray's "Stop game" button sent the SIGTERM. `GB_STOP_REASON` now rides along
on the message `_on_stop` writes; this pins that it stays there and lands on
the same line the counts do, since a session's log is read per-appid,
per-launch (see `game_lifecycle.rs::wrapper_log_tail`), not correlated
against some other field.

Same AST-lift technique as test_proton_helper_gate.py: importing
gb_proton_main.py calls main() at module scope (it launches a game), so the
piece under test is compiled and exec'd on its own instead. `_on_stop` is
ALSO nested one level deeper than that test's targets , it's a closure
inside `_install_stop_handler()`, which itself calls `signal.signal()`.
Calling that for real from a test would install a handler on the test
runner's own process, so the source is dedented and exec'd directly rather
than going through `_install_stop_handler()` at all.
"""
from __future__ import annotations

import ast
import os
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "greenboost_proton" / "gb_proton_main.py"


def _find_nested(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name!r} not found in {WRAPPER}")


class _FakeGl:
    """Stands in for gb_gaming.game_lifecycle. Fixed counts so the test only
    has to check that the reason rides along, not re-verify the tree walk ,
    that's test_game_lifecycle.py's job."""

    @staticmethod
    def terminate_tree(pid, grace=5.0, include_root=False):
        return {"terminated": [111], "killed": [222, 333]}


@pytest.fixture
def on_stop():
    src = WRAPPER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    outer = _find_nested(tree, "_install_stop_handler")
    inner = _find_nested(outer, "_on_stop")
    lines = src.splitlines(True)
    chunk = textwrap.dedent("".join(lines[inner.lineno - 1: inner.end_lineno]))

    ns: dict = {"os": os, "sys": sys, "_lifecycle": lambda: _FakeGl()}
    exec(chunk, ns)                                  # noqa: S102 , our own source
    return ns["_on_stop"]


@pytest.fixture(autouse=True)
def _clean_reason(monkeypatch):
    monkeypatch.delenv("GB_STOP_REASON", raising=False)


def test_default_reason_is_signal(on_stop, capsys):
    """No one set GB_STOP_REASON , e.g. a plain SIGTERM from Steam's own
    process supervision, not from the Suite. Must still say something about
    why, not just go quiet about it the way it did on 2026-08-27."""
    with pytest.raises(KeyboardInterrupt):
        on_stop(15, None)
    err = capsys.readouterr().err
    assert "(reason=signal)" in err


@pytest.mark.parametrize("reason", ["suite_quit", "tray_stop"])
def test_reason_set_by_the_suite_is_reported(on_stop, monkeypatch, capsys, reason):
    monkeypatch.setenv("GB_STOP_REASON", reason)
    with pytest.raises(KeyboardInterrupt):
        on_stop(15, None)
    err = capsys.readouterr().err
    assert f"(reason={reason})" in err


def test_counts_and_reason_are_on_the_same_line(on_stop, capsys):
    with pytest.raises(KeyboardInterrupt):
        on_stop(15, None)
    err = capsys.readouterr().err
    stopping_line = next(l for l in err.splitlines() if "stopping game" in l)
    assert "1 process(es) asked to exit, 2 force-killed" in stopping_line
    assert "reason=" in stopping_line
