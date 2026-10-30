# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""RenderDoc capture arming (_renderdoc_capture_env in gb_proton_main.py).

Companion to src-tauri/src/renderdoc.rs, which writes the marker file this
function consumes. The Suite's "Capture" button calls arm_capture_impl()
(Rust) to write an empty marker at
~/.config/greenboost-gaming/renderdoc-capture-pending/<appid> before the
ordinary launch path runs; this function is the only thing that reads it,
deleting it on read so a later plain Launch never inherits capture mode.

Same AST-lift technique as test_external_dll_paths.py , the wrapper calls
main() at import time, so importing the module would try to launch a game.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "greenboost_proton" / "gb_proton_main.py"


@pytest.fixture()
def capture_env_fn():
    src = WRAPPER.read_text(encoding="utf-8")
    lines = src.splitlines(True)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_renderdoc_capture_env":
            chunk = "".join(lines[node.lineno - 1:node.end_lineno])
            ns: dict = {"os": os, "sys": sys}
            exec(chunk, ns)                               # noqa: S102 , our own source
            return ns["_renderdoc_capture_env"]
    raise AssertionError(f"_renderdoc_capture_env not found in {WRAPPER}")


@pytest.fixture()
def marker_dir(tmp_path):
    d = tmp_path / "renderdoc-capture-pending"
    d.mkdir()
    return d


def test_no_gameid_is_a_no_op(capture_env_fn, marker_dir):
    assert capture_env_fn("", marker_dir=str(marker_dir)) == {}
    assert capture_env_fn(None, marker_dir=str(marker_dir)) == {}


def test_no_marker_present_is_a_no_op(capture_env_fn, marker_dir):
    assert capture_env_fn("2909400", marker_dir=str(marker_dir)) == {}


def test_marker_present_arms_capture_and_is_consumed(capture_env_fn, marker_dir):
    marker = marker_dir / "2909400"
    marker.write_bytes(b"")

    env = capture_env_fn("2909400", marker_dir=str(marker_dir))
    assert env == {"ENABLE_VULKAN_RENDERDOC_CAPTURE": "1"}
    assert not marker.exists(), "marker must be consumed (deleted) on read"


def test_marker_for_a_different_appid_does_not_arm(capture_env_fn, marker_dir):
    (marker_dir / "2909400").write_bytes(b"")
    env = capture_env_fn("70", marker_dir=str(marker_dir))  # 007 First Light
    assert env == {}
    # the other game's marker is untouched
    assert (marker_dir / "2909400").exists()


def test_second_call_after_consumption_is_a_no_op(capture_env_fn, marker_dir):
    marker = marker_dir / "2909400"
    marker.write_bytes(b"")
    first = capture_env_fn("2909400", marker_dir=str(marker_dir))
    second = capture_env_fn("2909400", marker_dir=str(marker_dir))
    assert first == {"ENABLE_VULKAN_RENDERDOC_CAPTURE": "1"}
    assert second == {}
