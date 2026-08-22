# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""A non-Steam shortcut carries two ids, and the wrapper must report the one
the Suite holds.

Steam gives a shortcut launch `SteamGameId=(appid << 32) | 0x02000000`,
`SteamAppId=0`, and a `STEAM_COMPAT_DATA_PATH` that still ends in the 32-bit
appid. Taking `SteamGameId` verbatim named this wrapper's log
`greenboost-proton-14063739891024396288.log` while the Suite watched
`greenboost-proton-3274469611.log` , two files for one launch, and a launch
status that could never see the wrapper's own progress.

The pair below is ground truth, not arithmetic: it was read out of Steam's own
console log from a working Battle.net launch on 2026-08-18, which wrote the
compatdata path with 3274469611 and `Adding process … for gameID
14063739891024396288`.

Same `ast` lift as test_proton_helper_gate.py , importing the wrapper runs
main(), which launches a game.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "greenboost_proton" / "gb_proton_main.py"
WANTED = {"_normalize_shortcut_gameid", "_resolve_appid"}

BNET_APPID = 3274469611
BNET_GAMEID = 14063739891024396288


@pytest.fixture(scope="module")
def resolve():
    src = WRAPPER.read_text(encoding="utf-8")
    lines = src.splitlines(True)
    chunks = []
    for node in ast.parse(src).body:
        if getattr(node, "name", None) in WANTED:
            chunks.append("".join(lines[node.lineno - 1:node.end_lineno]))
    assert len(chunks) == len(WANTED), f"missing definitions in {WRAPPER}"
    ns: dict = {"os": os, "sys": sys}
    exec("".join(chunks), ns)                      # noqa: S102 , our own source
    return ns


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("SteamGameId", "SteamAppId", "STEAM_APPID",
                "STEAM_COMPAT_DATA_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["proton"])


def test_shortcut_gameid_becomes_the_compatdata_appid(resolve, monkeypatch):
    monkeypatch.setenv("SteamGameId", str(BNET_GAMEID))
    monkeypatch.setenv("SteamAppId", "0")
    assert resolve["_resolve_appid"]() == str(BNET_APPID)


def test_ordinary_steam_appid_is_untouched(resolve, monkeypatch):
    monkeypatch.setenv("SteamGameId", "2909400")
    assert resolve["_resolve_appid"]() == "2909400"


def test_non_numeric_gameid_is_untouched(resolve):
    # Steam has handed non-numeric ids through before; whatever it is, it is
    # the appid we have, and mangling it is worse than passing it on.
    assert resolve["_normalize_shortcut_gameid"]("not-a-number") == "not-a-number"


def test_a_big_id_that_is_not_a_shortcut_is_untouched(resolve):
    # Only the 0x02000000 tag marks a shortcut. A 64-bit id with anything else
    # in its low word is a mod / source-engine id, and its high word is not an
    # appid , shifting it would invent one.
    other = (2909400 << 32) | 0x01000000
    assert resolve["_normalize_shortcut_gameid"](str(other)) == str(other)


def test_compatdata_still_answers_when_steam_sets_no_id(resolve, monkeypatch):
    monkeypatch.setenv("STEAM_COMPAT_DATA_PATH",
                       f"/home/u/.steam/steam/steamapps/compatdata/{BNET_APPID}/")
    assert resolve["_resolve_appid"]() == str(BNET_APPID)
