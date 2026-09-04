#!/usr/bin/env python3
"""Contract checks for the bundled VS Code companion extension.

This is intentionally lightweight: no VS Code runtime, no npm, no external test
framework. It verifies that the runtime-side launcher contract in
`bin/display_targets.py` still matches the metadata and behavior documented by
`vscode-extension/package.json` and `vscode-extension/extension.js`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()
PACKAGE = json.loads((REPO / "vscode-extension" / "package.json").read_text(encoding="utf-8"))
EXTENSION_JS = (REPO / "vscode-extension" / "extension.js").read_text(encoding="utf-8")
DISPLAY_TARGETS = (REPO / "bin" / "display_targets.py").read_text(encoding="utf-8")
CONTRACT = (REPO / "vscode-extension" / "CONTRACT.md").read_text(encoding="utf-8")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    suffix = f" — {detail}" if detail and not ok else ""
    print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")


# Metadata stays aligned with the repo release stream.
check("package version matches repo VERSION", PACKAGE.get("version") == VERSION, f"{PACKAGE.get('version')} != {VERSION}")
check("package main is extension.js", PACKAGE.get("main") == "./extension.js", f"main={PACKAGE.get('main')!r}")
check("activation includes onUri", "onUri" in PACKAGE.get("activationEvents", []), str(PACKAGE.get("activationEvents")))
check("package is not private", "private" not in PACKAGE, str(PACKAGE.get("private")))
check("package has repository metadata", PACKAGE.get("repository", {}).get("url") == "https://github.com/ccmitchellusa/mcp-app-viewer.git", str(PACKAGE.get("repository")))
check("package has homepage metadata", PACKAGE.get("homepage") == "https://github.com/ccmitchellusa/mcp-app-viewer#readme", str(PACKAGE.get("homepage")))
check("package has bugs metadata", PACKAGE.get("bugs", {}).get("url") == "https://github.com/ccmitchellusa/mcp-app-viewer/issues", str(PACKAGE.get("bugs")))
check("package has keywords", isinstance(PACKAGE.get("keywords"), list) and len(PACKAGE.get("keywords", [])) >= 3, str(PACKAGE.get("keywords")))

extension_id = f"{PACKAGE.get('publisher')}.{PACKAGE.get('name')}"
runtime_id_match = re.search(r'_VSCODE_HELPER_EXTENSION\s*=\s*"([^"]+)"', DISPLAY_TARGETS)
check("runtime helper extension constant present", runtime_id_match is not None)
runtime_id = runtime_id_match.group(1) if runtime_id_match else ""
check("runtime helper extension matches package id", runtime_id == extension_id, f"runtime={runtime_id!r} package={extension_id!r}")

# Runtime and extension agree on the URI path contract.
check("runtime emits /open handler path", '/open' in DISPLAY_TARGETS)
check("extension documents /open handler path", '/open' in EXTENSION_JS)
check("contract documents /open handler path", '/open' in CONTRACT)

# Runtime and extension agree on the position contract.
expected_positions = {"right", "left", "top", "bottom"}
actual_positions = set(re.findall(r"\b(right|left|top|bottom)\b", CONTRACT))
check("contract mentions all four positions", expected_positions.issubset(actual_positions), f"found={sorted(actual_positions)}")
for token in expected_positions:
    check(f"extension supports position {token}", token in EXTENSION_JS)

# The extension must guard the handler URI to loopback http only.
for host in ["127.0.0.1", "localhost", "::1", "[::1]"]:
    check(f"allowed host listed: {host}", host in EXTENSION_JS)
check("extension requires http protocol", "parsed.protocol !== 'http:'" in EXTENSION_JS)

# The editor side still uses the documented Simple Browser commands.
check("extension uses simpleBrowser.api.open", "simpleBrowser.api.open" in EXTENSION_JS)
check("extension falls back to simpleBrowser.show", "simpleBrowser.show" in EXTENSION_JS)

# Runtime still verifies installation and emits the editor-specific scheme URI.
check("runtime checks installed extensions", "--list-extensions" in DISPLAY_TARGETS)
check("runtime emits handler URI using discovered scheme", "profile['url_protocol']" in DISPLAY_TARGETS)
check("runtime shells out with --open-url", "--open-url" in DISPLAY_TARGETS)

# README/contract drift checks.
check("contract documents extension id", extension_id in CONTRACT)
check("contract documents allowed hosts", all(host in CONTRACT for host in ["127.0.0.1", "localhost", "::1", "[::1]"]))

failures = [name for name, ok, _detail in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} passed")
if failures:
    sys.exit(1)
