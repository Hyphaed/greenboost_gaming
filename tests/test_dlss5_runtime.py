# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""gb_gaming.dlss5_runtime: locate/install/uninstall of GreenBoost's own
built DLSS Neural Rendering runtime.

Nothing here touches the network , the runtime ships with this project
(libraries/dlssnr/runtime/), so there is no fetch step to test. This
exercises the LOGIC (proxy-slot selection, manifest bookkeeping, the ini
edit, legacy-install migration) against synthetic fixtures , see CLAUDE.md's
"Real data over synthetic tests" rule for why that distinction matters; the
real runtime was verified separately (export table checked against
Source.def: 902 exports, all 848 Source.def entries present) and confirmed
dispatching frames on a real 007 First Light launch , see FORK_NOTES.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gb_gaming import dlss5_runtime as o  # noqa: E402


def _make_runtime_dir(tmp_path: Path) -> Path:
    """A minimal but structurally real runtime dir, matching
    libraries/dlssnr/runtime/'s own layout."""
    d = tmp_path / "runtime"
    d.mkdir()
    (d / "GreenBoostDLSS5.dll").write_bytes(b"fake dlss5 proxy binary")
    (d / "GreenBoostDLSS5.ini").write_text("[DlssNr]\nEnabled=auto\n")
    (d / "nvngx.dll_dlssnr.dll").write_bytes(b"fake forwarder")
    (d / "GreenBoostDLSS5" / "D3D12_GreenBoostDLSS5").mkdir(parents=True)
    (d / "GreenBoostDLSS5" / "D3D12_GreenBoostDLSS5" / "D3D12Core.dll").write_bytes(b"fake agility")
    (d / "Licenses").mkdir()
    (d / "Licenses" / "DirectX_LICENSE.txt").write_text("license text")
    return d


def test_ensure_dlss5_runtime_available_finds_a_real_looking_dir(tmp_path, monkeypatch):
    runtime_dir = _make_runtime_dir(tmp_path)
    monkeypatch.setattr(o, "_RUNTIME_DIR", runtime_dir)
    ok, result = o.ensure_dlss5_runtime_available()
    assert ok
    assert result == runtime_dir


def test_ensure_dlss5_runtime_available_reports_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "_RUNTIME_DIR", tmp_path / "nowhere")
    ok, msg = o.ensure_dlss5_runtime_available()
    assert not ok
    assert "not found" in msg


def test_ensure_dlss5_runtime_available_reports_missing_dll(tmp_path, monkeypatch):
    d = tmp_path / "runtime"
    d.mkdir()
    monkeypatch.setattr(o, "_RUNTIME_DIR", d)
    ok, msg = o.ensure_dlss5_runtime_available()
    assert not ok
    assert "GreenBoostDLSS5.dll" in msg


# ── pick_proxy_dll ────────────────────────────────────────────────────────

def test_pick_proxy_dll_returns_first_free_candidate(tmp_path):
    assert o.pick_proxy_dll(tmp_path) == o.PROXY_CANDIDATES[0]


def test_pick_proxy_dll_skips_names_the_game_already_ships(tmp_path):
    (tmp_path / o.PROXY_CANDIDATES[0]).write_bytes(b"native file")
    assert o.pick_proxy_dll(tmp_path) == o.PROXY_CANDIDATES[1]


def test_pick_proxy_dll_none_when_every_candidate_collides(tmp_path):
    for name in o.PROXY_CANDIDATES:
        (tmp_path / name).write_bytes(b"x")
    assert o.pick_proxy_dll(tmp_path) is None


# ── install / uninstall ───────────────────────────────────────────────────

@pytest.fixture
def runtime_dir(tmp_path):
    return _make_runtime_dir(tmp_path)


def test_install_places_files_and_renames_proxy(runtime_dir, tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    model = tmp_path / "nvngx_dlssnr.dll"
    model.write_bytes(b"fake model weights")

    result = o.install_dlss5_runtime_into_game(runtime_dir, game_dir, "winmm.dll", model)
    assert result["ok"] is True
    assert (game_dir / "winmm.dll").read_bytes() == b"fake dlss5 proxy binary"
    assert (game_dir / "GreenBoostDLSS5.ini").is_file()
    assert (game_dir / "nvngx.dll_dlssnr.dll").is_file()
    assert (game_dir / "nvngx_dlssnr.dll").read_bytes() == b"fake model weights"
    assert (game_dir / "GreenBoostDLSS5" / "D3D12_GreenBoostDLSS5" / "D3D12Core.dll").is_file()
    assert (game_dir / o._MANIFEST_NAME).is_file()

    manifest = json.loads((game_dir / o._MANIFEST_NAME).read_text())
    assert manifest["proxy_dll"] == "winmm.dll"
    assert "winmm.dll" in manifest["added"]
    assert not manifest["overwritten"]


def test_install_snapshots_a_colliding_file(runtime_dir, tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "winmm.dll").write_bytes(b"the game's own winmm")
    model = tmp_path / "nvngx_dlssnr.dll"
    model.write_bytes(b"fake model weights")

    result = o.install_dlss5_runtime_into_game(runtime_dir, game_dir, "winmm.dll", model)
    assert result["ok"] is True
    assert (game_dir / "winmm.dll.gb_dlss5_original").read_bytes() == b"the game's own winmm"
    manifest = json.loads((game_dir / o._MANIFEST_NAME).read_text())
    assert "winmm.dll" in manifest["overwritten"]
    assert "winmm.dll" not in manifest["added"]


def test_install_fails_clearly_when_model_dll_missing(runtime_dir, tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    result = o.install_dlss5_runtime_into_game(runtime_dir, game_dir, "winmm.dll", tmp_path / "missing.dll")
    assert result["ok"] is False
    assert "nvngx_dlssnr.dll" in result["msg"]


def test_uninstall_removes_added_and_restores_overwritten(runtime_dir, tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "winmm.dll").write_bytes(b"the game's own winmm")
    model = tmp_path / "nvngx_dlssnr.dll"
    model.write_bytes(b"fake model weights")
    o.install_dlss5_runtime_into_game(runtime_dir, game_dir, "winmm.dll", model)

    result = o.uninstall_dlss5_runtime_from_game(game_dir)
    assert result["ok"] is True
    assert (game_dir / "winmm.dll").read_bytes() == b"the game's own winmm"
    assert not (game_dir / "winmm.dll.gb_dlss5_original").exists()
    assert not (game_dir / "GreenBoostDLSS5.ini").exists()
    assert not (game_dir / "nvngx_dlssnr.dll").exists()
    assert not (game_dir / o._MANIFEST_NAME).exists()


def test_uninstall_is_a_harmless_noop_when_nothing_installed(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    result = o.uninstall_dlss5_runtime_from_game(game_dir)
    assert result["ok"] is True
    assert "nothing installed" in result["msg"]


# ── legacy (pre-2026-09-02) install migration ─────────────────────────────

def test_install_migrates_a_legacy_install_first(runtime_dir, tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    # Simulate a pre-2026-09-02 install: legacy manifest naming
    # OptiScaler.ini as an added file, plus one of the ~175 MB dead
    # alternate-vendor files that install used to bring along.
    (game_dir / "winmm.dll").write_bytes(b"legacy proxy binary")
    (game_dir / "OptiScaler.ini").write_text("[DlssNr]\nEnabled=true\n")
    (game_dir / "libxess.dll").write_bytes(b"dead xess payload")
    (game_dir / o._LEGACY_MANIFEST_NAME).write_text(json.dumps({
        "proxy_dll": "winmm.dll",
        "added": ["winmm.dll", "OptiScaler.ini", "libxess.dll"],
        "overwritten": [],
    }))

    model = tmp_path / "nvngx_dlssnr.dll"
    model.write_bytes(b"fake model weights")
    result = o.install_dlss5_runtime_into_game(runtime_dir, game_dir, "winmm.dll", model)

    assert result["ok"] is True
    assert not (game_dir / "OptiScaler.ini").exists()
    assert not (game_dir / "libxess.dll").exists()
    assert not (game_dir / o._LEGACY_MANIFEST_NAME).exists()
    assert (game_dir / "GreenBoostDLSS5.ini").is_file()
    assert (game_dir / "winmm.dll").read_bytes() == b"fake dlss5 proxy binary"


def test_uninstall_falls_back_to_legacy_manifest(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "winmm.dll").write_bytes(b"legacy proxy binary")
    (game_dir / o._LEGACY_MANIFEST_NAME).write_text(json.dumps({
        "proxy_dll": "winmm.dll", "added": ["winmm.dll"], "overwritten": [],
    }))

    result = o.uninstall_dlss5_runtime_from_game(game_dir)
    assert result["ok"] is True
    assert not (game_dir / "winmm.dll").exists()
    assert not (game_dir / o._LEGACY_MANIFEST_NAME).exists()


# ── set_neural_rendering_enabled / disable_startup_splash ────────────────

def test_set_neural_rendering_enabled_flips_existing_key(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[General]\nSomeKey=1\n\n[DlssNr]\nEnabled=auto\nIntensity=1.0\n")

    result = o.set_neural_rendering_enabled(ini, True)
    assert result["ok"] is True
    text = ini.read_text()
    assert "Enabled=true" in text
    assert "[General]\nSomeKey=1" in text
    assert "Intensity=1.0" in text


def test_set_neural_rendering_enabled_is_idempotent(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[DlssNr]\nEnabled=auto\n")
    o.set_neural_rendering_enabled(ini, True)
    o.set_neural_rendering_enabled(ini, True)
    text = ini.read_text()
    assert text.count("Enabled=") == 1
    assert "Enabled=true" in text


def test_set_neural_rendering_enabled_can_turn_back_off(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[DlssNr]\nEnabled=auto\n")
    o.set_neural_rendering_enabled(ini, True)
    o.set_neural_rendering_enabled(ini, False)
    assert "Enabled=false" in ini.read_text()


def test_set_neural_rendering_enabled_missing_ini(tmp_path):
    result = o.set_neural_rendering_enabled(tmp_path / "missing.ini", True)
    assert result["ok"] is False


def test_disable_startup_splash_sets_the_key(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[Menu]\nOverlayMenu=auto\nDisableSplash=auto\nShowFps=auto\n")

    result = o.disable_startup_splash(ini)
    assert result["ok"] is True
    text = ini.read_text()
    assert "DisableSplash=true" in text
    assert "OverlayMenu=auto" in text
    assert "ShowFps=auto" in text


def test_disable_startup_splash_creates_section_and_key_if_absent(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[General]\nSomeKey=1\n")

    result = o.disable_startup_splash(ini)
    assert result["ok"] is True
    text = ini.read_text()
    assert "[Menu]" in text
    assert "DisableSplash=true" in text


# ── read_ini_section ───────────────────────────────────────────────────────

def test_read_ini_section_returns_keys_including_literal_auto(tmp_path):
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[DlssNr]\nEnabled=true\nIntensity=auto\nPreset=0\n\n[Menu]\nShowFps=auto\n")
    section = o.read_ini_section(ini, "DlssNr")
    assert section == {"Enabled": "true", "Intensity": "auto", "Preset": "0"}


def test_read_ini_section_missing_file_or_section_is_empty(tmp_path):
    assert o.read_ini_section(tmp_path / "missing.ini", "DlssNr") == {}
    ini = tmp_path / "GreenBoostDLSS5.ini"
    ini.write_text("[Menu]\nShowFps=auto\n")
    assert o.read_ini_section(ini, "DlssNr") == {}
