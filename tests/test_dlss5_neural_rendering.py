# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""gb_gaming.dlss5: the one-toggle orchestrator over gb_gaming.dlss5_runtime.
Rewritten 2026-09-02 , see the module's own docstring for why the previous
mechanism (patching a leaked RenoDX "Control" add-on) was replaced rather
than extended.

dlss5_runtime's own install/uninstall/ini logic is exercised in
tests/test_dlss5_runtime.py; these tests monkeypatch that module's entry
points to check dlss5.py's ORCHESTRATION , what it calls, in what order, and
what it does with each result , not the mechanics underneath.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gb_gaming import dlss5 as d  # noqa: E402
from gb_gaming import dlss5_runtime as runtime  # noqa: E402


@pytest.fixture
def game_dir(tmp_path):
    g = tmp_path / "game"
    g.mkdir()
    return g


@pytest.fixture
def model_dll(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    dll = assets / "nvngx_dlssnr.dll"
    dll.write_bytes(b"fake model weights")
    monkeypatch.setattr(d, "asset_dir", lambda: assets)
    return dll


def test_list_assets_reports_only_the_model_dll(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(d, "asset_dir", lambda: assets)

    result = d.list_assets()
    assert result["asset_dir"] == str(assets)
    assert result["files"] == [{"name": "nvngx_dlssnr.dll", "present": False, "required": True}]

    (assets / "nvngx_dlssnr.dll").write_bytes(b"x")
    assert d.list_assets()["files"][0]["present"] is True


def test_enable_fails_clearly_when_model_dll_missing(tmp_path, game_dir, monkeypatch):
    assets = tmp_path / "empty_assets"
    assets.mkdir()
    monkeypatch.setattr(d, "asset_dir", lambda: assets)

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is False
    assert "nvngx_dlssnr.dll" in result["msg"]


def test_enable_fails_clearly_when_runtime_unreachable(game_dir, model_dll, monkeypatch):
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (False, "runtime not found"))
    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is False
    assert result["msg"] == "runtime not found"


def test_enable_fails_clearly_when_no_proxy_slot_free(game_dir, model_dll, tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda game_dir: None)
    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is False
    assert "no free DLL proxy slot" in result["msg"]


def test_enable_installs_and_turns_the_pass_on(game_dir, model_dll, tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda game_dir: "winmm.dll")

    install_calls = []
    def _fake_install(runtime_dir, game_dir, proxy, model):
        install_calls.append((runtime_dir, game_dir, proxy, model))
        return {"ok": True, "msg": "installed", "proxy_dll": proxy, "files": ["winmm.dll"]}
    monkeypatch.setattr(runtime, "install_dlss5_runtime_into_game", _fake_install)

    enable_calls = []
    def _fake_enable(ini_path, enabled):
        enable_calls.append((ini_path, enabled))
        return {"ok": True, "msg": "enabled"}
    monkeypatch.setattr(runtime, "set_neural_rendering_enabled", _fake_enable)

    splash_calls = []
    monkeypatch.setattr(runtime, "disable_startup_splash",
                         lambda ini_path: splash_calls.append(ini_path) or {"ok": True, "msg": "silenced"})

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is True
    assert result["proxy_dll"] == "winmm.dll"
    assert "Neural Rendering enabled" in result["msg"]
    assert install_calls == [(runtime_dir, game_dir, "winmm.dll", model_dll)]
    assert enable_calls == [(game_dir / "GreenBoostDLSS5.ini", True)]
    assert splash_calls == [game_dir / "GreenBoostDLSS5.ini"]


def test_enable_does_not_touch_frame_generation(game_dir, model_dll, tmp_path, monkeypatch):
    """enable_for_game() used to force FrameGen.Enabled=true / FGInput=dlssg /
    FGOutput=dlssg automatically, on the theory that the whole DLSS 5 feature
    set should work from one toggle. Real testing on 007 First Light left the
    image corrupted (pink/magenta cast) with this forced on, even after the
    DLSS preset override (a separate, since-reverted default , see the other
    test below) was ruled out. The user's original report, before either
    default existed, was ghosting only , a much milder symptom , which points
    at Frame Generation, not Neural Rendering itself, as the actual cause.
    enable_for_game() must leave [FrameGen] alone; the panel's own toggle is
    opt-in only now, with a warning (see Dlss5Panel.tsx)."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda game_dir: "winmm.dll")
    monkeypatch.setattr(runtime, "install_dlss5_runtime_into_game",
                         lambda *a, **k: {"ok": True, "msg": "installed", "files": ["winmm.dll"]})
    monkeypatch.setattr(runtime, "set_neural_rendering_enabled",
                         lambda ini_path, enabled: {"ok": True, "msg": "enabled"})
    monkeypatch.setattr(runtime, "disable_startup_splash",
                         lambda ini_path: {"ok": True, "msg": "silenced"})

    ini_path = game_dir / "GreenBoostDLSS5.ini"
    ini_path.write_text("[FrameGen]\nEnabled=auto\nFGInput=auto\nFGOutput=auto\n")

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is True
    assert "Frame Generation" not in result["msg"]

    frame_gen = runtime.read_ini_section(ini_path, "FrameGen")
    assert frame_gen["Enabled"] == "auto"
    assert frame_gen["FGInput"] == "auto"
    assert frame_gen["FGOutput"] == "auto"


def test_enable_does_not_touch_dlss_super_resolution_preset(game_dir, model_dll, tmp_path, monkeypatch):
    """enable_for_game() used to force DLSS Super Resolution's preset to M
    (the transformer model) automatically, on the theory that ghosting is
    usually an SR preset problem. Real testing on 007 First Light
    immediately produced far worse corruption (blown highlights, a magenta
    cast, vertical comb artifacts) the moment Neural Rendering and a
    non-default preset were both forced on together , not ghosting, a
    broken image. 007's Neural Rendering pass was only ever confirmed
    working under the game's own default preset. enable_for_game() must
    leave [DLSS] alone; the panel's own preset override control is opt-in
    only now (see Dlss5Panel.tsx)."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda game_dir: "winmm.dll")
    monkeypatch.setattr(runtime, "install_dlss5_runtime_into_game",
                         lambda *a, **k: {"ok": True, "msg": "installed", "files": ["winmm.dll"]})
    monkeypatch.setattr(runtime, "set_neural_rendering_enabled",
                         lambda ini_path, enabled: {"ok": True, "msg": "enabled"})
    monkeypatch.setattr(runtime, "disable_startup_splash",
                         lambda ini_path: {"ok": True, "msg": "silenced"})

    ini_path = game_dir / "GreenBoostDLSS5.ini"
    ini_path.write_text("[DLSS]\nRenderPresetOverride=auto\nRenderPresetForAll=auto\n")

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is True
    assert "preset" not in result["msg"].lower()

    dlss = runtime.read_ini_section(ini_path, "DLSS")
    assert dlss["RenderPresetOverride"] == "auto"
    assert dlss["RenderPresetForAll"] == "auto"


def test_enable_reports_install_failure_without_touching_the_ini(game_dir, model_dll, tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda game_dir: "winmm.dll")
    monkeypatch.setattr(runtime, "install_dlss5_runtime_into_game",
                         lambda *a, **k: {"ok": False, "msg": "disk full"})

    def _fail_enable(*a, **k):
        raise AssertionError("must not try to enable the pass when install failed")
    monkeypatch.setattr(runtime, "set_neural_rendering_enabled", _fail_enable)

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is False
    assert result["msg"] == "disk full"


def test_disable_delegates_to_uninstall(game_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "uninstall_dlss5_runtime_from_game",
                         lambda gd: calls.append(gd) or {"ok": True, "msg": "removed 5, restored 1"})
    result = d.disable_for_game(game_dir, "3768760")
    assert result["ok"] is True
    assert calls == [game_dir]


# ── preflight ──────────────────────────────────────────────────────────────

def _rtx50_output():
    class Result:
        stdout = "NVIDIA GeForce RTX 5070, 610.43.02\n"
    return Result()


def test_preflight_reports_all_rows(game_dir, model_dll, tmp_path, monkeypatch):
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _rtx50_output())
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda gd: "winmm.dll")

    results = d.preflight(game_dir, "3768760")
    by_check = {r["check"]: r for r in results}
    assert by_check["GPU is RTX 50-series"]["ok"] is True
    assert by_check["nvngx_dlssnr.dll staged"]["ok"] is True
    assert by_check["DLSS5 runtime staged"]["ok"] is True
    assert by_check["A DLL proxy slot is free"]["ok"] is True
    assert "winmm.dll" in by_check["A DLL proxy slot is free"]["detail"]


def test_preflight_flags_non_rtx50_gpu_and_missing_model_dll(game_dir, tmp_path, monkeypatch):
    class Result:
        stdout = "NVIDIA GeForce RTX 4070, 560.00\n"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: Result())
    empty_assets = tmp_path / "empty"
    empty_assets.mkdir()
    monkeypatch.setattr(d, "asset_dir", lambda: empty_assets)
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, tmp_path))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda gd: "winmm.dll")

    results = d.preflight(game_dir, "3768760")
    by_check = {r["check"]: r for r in results}
    assert by_check["GPU is RTX 50-series"]["ok"] is False
    assert by_check["nvngx_dlssnr.dll staged"]["ok"] is False


def test_preflight_flags_a_game_still_on_the_pre_migration_install(game_dir, model_dll, tmp_path, monkeypatch):
    """A game enabled before 2026-09-02 is still running the old
    OptiScaler.ini-based runtime until DLSS 5 is turned off and back on for
    it , migration only runs inside install_dlss5_runtime_into_game().
    Every config panel write targets GreenBoostDLSS5.ini, which doesn't
    exist for such a game, so without this check a setting (including the
    anti-ghosting DLSS preset) looks like it applied and then silently
    doesn't , this must be surfaced, not left to fail quietly."""
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _rtx50_output())
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda gd: "winmm.dll")

    (game_dir / ".gb_optiscaler_manifest.json").write_text(
        '{"added": ["OptiScaler.ini"], "overwritten": []}')

    results = d.preflight(game_dir, "3768760")
    by_check = {r["check"]: r for r in results}
    assert by_check["Runtime is up to date"]["ok"] is False
    assert by_check["Runtime is up to date"]["level"] == "warn"
    assert "OptiScaler.ini" in by_check["Runtime is up to date"]["detail"]

    # Once migrated (GreenBoostDLSS5.ini now present), the row must disappear
    # rather than keep warning about a state that's no longer true.
    (game_dir / "GreenBoostDLSS5.ini").write_text("[DlssNr]\nEnabled=true\n")
    results = d.preflight(game_dir, "3768760")
    assert "Runtime is up to date" not in {r["check"] for r in results}


# ── asset_dir override ("Select DLSS5 folder") ────────────────────────────

def test_asset_dir_override_takes_priority_and_persists(tmp_path, monkeypatch):
    override_path = tmp_path / "dlss5_asset_dir.txt"
    monkeypatch.setattr(d, "_ASSET_DIR_OVERRIDE_PATH", override_path)
    monkeypatch.delenv("GB_DLSS5_DIR", raising=False)

    picked = tmp_path / "my_dlss5_files"
    picked.mkdir()

    result = d.set_asset_dir_override(str(picked))
    assert result["ok"] is True
    assert d.get_asset_dir_override() == str(picked)
    assert d.asset_dir() == picked


def test_asset_dir_override_rejects_a_non_directory(tmp_path, monkeypatch):
    override_path = tmp_path / "dlss5_asset_dir.txt"
    monkeypatch.setattr(d, "_ASSET_DIR_OVERRIDE_PATH", override_path)

    result = d.set_asset_dir_override(str(tmp_path / "does_not_exist"))
    assert result["ok"] is False
    assert not override_path.exists()


def test_asset_dir_override_can_be_cleared(tmp_path, monkeypatch):
    override_path = tmp_path / "dlss5_asset_dir.txt"
    monkeypatch.setattr(d, "_ASSET_DIR_OVERRIDE_PATH", override_path)
    picked = tmp_path / "my_dlss5_files"
    picked.mkdir()
    d.set_asset_dir_override(str(picked))

    result = d.set_asset_dir_override("")
    assert result["ok"] is True
    assert d.get_asset_dir_override() == ""
    assert not override_path.exists()


# ── read_config / write_config ─────────────────────────────────────────────

def test_read_config_returns_only_present_keys(game_dir):
    (game_dir / "GreenBoostDLSS5.ini").write_text(
        "[DlssNr]\nEnabled=true\nIntensity=1.6\n\n[FrameGen]\nEnabled=false\n")
    config = d.read_config(game_dir)
    assert config["DlssNr.Enabled"] == "true"
    assert config["DlssNr.Intensity"] == "1.6"
    assert config["FrameGen.Enabled"] == "false"
    assert "DlssNr.Preset" not in config


def test_write_config_sets_requested_keys_only(game_dir):
    (game_dir / "GreenBoostDLSS5.ini").write_text("[DlssNr]\nEnabled=auto\nIntensity=auto\n")
    result = d.write_config(game_dir, {"DlssNr.Intensity": "1.6", "DlssNr.SkinStructure": "0.8"})
    assert result["ok"] is True
    config = d.read_config(game_dir)
    assert config["DlssNr.Intensity"] == "1.6"
    assert config["DlssNr.SkinStructure"] == "0.8"
    assert config["DlssNr.Enabled"] == "auto"  # untouched


def test_write_config_rejects_unknown_key(game_dir):
    (game_dir / "GreenBoostDLSS5.ini").write_text("[DlssNr]\nEnabled=auto\n")
    result = d.write_config(game_dir, {"DlssNr.NotARealKey": "1"})
    assert result["ok"] is False
    assert "unknown" in result["msg"]
