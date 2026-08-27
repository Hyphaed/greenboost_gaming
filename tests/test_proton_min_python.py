# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""The Proton wrapper must parse on the interpreter that actually runs it.

Steam does not execute the compat tool with the host python3.  toolmanifest.vdf
declares `require_tool_appid 4183110`, so the wrapper runs inside the Steam
Linux Runtime 4.0 container, whose /usr/bin/python3 is 3.13.

That appid changed 2026-08-27 from Steam Linux Runtime 3.0 ("sniper", appid
1628350, python3.9.2): a stale manifest had every launch running in a
DIFFERENT container than the one upstream Proton 11.0 actually requires,
and sniper's own pressure-vessel-wrap had an intermittent internal fault on
the machine that found this , every launch hung indefinitely at Steamworks'
in-prefix DRM bootstrap as a result. See gb_proton_main.py's module
docstring for the full diagnosis. The wrapper's own coding floor stays
3.9 regardless (older syntax parses fine on 3.13), so this gate still
checks against that floor , it just prefers the real container first.

That gap is not academic.  On 2026-08-20 a PEP 701 f-string in the wrapper
compiled cleanly under the 3.14 host and took down every single launch with
"SyntaxError: EOL while scanning string literal".  Steam showed the game
starting and stopping one second later; dataflux recorded nothing at all,
because the wrapper died before it could emit anything.

`ast.parse(..., feature_version=(3, 9))` does NOT catch this class of error
(verified) , only a real old interpreter does.  So these tests look for one,
preferring the container Steam will genuinely use.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER_FILES = [
    REPO / "greenboost_proton" / "proton",
    REPO / "greenboost_proton" / "gb_proton_main.py",
]

STEAM_ROOTS = [
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
    Path.home() / ".steam/root",
]

# Newer than the wrapper's floor, but they still reject PEP 701 syntax, which
# is the class of bug this file exists for.
FALLBACK_INTERPRETERS = ["python3.9", "python3.10", "python3.11"]


def _runtime4_python() -> list[str] | None:
    """The real container's own python3.13 , preferred, matches production."""
    for root in STEAM_ROOTS:
        base = root / "steamapps/common/SteamLinuxRuntime_4"
        if not base.is_dir():
            continue
        for candidate in sorted(base.glob("*/files/bin/python3.*")):
            if candidate.name.endswith("-config") or not candidate.is_file():
                continue
            if shutil.which(str(candidate)):
                return [str(candidate)]
    return None


def _sniper_runner() -> list[str] | None:
    """Second real-container fallback , still a genuine old interpreter,
    just not the one Steam actually uses for this wrapper any more."""
    for root in STEAM_ROOTS:
        runner = root / "steamapps/common/SteamLinuxRuntime_sniper/run-in-sniper"
        if runner.is_file() and shutil.which(str(runner)):
            return [str(runner), "--", "python3"]
    return None


def _old_interpreter() -> list[str] | None:
    for name in FALLBACK_INTERPRETERS:
        found = shutil.which(name)
        if found:
            return [found]
    return None


def _gate() -> list[str] | None:
    return _runtime4_python() or _sniper_runner() or _old_interpreter()


@pytest.mark.parametrize("path", WRAPPER_FILES, ids=lambda p: p.name)
def test_wrapper_parses_on_steam_runtime_python(path: Path, tmp_path: Path):
    gate = _gate()
    if gate is None:
        pytest.skip(
            "no Python <= 3.11 and no Steam sniper runtime available , the "
            "check that matters cannot run here. Start Steam once so it "
            "downloads SteamLinuxRuntime_sniper, or install python3.9.")

    proc = subprocess.run(
        gate + ["-m", "py_compile", str(path)],
        capture_output=True, text=True, timeout=300,
        cwd=str(tmp_path),  # keep __pycache__ out of the repo
    )
    assert proc.returncode == 0, (
        "%s does not parse on the interpreter Steam runs it with "
        "(%s).\n\n%s\n\nThis is almost always syntax newer than 3.9: a PEP 701 "
        "f-string (a newline or same-type nested quote inside {...}), `match`, "
        "`except*`, `tomllib`, `datetime.UTC`, or `zip(strict=)`."
        % (path.name, " ".join(gate), proc.stderr.strip())
    )


def test_stub_does_not_import_the_body():
    """The stub's whole job is surviving a broken body , it must not need it.

    If the stub ever imports gb_proton_main (or anything from the project), a
    SyntaxError there takes the stub down too and the fallback is gone.
    """
    tree = ast.parse((REPO / "greenboost_proton" / "proton").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # Checked against the AST, not the text: the stub's comments legitimately
    # mention gb_proton_main while explaining why they do not import it.
    project = {"gb_proton_main", "gb_gaming", "gb_dataflux"}
    assert not (imported & project), (
        "greenboost_proton/proton imports %s , the stub has to keep working "
        "when the module it loads is broken, so it can depend on nothing from "
        "this project." % sorted(imported & project))

    stdlib_only = {"os", "sys"}
    assert imported <= stdlib_only, (
        "greenboost_proton/proton imports %s. Keep the stub to %s: every "
        "import is another way for the fallback path itself to fail."
        % (sorted(imported - stdlib_only), sorted(stdlib_only)))


def test_stub_is_in_the_installer_payload():
    """Both halves must deploy together, or Steam gets a stub with no body."""
    installer = (REPO / "greenboost_proton" / "install.sh").read_text()
    payload = next(l for l in installer.splitlines() if l.startswith("PAYLOAD="))
    for required in ("proton", "gb_proton_main.py"):
        assert required in payload, "PAYLOAD is missing %r: %s" % (required, payload)
