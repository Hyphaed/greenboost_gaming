# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Steam's own helpers must go straight to upstream Proton, untouched.

Steam runs the GreenBoost compat tool for things that are not the game, and it
BLOCKS on one of them , `iscriptevaluator.exe`, the install-script evaluator ,
before it launches anything at all.

Confirmed live 2026-08-21: that helper came through the wrapper and was given
the full game treatment (subreaper, session record, power lock + watchdog,
DXVK/gplasync staging, its own `greenboost-proton-<appid>.log`), then hung.
With Steam waiting on it, clicking Launch in the Suite did nothing, while the
Suite , reading that helper's log , reported "staging DXVK/VKD3D libraries"
for something that was never going to become a game.

The gate is matched on the target executable, not on the verb alone: Steam
launches the real game with `waitforexitandrun` and helpers with `run`, but
`run` is also the verb in the documented dry-run check and in some non-Steam
shortcuts, so verb-only would silently change what those do.

The wrapper calls `main()` at import time (by design , the stub compiles and
execs it), so these tests lift the two definitions out with `ast` instead of
importing the module. Importing it launches a game.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "greenboost_proton" / "gb_proton_main.py"
WANTED = {"_STEAM_INTERNAL_EXES", "_steam_internal_helper"}


@pytest.fixture(scope="module")
def helper_fn():
    src = WRAPPER.read_text(encoding="utf-8")
    lines = src.splitlines(True)
    chunks = []
    for node in ast.parse(src).body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign):
            name = getattr(node.targets[0], "id", None)
        if name in WANTED:
            chunks.append("".join(lines[node.lineno - 1:node.end_lineno]))
    assert len(chunks) == len(WANTED), f"missing definitions in {WRAPPER}"
    ns: dict = {"os": os, "sys": sys}
    exec("".join(chunks), ns)                      # noqa: S102 , our own source
    return ns["_steam_internal_helper"]


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    monkeypatch.delenv("GREENBOOST_DRY_RUN", raising=False)


HELPERS = [
    (["proton", "run",
      "/home/u/.local/share/Steam/legacycompat/iscriptevaluator.exe",
      "legacycompat\\evaluatorscript_2909400.vdf"], "iscriptevaluator.exe"),
    (["proton", "run", "/x/d3ddriverquery64.exe"], "d3ddriverquery64.exe"),
    (["proton", "run", "/x/xalia.exe"], "xalia.exe"),
    (["proton", "run", "/x/steamerrorreporter.exe"], "steamerrorreporter.exe"),
    # Anything under legacycompat/, and anything handed a .vdf, whatever it is
    # called , those two are what Steam's internal invocations look like.
    (["proton", "run", "/x/legacycompat/whatever.exe"], "whatever.exe"),
    (["proton", "run", "/x/thing.exe", "script.vdf"], "thing.exe"),
]

GAMES = [
    ["proton", "waitforexitandrun", "/games/ff7/ff7rebirth.exe"],
    ["proton", "run", "/games/ff7/ff7rebirth.exe"],
    ["proton", "runinprefix", "/games/ff7/ff7rebirth.exe"],
    ["proton", "run", "/bin/true"],
    ["proton"],
]


@pytest.mark.parametrize("argv,expected", HELPERS)
def test_steam_helpers_are_recognised(helper_fn, monkeypatch, argv, expected):
    monkeypatch.setattr(sys, "argv", argv)
    assert helper_fn() == expected


@pytest.mark.parametrize("argv", GAMES)
def test_real_launches_are_not_touched(helper_fn, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    assert helper_fn() == ""


def test_dry_run_still_takes_the_full_greenboost_path(helper_fn, monkeypatch):
    """`GREENBOOST_DRY_RUN=1 proton run /bin/true` is the documented check that
    the wrapper's own launch path works end to end (CLAUDE.md, Verification).
    Gating it out would leave that check exercising upstream Proton instead,
    and still passing , the worst kind of green."""
    monkeypatch.setenv("GREENBOOST_DRY_RUN", "1")
    monkeypatch.setattr(sys, "argv",
                        ["proton", "run", "/x/legacycompat/iscriptevaluator.exe"])
    assert helper_fn() == ""
