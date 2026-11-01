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


@pytest.fixture(autouse=True)
def _allow_fake_model(monkeypatch):
    """These tests stage tiny fake model files; the hash gate has its own tests."""
    monkeypatch.setenv("GREENBOOST_ALLOW_UNVERIFIED_NR", "1")


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


def test_enable_applies_the_ff7_rebirth_color_resource_barrier_hotfix(game_dir, model_dll, tmp_path, monkeypatch):
    """FINAL FANTASY VII REBIRTH (appid 2909400) update 1.004 broke the game's
    own Unreal Engine resource-state reporting for its color target , the
    upstream OptiScaler project documents this as causing dark glitches and
    fixes it with Hotfix.ColorResourceBarrier=4 in place of the default
    "auto". enable_for_game() must apply this automatically for that appid
    without the user having to know it exists , see GAME_INI_HOTFIXES."""
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
    ini_path.write_text("[Hotfix]\nColorResourceBarrier=auto\n")

    result = d.enable_for_game(game_dir, "2909400")
    assert result["ok"] is True
    assert runtime.read_ini_section(ini_path, "Hotfix")["ColorResourceBarrier"] == "4"


def test_enable_leaves_the_hotfix_alone_for_an_unrelated_game(game_dir, model_dll, tmp_path, monkeypatch):
    """A game not in GAME_INI_HOTFIXES must not have its [Hotfix] section
    touched , the table is a targeted per-appid fix list, not a blanket
    default."""
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
    ini_path.write_text("[Hotfix]\nColorResourceBarrier=auto\n")

    result = d.enable_for_game(game_dir, "3768760")
    assert result["ok"] is True
    assert runtime.read_ini_section(ini_path, "Hotfix")["ColorResourceBarrier"] == "auto"


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


# ── last_session_neural_rendering_status() ─────────────────────────────────
# The config's own DlssNr.Enabled=true never proved the pass actually ran ,
# confirmed 2026-09-02 when a full playtest showed zero DlssNr_Dx12::Dispatch
# and zero CreateFeature/EvaluateFeature calls for any DLSS feature. These
# tests use the exact log line formats DlssNrFeature_Dx12.cpp now emits.

def test_last_session_status_none_without_a_log(game_dir):
    assert d.last_session_neural_rendering_status(game_dir) is None


def test_last_session_status_found_nothing_in_an_unrelated_log(game_dir):
    (game_dir / "GreenBoostDLSS5.log").write_text(
        "[14:23:36.550365] [I] Util::FindFilePath nvngx_dlss.dll found\n"
        "[14:23:36.665929] [W] Config::CheckUpscalerFiles nvngx.dll not found!\n")
    status = d.last_session_neural_rendering_status(game_dir)
    assert status == {"found": False, "active": None, "detail": ""}


def test_last_session_status_active_when_the_pass_actually_ran(game_dir):
    (game_dir / "GreenBoostDLSS5.log").write_text(
        "[14:23:36.901531] [I] DLSS-NR running at 2560x1440, guides 1707x960 "
        "(preset 0, intensity 1, style 0)\n"
        "[14:23:36.901600] [I] GreenBoost DLSS5 Injected: Neural Render ON "
        "(2560x1440, preset 0)\n")
    status = d.last_session_neural_rendering_status(game_dir)
    assert status == {"found": True, "active": True, "detail": "2560x1440, preset 0"}


def test_last_session_status_inactive_with_the_real_reason(game_dir):
    (game_dir / "GreenBoostDLSS5.log").write_text(
        "[14:23:36.665929] [I] DLSS-NR did not run: it is switched off\n"
        "[14:23:36.665935] [I] GreenBoost DLSS5: Neural Render OFF -- it is switched off\n")
    status = d.last_session_neural_rendering_status(game_dir)
    assert status == {"found": True, "active": False, "detail": "it is switched off"}


def test_last_session_status_uses_whichever_marker_is_last_in_the_log(game_dir):
    """A retry-after-failure can flip OFF -> ON within one session , the
    LAST occurrence in the log is what's true now, not the first."""
    (game_dir / "GreenBoostDLSS5.log").write_text(
        "[14:23:36.000000] [I] GreenBoost DLSS5: Neural Render OFF -- it is switched off\n"
        "[14:23:40.000000] [I] GreenBoost DLSS5 Injected: Neural Render ON "
        "(2560x1440, preset 0)\n")
    status = d.last_session_neural_rendering_status(game_dir)
    assert status["active"] is True


def test_preflight_reports_last_session_status(game_dir, model_dll, tmp_path, monkeypatch):
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _rtx50_output())
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "ensure_dlss5_runtime_available", lambda: (True, runtime_dir))
    monkeypatch.setattr(runtime, "pick_proxy_dll", lambda gd: "winmm.dll")

    results = d.preflight(game_dir, "3768760")
    by_check = {r["check"]: r for r in results}
    assert by_check["Last session"]["ok"] is False
    assert "no GreenBoostDLSS5.log yet" in by_check["Last session"]["detail"]

    (game_dir / "GreenBoostDLSS5.log").write_text(
        "[14:23:36.901600] [I] GreenBoost DLSS5 Injected: Neural Render ON "
        "(2560x1440, preset 0)\n")
    results = d.preflight(game_dir, "3768760")
    by_check = {r["check"]: r for r in results}
    assert by_check["Last session"]["ok"] is True
    assert "2560x1440" in by_check["Last session"]["detail"]


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


# ── Model trust gate and capability check ───────────────────────────────

def test_model_gate_refuses_a_same_size_different_build(tmp_path, monkeypatch):
    from gb_gaming import dlss_updater
    monkeypatch.delenv("GREENBOOST_ALLOW_UNVERIFIED_NR")
    good, other = tmp_path / "good.dll", tmp_path / "other.dll"
    good.write_bytes(b"nvidia-signed-310.8.0")
    other.write_bytes(b"nvidia-signed-310.8.X")        # same length, other content
    import hashlib
    h = hashlib.sha256(good.read_bytes()).hexdigest()
    monkeypatch.setitem(dlss_updater.TRUSTED_SHA256["nvngx_dlssnr.dll"], h, "test NVIDIA build")
    assert d.model_trust(good)[0] is True
    ok, why = d.model_trust(other)
    assert ok is False and "not a build GreenBoost knows" in why


def test_enable_is_refused_for_an_unverified_model(tmp_path, monkeypatch):
    monkeypatch.delenv("GREENBOOST_ALLOW_UNVERIFIED_NR")
    assets = tmp_path / "assets"; assets.mkdir()
    (assets / "nvngx_dlssnr.dll").write_bytes(b"patched build")
    monkeypatch.setattr(d, "asset_dir", lambda: assets)
    game = tmp_path / "game"; game.mkdir()
    res = d.enable_for_game(game, "1")
    assert res["ok"] is False and "not installed" in res["msg"]
    assert not any(game.iterdir())                      # nothing was copied


def _pe(machine=0x8664, payload=b""):
    b = bytearray(0x200)
    b[0:2] = b"MZ"
    import struct
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", b, 0x84, machine)
    return bytes(b) + payload


def test_capabilities_report_what_the_exe_contains(tmp_path):
    (tmp_path / "Game-Win64-Shipping.exe").write_bytes(_pe(payload=b"..D3D12CreateDevice..NGX_VULKAN..nvngx.dll.."))
    (tmp_path / "CrashReportClient.exe").write_bytes(_pe(payload=b"x" * 9000))   # not the game
    rows = {r["check"]: r for r in d.game_capabilities(tmp_path)}
    assert rows["64-bit game"]["ok"] and rows["Renderer"]["ok"] and rows["Game's own DLSS"]["ok"]
    assert "NGX (no Streamline)" in rows["Game's own DLSS"]["detail"]
    assert "Denuvo" in rows["Anti-cheat"]["detail"]            # never claims "none"


def test_capabilities_flag_streamline_and_anti_cheat(tmp_path):
    (tmp_path / "Game.exe").write_bytes(_pe(payload=b"d3d12 sl.interposer"))
    (tmp_path / "EasyAntiCheat").mkdir()
    rows = {r["check"]: r for r in d.game_capabilities(tmp_path)}
    assert "Streamline" in rows["Game's own DLSS"]["detail"]
    assert rows["Anti-cheat"]["ok"] is False and "EasyAntiCheat" in rows["Anti-cheat"]["detail"]


def test_capabilities_say_so_when_no_exe(tmp_path):
    rows = d.game_capabilities(tmp_path)
    assert rows[0]["ok"] is False
