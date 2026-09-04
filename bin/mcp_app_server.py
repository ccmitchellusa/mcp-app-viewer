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

3. **A host, not just a web server.** An MCP App gets its data over postMessage from
   the host that embedded it, and ``client-bridge.js`` refuses to talk to anything
   that is not its parent frame. Serving the app top-level therefore renders the
   chrome and *never* the data — a permanently empty app that looks like a broken
   server. So the app is embedded in a shell that answers ``ui/initialize``, declares
   a theme, and delivers ``--data-file`` as a tool result.

Usage:
    mcp_app_server.py --html-file app.html [--base-url https://host] [--port 8777]
    mcp_app_server.py --html-file - < app.html      # read from stdin
    mcp_app_server.py --html-file app.html --data-file result.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
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
    fallback: bool = True,
    pane_profile: str | None = None,
    anchor: str | None = None,
    pane_slot: str | None = None,
    pane_near: str | None = None,
) -> None:
    used = _open_on_target(
        url, target, position, terminal_browser, allow_text, theme, fallback,
        pane_profile, anchor, pane_slot, pane_near,
    )
    print(f"mcp-app-viewer: displayed on {used}")

_MAX_PROXY_BYTES = 32 * 1024 * 1024

# The host shell. Served AS `/app/index.html`; the app itself moves to
# `/app/content.html` and runs inside the iframe.
#
# WHY A SHELL AND NOT THE APP DIRECTLY
# ------------------------------------
# An MCP App receives its data over postMessage from its host. `client-bridge.js`
# gates the whole channel on being embedded — `isEmbedded()` is
# `window.parent !== window`, `connect()` returns null when that is false, and the
# inbound guard rejects anything whose `event.source` is not `window.parent`. Served
# top-level, as this viewer used to, every one of those checks fails and NOTHING can
# ever deliver a tool result. The app renders its empty state forever ("Waiting for
# a location…" for the map demo) and looks for all the world like a server that
# returned nothing.
#
# So the viewer has to BE a host, minimally: answer `ui/initialize`, declare a theme,
# and hand over the tool result. That is this file.
#
# The app keeps its directory (`/app/`), so relative asset paths like
# `../../src/client-bridge.js` resolve exactly as before.
_HOST_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP App</title>
<style>
  html, body { margin: 0; height: 100%; color-scheme: light dark;
               background: light-dark(#f6f8f8, #0f1518); }
  iframe { border: 0; display: block; width: 100%; height: 100%; }
</style>
</head>
<body>
<iframe id="app" src="./content.html"></iframe>
<script>
  var VERSION = "__VERSION__";       // this render; changes when new HTML is staged
  var THEME_SETTING = "__THEME__";   // auto | light | dark
  var DATA = __DATA__;               // the tool's structuredContent, or null
  var MCP_ENDPOINT = "__MCP_ENDPOINT__"; // local /mcp-tool-call when proxy is active, else ""

  // What a host SENDS an app. The app consumes these as --color-*; its own
  // light-dark() fallbacks only apply when a host declares nothing.
  var PALETTE = {
    light: { '--color-background-primary': '#f6f8f8', '--color-text-primary': '#132024',
             '--color-text-secondary': '#52676f', '--color-border-primary': '#c9d5d9',
             '--color-text-info': '#2d83a8' },
    dark:  { '--color-background-primary': '#0f1518', '--color-text-primary': '#edf5f7',
             '--color-text-secondary': '#abc0c8', '--color-border-primary': '#344a54',
             '--color-text-info': '#7fd0f3' }
  };

  // The spec types theme as "light" | "dark" — there is no "system". A host that
  // wants to follow the OS must RESOLVE it and declare the answer.
  function resolveTheme() {
    if (THEME_SETTING === 'light' || THEME_SETTING === 'dark') return THEME_SETTING;
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  // SELF-UPDATING, so a render can reuse this pane instead of opening another.
  // The URL never changes; only the HTML behind it does, and nothing would
  // otherwise tell this page to reload. A pane showing the PREVIOUS app is worse
  // than a missing pane: it looks like a rendered app, so you conclude the current
  // one renders fine when you have not seen it at all.
  //
  // Full reload, not just the iframe: this shell carries the tool result baked in,
  // so new data means a new shell. A fetch error is the server restarting between
  // renders — ignore it and poll again.
  setInterval(function () {
    fetch('/version', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (v) { if (v && v.version && v.version !== VERSION) location.reload(); })
      .catch(function () {});
  }, 1000);

  var frame = document.getElementById('app');
  function post(msg) { frame.contentWindow.postMessage(msg, '*'); }

  function hostContext(theme) {
    return {
      theme: theme,
      styles: { variables: PALETTE[theme] },
      displayMode: 'inline',
      availableDisplayModes: ['inline'],
      platform: 'desktop',
      userAgent: 'mcp-app-viewer'
    };
  }

  function toolResultPayload() {
    if (DATA === null || DATA === undefined) return null;
    if (typeof DATA === 'object' &&
        (Object.prototype.hasOwnProperty.call(DATA, 'structuredContent') ||
         Object.prototype.hasOwnProperty.call(DATA, 'content') ||
         Object.prototype.hasOwnProperty.call(DATA, 'isError') ||
         Object.prototype.hasOwnProperty.call(DATA, '_meta'))) {
      return DATA;
    }
    return { structuredContent: DATA };
  }

  // Durable, because the point is for the AGENT to read it after the fact. Resolves
  // false rather than throwing: the caller turns that into a JSON-RPC error, so a
  // failed write reaches the app instead of vanishing.
  function record(m) {
    return fetch('/app-event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method: m.method, params: m.params === undefined ? null : m.params })
    }).then(function (r) { return r.ok; }).catch(function () { return false; });
  }

  addEventListener('message', function (e) {
    // Only our own app frame. The bridge constrains its inbound side the same way.
    if (e.source !== frame.contentWindow) return;
    var m = e.data;
    if (!m || m.jsonrpc !== '2.0') return;

    // ---- the app talking BACK -------------------------------------------
    // An MCP App is not a picture. It can push context to the model, ask the host
    // to run a tool, or send a message. A real client acts on these. This viewer
    // is not connected to the MCP server, so it RECORDS them and the agent reads
    // them with `/mcp-app events`.
    if (m.method === 'ui/update-model-context' || m.method === 'ui/message') {
      record(m).then(function (ok) {
        if (!('id' in m)) return;
        post(ok ? { jsonrpc: '2.0', id: m.id, result: {} }
                : { jsonrpc: '2.0', id: m.id, error: { code: -32603,
                    message: 'mcp-app-viewer could not record the event' } });
      });
      return;
    }

    if (m.method === 'tools/call') {
      // When MCP_ENDPOINT is configured, proxy tool calls to the real MCP server
      // so interactive apps (KG viewer buttons, etc.) actually work.
      if (typeof MCP_ENDPOINT === 'string' && MCP_ENDPOINT) {
        var callId = m.id;
        fetch('/mcp-tool-call', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: m.params && m.params.name, arguments: m.params && m.params.arguments })
        })
          .then(function (r) { return r.json(); })
          .then(function (result) {
            if (!('id' in m)) return;
            if (result && result.error) {
              post({ jsonrpc: '2.0', id: callId, error: result.error });
            } else {
              post({ jsonrpc: '2.0', id: callId, result: result });
            }
          })
          .catch(function (err) {
            if (!('id' in m)) return;
            post({ jsonrpc: '2.0', id: callId, error: { code: -32603, message: String(err) } });
          });
        return;
      }
      // No MCP endpoint configured: record and refuse.
      record(m).then(function (ok) {
        if (!('id' in m)) return;
        post({ jsonrpc: '2.0', id: m.id, error: { code: -32601, message:
          ok ? 'mcp-app-viewer is a preview host and cannot execute tools; the request was recorded for the agent (/mcp-app events)'
             : 'mcp-app-viewer is a preview host and cannot execute tools' } });
      });
      return;
    }

    if (m.method !== 'ui/initialize') {
      // Everything else this preview host does not implement, e.g.
      // ui/request-display-mode, ui/download-file. Answer with a real JSON-RPC
      // error rather than staying silent — client-bridge's sendRequest has no
      // timeout, so an unanswered request leaves the app awaiting a promise that
      // never settles. A rejection says "this host cannot do that"; a hang says
      // nothing at all, forever.
      if ('id' in m) {
        post({ jsonrpc: '2.0', id: m.id, error: { code: -32601,
               message: 'mcp-app-viewer is a preview host; ' + m.method + ' is not implemented' } });
      }
      return;
    }

    var t = resolveTheme();
    var context = hostContext(t);
    document.documentElement.style.colorScheme = t;
    post({ jsonrpc: '2.0', id: m.id, result: {
      protocolVersion: '2025-06-18',
      hostInfo: { name: 'mcp-app-viewer', version: '1' },
      hostCapabilities: {},
      hostContext: context
    } });

    var result = toolResultPayload();
    setTimeout(function () {
      post({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed',
             params: context });
    }, 0);

    if (result === null) return;
    // setTimeout(0), not a bare call. The bridge's own documented usage is
    // `await connect(); onToolResult(...)` — the handler is registered in a
    // continuation that runs after the initialize RESPONSE is handled. Posting the
    // notification synchronously can beat that registration, and a missed
    // tool-result is indistinguishable from a host that never sent one. Yielding a
    // task lets the microtask queue drain first.
    setTimeout(function () {
      post({ jsonrpc: '2.0', method: 'ui/notifications/tool-result',
             params: result });
    }, 0);
  });
</script>
</body>
</html>
"""


def _render_shell(theme: str, data_file: str | None, version: str, mcp_endpoint: str = "") -> str:
    """The host shell with its theme and payload baked in.

    Bad JSON degrades to "no data" — the app shows its empty state, which is honest —
    rather than a syntax error inside the script tag, which would kill the shell and
    take the app's chrome down with it for a reason nothing on screen would explain.
    """
    data_json = "null"
    if data_file:
        raw = Path(data_file).read_text("utf-8").strip()
        if raw:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                print(
                    f"mcp-app-viewer: --data-file is not valid JSON ({exc}) — "
                    "serving the app with no data",
                    file=sys.stderr,
                )
            else:
                # `</script>` anywhere in the payload would close the tag early. The
                # HTML parser does not care that it sits inside a JSON string.
                data_json = raw.replace("</", "<\\/")
    return (_HOST_SHELL.replace("__THEME__", theme)
            .replace("__DATA__", data_json)
            .replace("__VERSION__", version)
            .replace("__MCP_ENDPOINT__", mcp_endpoint or ""))


_EVENTS_LOCK = threading.Lock()
_MAX_EVENT_BYTES = 256 * 1024


def _asset_base_url(base_url: str, html: str) -> str:
    """Compute the correct asset base URL for a proxied MCP App.

    An MCP App's HTML may live at a subdirectory of the MCP server root
    (e.g. /mcp/widgets/kg-viewer/) while ``base_url`` only points at the
    server root (e.g. https://host/mcp).  Relative asset imports in the HTML
    then need to be proxied to the subdirectory, not the root.

    The app advertises its own location via::

        <meta name="mcp-app-resource" content="ui://<server>/<widget>">

    This function converts that URI to the correct proxy base:
        ui://ibmcloud/kg-viewer  +  https://host/mcp
        =>  https://host/mcp/widgets/kg-viewer
    """
    import re as _re
    m = _re.search(r'<meta\s[^>]*name="mcp-app-resource"\s[^>]*content="ui://[^/]*/([^"]+)"',
                   html, _re.IGNORECASE)
    if not m:
        # Also try content before name ordering
        m = _re.search(r'<meta\s[^>]*content="ui://[^/]*/([^"]+)"[^>]*name="mcp-app-resource"',
                       html, _re.IGNORECASE)
    if m:
        widget = m.group(1).strip("/")
        return base_url.rstrip("/") + "/widgets/" + widget
    return base_url


def _make_handler(root: Path, base_url: str | None, events_file: Path | None,
                  version: str, heartbeat: Path | None, event_label: str | None,
                  proxy_headers: dict | None = None):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt, *args):  # quiet by default
            if "--verbose" in sys.argv:
                super().log_message(fmt, *args)

        def send_head(self):
            if self.path == "/version":
                return self._version()
            if self.path == "/app-events":
                return self._read_events()
            path = self.translate_path(self.path)
            if Path(path).exists() or not base_url:
                return super().send_head()
            # The iframe lives at /app/content.html so its relative imports resolve
            # to /app/<asset>. If that file isn't found at stage/app/<asset>, check
            # whether it exists at stage/<asset> (copied there by --assets-dir).
            # If so, rewrite self.path so SimpleHTTPRequestHandler finds it.
            if self.path.startswith("/app/") and not Path(path).exists():
                alt_path = self.translate_path("/" + self.path[len("/app/"):])
                if Path(alt_path).exists():
                    self.path = "/" + self.path[len("/app/"):]
                    return super().send_head()
            return self._proxy()

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
            """Handle app event recording and MCP tool-call proxying."""
            if self.path == "/mcp-tool-call":
                self._mcp_tool_call()
                return
            if self.path != "/app-event":
                self.send_error(404, "no such endpoint")
                return
            if events_file is None:
                self.send_error(503, "viewer started without --events-file")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_EVENT_BYTES:
                self.send_error(413, "event body missing or larger than 256KB")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.send_error(400, f"event is not JSON: {exc}")
                return

            record = {
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                # Which pane it came from. With several apps on screen at once,
                # "the user moved the map" is ambiguous without this.
                "pane": event_label or "main",
                "event": payload,
            }
            # Threading server: two events from one gesture can land at once, and a
            # half-written line would corrupt the JSONL for every later reader.
            with _EVENTS_LOCK:
                with open(events_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"mcp-app-viewer: app event recorded ({payload.get('method', '?')})")

            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _mcp_tool_call(self):
            """Proxy a tools/call from the app iframe to the upstream MCP server.

            The shell JS posts {name, arguments} here; we wrap it in a
            Streamable-HTTP MCP request (initialize + tools/call), forward the
            Bearer + Cookie auth headers, and return the tool result JSON.
            """
            if not base_url or not (proxy_headers or {}).get("Authorization"):
                self.send_error(503, "no MCP endpoint or auth configured")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:
                self.send_error(400, f"bad request: {exc}")
                return

            tool_name = payload.get("name", "")
            tool_args = payload.get("arguments") or {}

            # Derive the MCP endpoint: strip any trailing path beyond /mcp
            from urllib.parse import urlparse as _up
            _parsed = _up(base_url)
            # base_url is e.g. https://host/mcp  — use it directly as the MCP endpoint
            mcp_url = base_url.rstrip("/")

            req_headers = dict(proxy_headers or {})
            req_headers["Content-Type"] = "application/json"
            req_headers["Accept"] = "application/json, text/event-stream"

            # Streamable HTTP: single POST with initialize
            init_body = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05",
                           "capabilities": {},
                           "clientInfo": {"name": "mcp-app-viewer", "version": "1"}}
            }).encode("utf-8")
            req_headers["Content-Length"] = str(len(init_body))

            result_body: bytes = b""
            try:
                import urllib.request as _ur
                import urllib.error as _ue

                # Initialize to get a session ID
                init_req = _ur.Request(mcp_url, data=init_body, headers=req_headers, method="POST")
                session_id = None
                with _ur.urlopen(init_req, timeout=15) as resp:
                    session_id = resp.headers.get("Mcp-Session-Id")

                # Now call the tool
                call_payload = json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": tool_name, "arguments": tool_args}
                }).encode("utf-8")
                call_headers = dict(req_headers)
                call_headers["Content-Length"] = str(len(call_payload))
                if session_id:
                    call_headers["Mcp-Session-Id"] = session_id

                call_req = _ur.Request(mcp_url, data=call_payload, headers=call_headers, method="POST")
                with _ur.urlopen(call_req, timeout=60) as resp:
                    raw = resp.read(32 * 1024 * 1024)
                    # Streamable HTTP may return SSE; extract last data: line
                    text = raw.decode("utf-8", errors="replace")
                    if text.startswith("data:"):
                        lines = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")]
                        last_data = lines[-1] if lines else "{}"
                    else:
                        last_data = text
                    parsed = json.loads(last_data)
                    # Extract result from JSON-RPC envelope
                    if "result" in parsed:
                        result_body = json.dumps(parsed["result"]).encode("utf-8")
                    elif "error" in parsed:
                        result_body = json.dumps({"error": parsed["error"]}).encode("utf-8")
                    else:
                        result_body = json.dumps(parsed).encode("utf-8")

            except Exception as exc:
                err = json.dumps({"error": {"code": -32603, "message": str(exc)}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result_body)))
            self.end_headers()
            self.wfile.write(result_body)

        def _version(self):
            """The token the open pane compares against, and its liveness ping.

            Two jobs in one request. The page polls this to learn that the HTML
            behind its URL changed (the URL never does), and the mtime of the
            heartbeat is how the NEXT render knows a pane is still watching. A
            closed pane stops polling, so a stale heartbeat is self-correcting —
            no callback exists to tell us a pane was closed.
            """
            if heartbeat is not None:
                try:
                    heartbeat.write_text(version, encoding="utf-8")
                except OSError:
                    pass
            body = json.dumps({"version": version}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

        def _read_events(self):
            body = b""
            if events_file is not None and events_file.exists():
                body = events_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

        def _proxy(self):
            """Fetch a missing asset from the origin that served the app.

            An app's relative asset paths are meaningless to a local staging dir,
            so rather than guessing a layout we ask the origin. A failure here is
            reported as a real status code — never a silent empty 200, which would
            render as a broken component with no explanation.
            """
            # Avoid double-prefixing: if the base URL's path tail already
            # appears at the start of self.path, strip it once so we don't
            # produce e.g. https://host/mcp/mcp/widgets/kg-viewer/.
            from urllib.parse import urlparse as _urlparse
            _base_path = _urlparse(base_url).path.rstrip("/")
            _req_path = "/" + self.path.lstrip("/")
            if _base_path and _req_path.startswith(_base_path + "/"):
                _req_path = _req_path[len(_base_path):]
            # The app HTML lives at /app/content.html (inside the host shell's
            # iframe), so relative asset requests from it resolve to /app/<asset>.
            # But the origin serves those assets at the base URL root, not under
            # /app/. Strip the leading /app/ prefix so the proxy fetches the right
            # path instead of 404ing on e.g. /mcp/app/activity-pane.js.
            if _req_path.startswith("/app/"):
                _req_path = _req_path[len("/app"):]
            target = base_url.rstrip("/") + _req_path
            try:
                req = urllib.request.Request(target, headers=proxy_headers or {})
                with urllib.request.urlopen(req, timeout=20) as resp:
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
    ap.add_argument(
        "--no-fallback",
        action="store_true",
        help=(
            "do NOT open the OS browser when the chosen target is unavailable. For "
            "callers that will display the app themselves -- mcp-app.sh renders the "
            "'terminal' target inline when nothing can split, and without this both "
            "browsers open at once."
        ),
    )
    ap.add_argument("--assets-dir", default=None, help="optional local dir mounted at /")
    ap.add_argument("--pane-profile", default=None,
                    help="iTerm2 dynamic-profile name for this pane (one per slot)")
    ap.add_argument("--pane-anchor", default=None,
                    help="iTerm2 session id to split FROM, so a slot lands where asked")
    ap.add_argument("--pane-slot", default=None,
                    help="name of the pane this render belongs to")
    ap.add_argument("--pane-near", default=None,
                    help="name of the pane to place this one beside (editor targets)")
    ap.add_argument("--event-label", default=None,
                    help="slot name recorded with each app event, so events name their pane")
    ap.add_argument(
        "--heartbeat-file",
        default=None,
        help=(
            "file touched whenever the open pane polls /version. Its age is how the "
            "next render decides whether a pane is still alive to reuse."
        ),
    )
    ap.add_argument(
        "--events-file",
        default=None,
        help=(
            "JSONL file to append what the app pushes back (ui/update-model-context, "
            "tools/call, ui/message). Without it those calls are refused instead of "
            "recorded. Read it with 'mcp-app.sh events'."
        ),
    )
    ap.add_argument(
        "--data-file",
        default=None,
        help=(
            "JSON file holding the tool result's structuredContent. The host shell "
            "delivers it to the app as ui/notifications/tool-result. Without it the "
            "app renders its empty state, because no host ever sent it data."
        ),
    )
    ap.add_argument(
        "--proxy-header",
        metavar="NAME:VALUE",
        action="append",
        default=[],
        help=(
            "extra request header forwarded on every proxied fetch (repeatable). "
            "Example: --proxy-header 'Authorization: Bearer <token>'. "
            "When --base-url is set and no --proxy-header is given the server also "
            "tries ~/.bob/settings/mcp.json (mcpServers.ibmcloud.headers) as a "
            "fallback so the IBM Cloud KG viewer works without manual flag threading."
        ),
    )
    ap.add_argument(
        "--proxy-cookie",
        metavar="NAME=VALUE",
        action="append",
        default=[],
        help=(
            "session cookie forwarded on every proxied fetch (repeatable). "
            "Example: --proxy-cookie 'session=abc123'. "
            "When --base-url is set and no --proxy-cookie is given the server also "
            "tries ~/.config/mcp-app-viewer/state (proxy_cookie key) as a fallback."
        ),
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    # Build proxy_headers from --proxy-header flags, then fall back to mcp.json.
    proxy_headers: dict[str, str] = {}
    for raw in args.proxy_header:
        if ":" in raw:
            k, _, v = raw.partition(":")
            proxy_headers[k.strip()] = v.strip()
    if not proxy_headers and args.base_url:
        try:
            import json as _json
            _cfg = Path.home() / ".bob" / "settings" / "mcp.json"
            _data = _json.loads(_cfg.read_text("utf-8"))
            for _srv in _data.get("mcpServers", {}).values():
                _u = (_srv.get("url") or "").rstrip("/")
                _b = args.base_url.rstrip("/")
                if _u and _b.startswith(_u.rsplit("/mcp", 1)[0]):
                    proxy_headers.update(_srv.get("headers", {}))
                    break
        except Exception:  # noqa: BLE001
            pass

    # Build proxy_cookies from --proxy-cookie flags, then fall back to state file.
    proxy_cookies: list[str] = list(args.proxy_cookie)
    if not proxy_cookies and args.base_url:
        try:
            _state = Path.home() / ".config" / "mcp-app-viewer" / "state"
            for line in _state.read_text("utf-8").splitlines():
                if line.startswith("proxy_cookie="):
                    proxy_cookies.append(line[len("proxy_cookie="):])
                    break
        except Exception:  # noqa: BLE001
            pass
    # Merge all cookie fragments into a single Cookie header value.
    if proxy_cookies:
        proxy_headers["Cookie"] = "; ".join(proxy_cookies)

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
    # The app is the FRAME, not the page. `index.html` is the host shell that embeds
    # it; keeping both in `/app/` leaves the app's relative asset paths untouched and
    # leaves the served URL unchanged.
    (stage / "app" / "content.html").write_text(html, encoding="utf-8")
    # Monotonic by construction and distinct per server start, which is what the
    # open pane compares against.
    version = str(time.time_ns())
    (stage / "app" / "index.html").write_text(
        _render_shell(args.theme, args.data_file, version,
                      mcp_endpoint="/mcp-tool-call" if (args.base_url and proxy_headers.get("Authorization")) else ""),
        encoding="utf-8"
    )

    # Resolve the correct asset base URL from the app HTML's mcp-app-resource
    # meta tag so that relative asset imports proxy to the right subdirectory.
    asset_base_url = _asset_base_url(args.base_url, html) if args.base_url else None

    url = f"http://127.0.0.1:{args.port}/app/index.html"
    print(f"mcp-app-viewer: serving {url}")
    if args.data_file:
        print("mcp-app-viewer: delivering tool result to the app")
    if args.base_url:
        effective = asset_base_url or args.base_url
        print(f"mcp-app-viewer: proxying missing assets -> {effective}")
        if effective != args.base_url:
            print(f"mcp-app-viewer: asset subpath resolved from mcp-app-resource meta tag")
        if proxy_headers:
            injected = ", ".join(k for k in proxy_headers)
            print(f"mcp-app-viewer: forwarding proxy headers: {injected}")
        if proxy_cookies:
            print(f"mcp-app-viewer: forwarding session cookie ({len(proxy_cookies)} fragment(s))")
    print("mcp-app-viewer: Ctrl-C to stop")

    events_file = Path(args.events_file) if args.events_file else None
    if events_file is not None:
        events_file.parent.mkdir(parents=True, exist_ok=True)
        print(f"mcp-app-viewer: recording app events -> {events_file}")

    heartbeat = Path(args.heartbeat_file) if args.heartbeat_file else None
    if heartbeat is not None:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        _make_handler(stage, asset_base_url, events_file, version, heartbeat,
                      args.event_label, proxy_headers or None),
    )
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
                not args.no_fallback,
                args.pane_profile,
                args.pane_anchor,
                args.pane_slot,
                args.pane_near,
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
