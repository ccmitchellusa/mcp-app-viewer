#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Focused tests for editor-host discovery in bin/display_targets.py.

Runs directly with stdlib only:

    python3 test/display_targets_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

import display_targets  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    suffix = f" — {detail}" if detail and not ok else ""
    print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")


def test_editor_cli_from_text() -> None:
    cases = {
        "Cursor Helper (Plugin)": "cursor",
        "Windsurf": "windsurf",
        "Bob IDE": "bob",
        "Visual Studio Code": "code",
        "Visual Studio Code - Insiders": "code-insiders",
        "VSCodium": "codium",
        "": "",
        "plain terminal": "",
    }
    for raw, expected in cases.items():
        actual = display_targets._editor_cli_from_text(raw)
        check(f"text hint: {raw or '<empty>'}", actual == expected, f"got {actual!r}, expected {expected!r}")


def test_host_hint_prefers_process_over_generic_term_program() -> None:
    old_term_program = os.environ.get("TERM_PROGRAM")
    old_term_program_version = os.environ.get("TERM_PROGRAM_VERSION")
    try:
        os.environ["TERM_PROGRAM"] = "vscode"
        os.environ["TERM_PROGRAM_VERSION"] = "1.0"
        old_fn = display_targets._editor_host_process_lines
        display_targets._editor_host_process_lines = lambda: ["/Applications/Cursor.app/Contents/MacOS/Cursor"]
        try:
            actual = display_targets._host_editor_cli_hint()
        finally:
            display_targets._editor_host_process_lines = old_fn
        check("host hint prefers process ancestry over TERM_PROGRAM=vscode", actual == "cursor", f"got {actual!r}")
    finally:
        if old_term_program is None:
            os.environ.pop("TERM_PROGRAM", None)
        else:
            os.environ["TERM_PROGRAM"] = old_term_program
        if old_term_program_version is None:
            os.environ.pop("TERM_PROGRAM_VERSION", None)
        else:
            os.environ["TERM_PROGRAM_VERSION"] = old_term_program_version


def test_resolve_editor_cli_sources() -> None:
    old_env = os.environ.get("MCP_APP_EDITOR_CLI")
    old_host = display_targets._host_editor_cli_hint
    old_which = display_targets.shutil.which
    try:
        display_targets._host_editor_cli_hint = lambda: "cursor"
        display_targets.shutil.which = lambda name: f"/mock/{name}" if name in {"cursor", "code", "custom-code"} else None

        os.environ["MCP_APP_EDITOR_CLI"] = "custom-code"
        cli, source, host_hint = display_targets._resolve_editor_cli()
        check("resolve source: explicit env wins", (cli, source, host_hint) == ("custom-code", "env", ""), f"got {(cli, source, host_hint)!r}")

        os.environ.pop("MCP_APP_EDITOR_CLI", None)
        cli, source, host_hint = display_targets._resolve_editor_cli()
        check("resolve source: host hint wins over PATH order", (cli, source, host_hint) == ("cursor", "host", "cursor"), f"got {(cli, source, host_hint)!r}")

        display_targets._host_editor_cli_hint = lambda: "windsurf"
        cli, source, host_hint = display_targets._resolve_editor_cli()
        check("resolve source: PATH fallback when host hint binary missing", (cli, source, host_hint) == ("code", "path", "windsurf"), f"got {(cli, source, host_hint)!r}")
    finally:
        if old_env is None:
            os.environ.pop("MCP_APP_EDITOR_CLI", None)
        else:
            os.environ["MCP_APP_EDITOR_CLI"] = old_env
        display_targets._host_editor_cli_hint = old_host
        display_targets.shutil.which = old_which


def test_editor_profile_exposes_source_and_hint() -> None:
    old_resolve = display_targets._resolve_editor_cli
    old_find = display_targets._find_product_json
    old_which = display_targets.shutil.which
    old_uri = os.environ.get("MCP_APP_EDITOR_URI_SCHEME")
    old_folder = os.environ.get("MCP_APP_EDITOR_DATA_FOLDER")
    old_ext = os.environ.get("MCP_APP_EDITOR_EXT_DIR")
    try:
        display_targets._resolve_editor_cli = lambda: ("cursor", "host", "cursor")
        display_targets._find_product_json = lambda _path: {
            "urlProtocol": "cursor",
            "dataFolderName": ".cursor",
        }
        display_targets.shutil.which = lambda name: f"/mock/{name}" if name == "cursor" else None
        os.environ.pop("MCP_APP_EDITOR_URI_SCHEME", None)
        os.environ.pop("MCP_APP_EDITOR_DATA_FOLDER", None)
        os.environ.pop("MCP_APP_EDITOR_EXT_DIR", None)

        profile = display_targets.editor_profile()
        ok = (
            profile["cli"] == "cursor"
            and profile["url_protocol"] == "cursor"
            and profile["data_folder"] == ".cursor"
            and profile["extensions_dir"].endswith("/.cursor/extensions")
            and profile["source"] == "host"
            and profile["host_hint"] == "cursor"
            and profile["found"] is True
        )
        check("editor_profile reports source, host_hint, and discovered fork data", ok, str(profile))
    finally:
        display_targets._resolve_editor_cli = old_resolve
        display_targets._find_product_json = old_find
        display_targets.shutil.which = old_which
        if old_uri is None:
            os.environ.pop("MCP_APP_EDITOR_URI_SCHEME", None)
        else:
            os.environ["MCP_APP_EDITOR_URI_SCHEME"] = old_uri
        if old_folder is None:
            os.environ.pop("MCP_APP_EDITOR_DATA_FOLDER", None)
        else:
            os.environ["MCP_APP_EDITOR_DATA_FOLDER"] = old_folder
        if old_ext is None:
            os.environ.pop("MCP_APP_EDITOR_EXT_DIR", None)
        else:
            os.environ["MCP_APP_EDITOR_EXT_DIR"] = old_ext


def test_iterm2_readiness_accepts_36_without_the_advanced_key() -> None:
    """3.6 retired `browserProfiles`; gating on it alone refused every 3.6 machine."""
    import subprocess as sp

    real_run = display_targets.subprocess.run
    real_version = display_targets._iterm2_version
    empty_prefs = b'<?xml version="1.0"?><plist version="1.0"><dict/></plist>'
    try:
        display_targets.subprocess.run = lambda *a, **k: sp.CompletedProcess(
            a, 0, stdout=empty_prefs, stderr=b"")
        display_targets._iterm2_version = lambda: (3, 6, 11)
        ready, why = display_targets._iterm2_browser_readiness()
        check("readiness: 3.6.11 with no browserProfiles key is ready", ready, why)

        display_targets._iterm2_version = lambda: (3, 5, 14)
        ready, why = display_targets._iterm2_browser_readiness()
        check("readiness: 3.5.x refused, and the remedy names the version",
              not ready and "3.5.14" in why and "3.6" in why, why)
    finally:
        display_targets.subprocess.run = real_run
        display_targets._iterm2_version = real_version


def test_iterm2_profile_write_is_atomic() -> None:
    """iTerm2 closes sessions whose profile vanishes; the file must never be partial."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "mcp-app-viewer.json"
        old_dir = display_targets._ITERM2_PROFILE_DIR
        try:
            display_targets._ITERM2_PROFILE_DIR = Path(tmp)
            ok, changed = display_targets._write_iterm2_profile(
                "http://127.0.0.1:8777/app/index.html", "MCP App Viewer", target)
            profile = json.loads(target.read_text())["Profiles"][0]
            check("profile write: succeeds and reports the change",
                  ok and changed, f"ok={ok} changed={changed}")
            check("profile write: carries browser mode and the initial URL",
                  profile["Custom Command"] == "Browser"
                  and profile["Initial URL"].endswith("/app/index.html"), str(profile))
            check("profile write: leaves no staging file for the watcher to see",
                  not list(Path(tmp).glob("*.incoming")),
                  str([p.name for p in Path(tmp).glob("*")]))

            ok, changed = display_targets._write_iterm2_profile(
                "http://127.0.0.1:8777/app/index.html", "MCP App Viewer", target)
            check("profile write: identical content is not rewritten",
                  ok and not changed, f"ok={ok} changed={changed}")
        finally:
            display_targets._ITERM2_PROFILE_DIR = old_dir


if __name__ == "__main__":
    test_iterm2_readiness_accepts_36_without_the_advanced_key()
    test_iterm2_profile_write_is_atomic()
    test_editor_cli_from_text()
    test_host_hint_prefers_process_over_generic_term_program()
    test_resolve_editor_cli_sources()
    test_editor_profile_exposes_source_and_hint()
    failures = [name for name, ok, _detail in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} passed")
    sys.exit(1 if failures else 0)
