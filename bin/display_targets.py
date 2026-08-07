#!/usr/bin/env python3
"""Where to put a rendered MCP App.

Every coding-agent host has a different idiomatic place to show a web view, and
the right one depends on where you are sitting: a separate browser window is fine
from a plain terminal, but if you are working inside iTerm2 or VS Code you want
the app *beside* the session, not stealing focus into another application.

So the display is a pluggable TARGET rather than a hardcoded ``webbrowser.open``:

    system              the OS default browser (works everywhere; the fallback)
    (position: right (default) | bottom | left | top — for split-pane targets)
    chrome|safari|...   a specific browser by name
    iterm2              split the current iTerm2 window, app pane on the right
    vscode              VS Code's Simple Browser, beside the editor
    none                serve only and print the URL

Chrome is worth calling out: it is what chrome-devtools tooling attaches to, so
an app opened there can be inspected programmatically rather than only looked at.

Every target degrades to ``system`` rather than failing, and says so on stderr.
A viewer that serves correctly but cannot reach your preferred surface should
still show you the app — the URL is always printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

_MAC_APP_NAMES = {
    "chrome": "Google Chrome",
    "google-chrome": "Google Chrome",
    "safari": "Safari",
    "firefox": "Firefox",
    "edge": "Microsoft Edge",
    "brave": "Brave Browser",
    "arc": "Arc",
    "orion": "Orion",
}


def _warn(msg: str) -> None:
    print(f"mcp-app-viewer: {msg}", file=sys.stderr)


def _open_system(url: str) -> bool:
    webbrowser.open(url)
    return True


def _open_named(url: str, name: str) -> bool:
    key = name.strip().lower()
    if sys.platform == "darwin":
        app = _MAC_APP_NAMES.get(key, name)
        done = subprocess.run(["open", "-a", app, url], capture_output=True)
        if done.returncode == 0:
            return True
        _warn(f"could not open {app!r}")
        return False
    try:
        webbrowser.get(key).open(url)
        return True
    except Exception:  # noqa: BLE001
        _warn(f"no registered browser {name!r}")
        return False


def _iterm2_has_browser_profile() -> bool:
    """Is there a profile we can actually render a web view in?"""
    done = subprocess.run(
        ["osascript", "-e", 'tell application "iTerm2" to get name of every profile'],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return False
    names = {n.strip().lower() for n in done.stdout.split(",")}
    return bool(names & {"browser", "web", "webview"})


def _open_iterm2(url: str, position: str = "right") -> bool:
    """Show the app in an iTerm2 pane beside the session.

    iTerm2 3.6 ships a built-in browser, but browser panes require the
    ``browserProfiles`` advanced setting AND a profile to render in — both off by
    default, and neither creatable from AppleScript.

    We therefore check for a usable profile BEFORE splitting. An earlier version
    split first and echoed the URL into the new shell as a signpost; that was
    wrong twice over — it leaves a stray terminal pane that renders nothing, and
    `write text` races the shell's own startup, which mangled the command into
    `llecho`. A pane that cannot show the app should not be created at all.

    ``position`` picks the split axis. iTerm2 places a new pane to the right of a
    vertical split and below a horizontal one, so left/top use the same axis and
    say so rather than silently doing something else.
    """
    if sys.platform != "darwin" or not shutil.which("osascript"):
        _warn("iterm2 target needs macOS + osascript")
        return False
    if "ITERM_SESSION_ID" not in os.environ:
        _warn("not running inside iTerm2 (no ITERM_SESSION_ID)")
        return False
    if not _iterm2_has_browser_profile():
        _warn(
            "iTerm2 has no browser profile, so a pane cannot render the app. "
            "Enable it once: Settings > Advanced > search 'browserProfiles' > on, "
            "add a profile named 'Browser', restart iTerm2. Using a browser instead."
        )
        return False

    pos = (position or "right").strip().lower()
    axis = "horizontally" if pos in {"top", "bottom"} else "vertically"
    if pos in {"left", "top"}:
        _warn(f"iTerm2 opens new panes right/below; showing the app on the {axis[:-2]} side")

    script = f'''
    tell application "iTerm2"
      tell current session of current window
        set p to (split {axis} with profile "Browser")
        tell p to set URL to "{url}"
      end tell
    end tell
    '''
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if done.returncode != 0:
        _warn(f"iTerm2 browser pane failed: {done.stderr.strip()[:140]}")
        return False
    return True


def _open_vscode(url: str) -> bool:
    """Show the app in VS Code's built-in Simple Browser, beside the editor."""
    code = shutil.which("code") or shutil.which("code-insiders")
    if not code:
        _warn("vscode target needs the `code` CLI on PATH (Shell Command: Install 'code')")
        return False
    # Newer VS Code exposes --open-url, which the built-in URI handler routes to
    # Simple Browser for http(s). Older builds ignore it, so verify and fall back.
    done = subprocess.run([code, "--open-url", url], capture_output=True, text=True)
    if done.returncode == 0:
        return True
    _warn(f"`code --open-url` unsupported here ({done.stderr.strip()[:80]})")
    return False


def open_app(url: str, target: str | None, position: str = "right") -> str:
    """Open ``url`` on ``target``; returns the target actually used."""
    name = (target or "system").strip().lower()
    if name in {"none", "off", ""}:
        return "none"

    if name in {"iterm2", "iterm"}:
        if _open_iterm2(url, position):
            return "iterm2"
        if _open_system(url):
            return "system (fallback)"
        return "none"
    if name in {"vscode", "code"}:
        if _open_vscode(url):
            return "vscode"
        if _open_system(url):
            return "system (fallback)"
        return "none"

    if name == "system":
        _open_system(url)
        return "system"

    if _open_named(url, name):
        return name
    _open_system(url)
    return "system (fallback)"
