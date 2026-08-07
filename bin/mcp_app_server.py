#!/usr/bin/env python3
"""Serve one MCP App's HTML in a real browser.

MCP Apps (SEP-1865 / ext-apps) are HTML delivered as ``ui://`` resources. No
terminal MCP client renders them, so in a terminal session they are invisible —
a server can ship an app that is blank, broken, or unstyled and nothing says so.

This serves a single app locally and (optionally) opens it. Two details it exists
to get right, because both fail SILENTLY otherwise:

1. **http://, never file://.** MCP App HTML normally loads its component as an ES
   module. From a ``file://`` page the origin is ``null`` and module scripts are
   blocked, so the custom element never upgrades and you get an empty box that
   looks exactly like a broken app.

2. **Relative asset paths.** An app that references ``../static/foo.js`` needs
   those assets reachable. Rather than assuming any one server's layout, anything
   this server cannot find locally is proxied to ``--base-url`` (the origin that
   served the app). That keeps the viewer generic across MCP servers.

Usage:
    mcp_app_server.py --html-file app.html [--base-url https://host] [--port 8777]
    mcp_app_server.py --html-file - < app.html      # read from stdin
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from display_targets import open_app as _open_on_target  # noqa: E402


def _show(
    url: str,
    target: str | None,
    position: str = "right",
    terminal_browser: str | None = None,
    allow_text: bool = False,
    theme: str | None = None,
) -> None:
    used = _open_on_target(url, target, position, terminal_browser, allow_text, theme)
    print(f"mcp-app-viewer: displayed on {used}")

_MAX_PROXY_BYTES = 32 * 1024 * 1024


def _make_handler(root: Path, base_url: str | None):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt, *args):  # quiet by default
            if "--verbose" in sys.argv:
                super().log_message(fmt, *args)

        def send_head(self):
            path = self.translate_path(self.path)
            if Path(path).exists() or not base_url:
                return super().send_head()
            return self._proxy()

        def _proxy(self):
            """Fetch a missing asset from the origin that served the app.

            An app's relative asset paths are meaningless to a local staging dir,
            so rather than guessing a layout we ask the origin. A failure here is
            reported as a real status code — never a silent empty 200, which would
            render as a broken component with no explanation.
            """
            target = base_url.rstrip("/") + "/" + self.path.lstrip("/")
            try:
                with urllib.request.urlopen(target, timeout=20) as resp:
                    body = resp.read(_MAX_PROXY_BYTES)
                    ctype = resp.headers.get("Content-Type", "application/octet-stream")
            except urllib.error.HTTPError as exc:
                self.send_error(exc.code, f"upstream {exc.code} for {target}")
                return None
            except Exception as exc:  # noqa: BLE001 - surface, never swallow
                self.send_error(502, f"proxy failed for {target}: {exc}")
                return None
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html-file", required=True, help="app HTML file, or - for stdin")
    ap.add_argument("--base-url", default=None, help="origin to proxy missing assets to")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument(
        "--browser",
        default=None,
        help="display target: system | chrome | safari | firefox | iterm2 | vscode | terminal | none",
    )
    ap.add_argument(
        "--position",
        default="right",
        choices=["right", "left", "top", "bottom"],
        help="pane position for split-pane targets (iterm2, vscode, terminal). Default: right.",
    )
    ap.add_argument(
        "--terminal-browser",
        default=None,
        help="which terminal browser the 'terminal' target uses (default: best engine found)",
    )
    ap.add_argument(
        "--theme",
        default="auto",
        choices=["auto", "light", "dark"],
        help=(
            "colour scheme the terminal browser reports to prefers-color-scheme. "
            "auto follows the OS. Only carbonyl needs this -- real browsers already "
            "follow your system setting."
        ),
    )
    ap.add_argument(
        "--fallback-preview",
        action="store_true",
        help=(
            "allow a TEXT-ONLY terminal browser (lynx, w3m). These cannot render MCP "
            "Apps -- use this only to preview the text fallback a non-visual client gets."
        ),
    )
    ap.add_argument("--assets-dir", default=None, help="optional local dir mounted at /")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    html = sys.stdin.read() if args.html_file == "-" else Path(args.html_file).read_text("utf-8")
    if not html.strip():
        print("mcp-app-viewer: empty app HTML — nothing to render", file=sys.stderr)
        return 2

    stage = Path(tempfile.mkdtemp(prefix="mcp-app-"))
    if args.assets_dir:
        src = Path(args.assets_dir)
        if src.is_dir():
            shutil.copytree(src, stage, dirs_exist_ok=True)
    (stage / "app").mkdir(parents=True, exist_ok=True)
    (stage / "app" / "index.html").write_text(html, encoding="utf-8")

    url = f"http://127.0.0.1:{args.port}/app/index.html"
    print(f"mcp-app-viewer: serving {url}")
    if args.base_url:
        print(f"mcp-app-viewer: proxying missing assets -> {args.base_url}")
    print("mcp-app-viewer: Ctrl-C to stop")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(stage, args.base_url))
    if not args.no_open:
        threading.Timer(
            0.4,
            lambda: _show(
                url,
                args.browser,
                args.position,
                args.terminal_browser,
                args.fallback_preview,
                args.theme,
            ),
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nmcp-app-viewer: stopped")
    finally:
        server.server_close()
        shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
