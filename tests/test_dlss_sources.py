# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""DLSS library sync: x64-only Streamline, optional DLLs, Recol manifest,
model hash gate, ARM cache purge.  No network: every fetch is faked."""
from __future__ import annotations

import io
import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gb_gaming import dlss_updater as d  # noqa: E402


def pe(machine: int, pad: int = 2048) -> bytes:
    """Smallest thing _pe_machine accepts: MZ header -> PE\\0\\0 + machine."""
    b = bytearray(pad)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", b, 0x84, machine)
    return bytes(b)


X64, ARM = pe(0x8664), pe(0xAA64)


def sdk_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "LIBRARIES_DIR", tmp_path / "libraries")
    d.LIBRARIES_DIR.mkdir()
    d._STREAMLINE_ZIP_CACHE.clear()
    d._SOURCE_USED.clear()


def test_pe_machine_reads_architecture():
    assert d._pe_machine(X64) == 0x8664
    assert d._pe_machine(ARM) == 0xAA64
    assert d._pe_machine(b"not a pe") is None


def test_streamline_uses_x64_zip_and_production_dir(monkeypatch):
    good = sdk_zip({"bin/x64/sl.common.dll": X64,
                    "_artifacts/sl.common/Develop_x64/sl.common.dll": pe(0x8664, 4096),
                    "bin/x64/nvngx_deepdvc.dll": X64})
    arm = sdk_zip({"bin/x64/sl.common.dll": ARM})
    served = {"https://x/streamline-sdk-v1-aarch64.zip": arm,
              "https://x/streamline-sdk-v1.zip": good}
    monkeypatch.setattr(d, "_http_get", lambda url, **k: served.get(url))
    assets = [{"name": "streamline-sdk-v1-aarch64.zip",
               "browser_download_url": "https://x/streamline-sdk-v1-aarch64.zip"},
              {"name": "streamline-sdk-v1.zip",
               "browser_download_url": "https://x/streamline-sdk-v1.zip"}]
    bundle = d._streamline_x64_bundle("v1", assets)
    assert bundle["sl.common.dll"] == X64            # not the 4096-byte Develop build
    assert "nvngx_deepdvc.dll" in bundle             # deepdvc rides in the same zip
    assert d._pe_machine(bundle["sl.common.dll"]) == 0x8664


def test_streamline_never_accepts_arm_only_release(monkeypatch):
    arm = sdk_zip({"bin/x64/sl.common.dll": ARM})
    monkeypatch.setattr(d, "_http_get", lambda url, **k: arm)
    assets = [{"name": "streamline-sdk-v2.zip", "browser_download_url": "https://x/a.zip"}]
    assert d._streamline_x64_bundle("v2", assets) == {}


def test_recol_manifest_is_read_from_repo_root(monkeypatch):
    base = "https://raw.example/Recol/main/dlls"
    seen = []

    def fake(url, **k):
        seen.append(url)
        if url == "https://raw.example/Recol/main/manifest.json":
            return json.dumps({"nvngx_dlss.dll": {"url": "https://gh/rel/nvngx_dlss.dll"}}).encode()
        if url == "https://gh/rel/nvngx_dlss.dll":
            return X64
        return None

    monkeypatch.setattr(d, "_http_get", fake)
    ok, blob = d._fetch_community_mirror("nvngx_dlss.dll", base)
    assert ok and blob == X64
    assert seen[0] == "https://raw.example/Recol/main/manifest.json"


def test_recol_rejects_arm_file(monkeypatch):
    base = "https://raw.example/Recol/main/dlls"
    monkeypatch.setattr(d, "_http_get", lambda url, **k: ARM if url.endswith("nvngx_dlss.dll") else None)
    ok, msg = d._fetch_community_mirror("nvngx_dlss.dll", base)
    assert not ok


def test_model_hash_gate_accepts_only_allowlisted_build():
    real = b"real-nvidia-model"
    fake_same_size = b"real-nvidia-modeX"
    import hashlib
    d.TRUSTED_SHA256["nvngx_dlssnr.dll"][hashlib.sha256(real).hexdigest()] = "test build"
    try:
        assert d._trust_error("nvngx_dlssnr.dll", real) is None
        err = d._trust_error("nvngx_dlssnr.dll", fake_same_size)
        assert err and "unverified" in err
        assert d._trust_error("nvngx_dlss.dll", fake_same_size) is None   # not gated
    finally:
        d.TRUSTED_SHA256["nvngx_dlssnr.dll"].pop(hashlib.sha256(real).hexdigest())


def test_optional_dlls_do_not_fail_the_sync(monkeypatch):
    monkeypatch.setattr(d, "download_latest",
                        lambda name, dest_dir=None: (False, "not published by NVIDIA yet")
                        if d.KNOWN[name].optional else (True, d.LIBRARIES_DIR / name))
    lines: list[str] = []
    out = d.download_all_known(progress=lines.append)
    assert all(ok for ok, _ in out.values())
    assert any(l.startswith("[skip]") and "dlssnr" in l for l in lines)
    assert not any(l.startswith("[error]") for l in lines)


def test_real_failures_still_fail_the_sync(monkeypatch):
    monkeypatch.setattr(d, "download_latest", lambda name, dest_dir=None: (False, "boom"))
    out = d.download_all_known()
    assert out["sl.common.dll"][0] is False
    assert out["nvngx_dlssnr.dll"][0] is True       # optional


def test_purge_removes_arm_builds_and_fixes_manifest():
    lib = d.LIBRARIES_DIR
    (lib / "versions" / "sl.common.dll").mkdir(parents=True)
    (lib / "sl.common.dll").write_bytes(ARM)
    (lib / "versions" / "sl.common.dll" / "2.12.0.0.dll").write_bytes(X64)
    (lib / "versions" / "sl.common.dll" / "2.14.1.0.dll").write_bytes(ARM)
    (lib / "manifest.json").write_text(json.dumps({"dlls": {"sl.common.dll": {
        "version": "2.14.1.0",
        "versions": {"2.12.0.0": {}, "2.14.1.0": {}}}}}))
    gone = d._purge_non_x64_cache()
    assert sorted(gone) == ["sl.common.dll", "sl.common.dll/2.14.1.0.dll"]
    assert (lib / "versions" / "sl.common.dll" / "2.12.0.0.dll").exists()
    mf = json.loads((lib / "manifest.json").read_text())["dlls"]["sl.common.dll"]
    assert mf["version"] == "2.12.0.0" and "2.14.1.0" not in mf["versions"]


def test_deepdvc_comes_from_streamline_not_a_mirror():
    assert d.KNOWN["nvngx_deepdvc.dll"].via == "nvidia-streamline-github"
    assert d.KNOWN["nvngx_dlssnr.dll"].optional and d.KNOWN["sl.dlss_nr.dll"].optional


def test_streamline_release_is_looked_up_once_and_survives_an_api_limit(monkeypatch):
    d._STREAMLINE_REL_CACHE.clear()
    api_calls = []

    def limited(url, **k):
        api_calls.append(url)
        return None                                   # GitHub API: rate-limited

    monkeypatch.setattr(d, "_http_get", limited)
    monkeypatch.setattr(d, "_latest_tag_without_api", lambda: "v9.9.9")
    rel = d._streamline_release("")
    assert rel["tag_name"] == "v9.9.9"
    assert rel["assets"][0]["browser_download_url"].endswith("/releases/download/v9.9.9/streamline-sdk-v9.9.9.zip")
    d._streamline_release("")                         # second DLL: no new request
    assert len(api_calls) == 1


def test_streamline_release_none_when_everything_is_unreachable(monkeypatch):
    d._STREAMLINE_REL_CACHE.clear()
    monkeypatch.setattr(d, "_http_get", lambda url, **k: None)
    monkeypatch.setattr(d, "_latest_tag_without_api", lambda: None)
    assert d._streamline_release("") is None


def test_cached_versions_sort_highest_first(monkeypatch):
    lib = d.LIBRARIES_DIR
    (lib / "versions" / "sl.common.dll").mkdir(parents=True)
    for v in ("2.12.0.0", "2.14.16.0", "2.14.1.0"):
        (lib / "versions" / "sl.common.dll" / f"{v}.dll").write_bytes(X64)
    monkeypatch.setattr(d, "_rekey_versions_cache", lambda: None)
    monkeypatch.setattr(d, "_read_runtime_manifest", lambda: {"dlls": {"sl.common.dll": {"versions": {
        "2.12.0.0": {"fetched_at": 300}, "2.14.16.0": {"fetched_at": 100}, "2.14.1.0": {"fetched_at": 200}}}}})
    got = [e["version"] for e in d.list_cached_dlls() if e["name"] == "sl.common.dll"]
    assert got == ["2.14.16.0", "2.14.1.0", "2.12.0.0"]       # not fetch order
