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
    terminal            split ANY terminal and render in a terminal browser
                        (Ghostty/tmux/WezTerm/kitty/iTerm2; works headless over SSH)
    vscode              VS Code's Simple Browser, beside the editor
    none                serve only and print the URL

Chrome is worth calling out: it is what chrome-devtools tooling attaches to, so
an app opened there can be inspected programmatically rather than only looked at.

Every target degrades to ``system`` rather than failing, and says so on stderr.
A viewer that serves correctly but cannot reach your preferred surface should
still show you the app — the URL is always printed.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import webbrowser
from urllib.parse import quote

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


# ------------------------------------------------------- editor discovery ---
# VS Code has forks — VSCodium, Cursor, Windsurf, Bob IDE — and the parts this
# project touches are all fork-specific: the CLI binary name, the URI scheme its
# handler answers on, and the extensions directory. Guessing any of them fails
# SILENTLY and in the worst possible way: firing `vscode://` on a machine with both
# installed opens a pane in the WRONG EDITOR rather than reporting an error.
#
# So discover instead of guessing. Every fork ships a ``product.json`` carrying
# exactly what we need — ``applicationName``, ``urlProtocol``, ``dataFolderName`` —
# and it is authoritative for that build. Resolve the CLI to its app bundle, read
# the file, done. A fork nobody has heard of works without a code change.
_EDITOR_CLI_CANDIDATES = ("code", "codium", "cursor", "windsurf", "bob", "code-insiders")

_VSCODE_DEFAULTS = {
    "cli": "code",
    "url_protocol": "vscode",
    "data_folder": ".vscode",
}


def _find_product_json(cli_path: str) -> dict | None:
    """Walk up from a resolved CLI binary to its build's product.json.

    Layouts differ (macOS: ``<app>/Contents/Resources/app/bin/code``; Linux:
    ``/usr/share/code/bin/code`` with ``resources/app/product.json``), so probe both
    shapes at each ancestor rather than hardcoding one platform's path.
    """
    here = pathlib.Path(os.path.realpath(cli_path)).parent
    for parent in [here, *here.parents][:6]:
        for candidate in (parent / "product.json", parent / "resources" / "app" / "product.json"):
            if candidate.is_file():
                try:
                    return json.loads(candidate.read_text())
                except (OSError, ValueError):
                    return None
    return None


def editor_profile() -> dict:
    """The editor triple: {cli, url_protocol, data_folder, extensions_dir}.

    Env overrides win, so a fork whose product.json lies (or is absent) is still
    usable without patching this file.
    """
    cli = os.environ.get("MCP_APP_EDITOR_CLI") or ""
    if not cli or not shutil.which(cli):
        cli = next((c for c in _EDITOR_CLI_CANDIDATES if shutil.which(c)), "")

    profile = dict(_VSCODE_DEFAULTS)
    if cli:
        profile["cli"] = cli
        product = _find_product_json(shutil.which(cli) or cli)
        if product:
            profile["url_protocol"] = product.get("urlProtocol") or profile["url_protocol"]
            profile["data_folder"] = product.get("dataFolderName") or profile["data_folder"]

    profile["url_protocol"] = os.environ.get("MCP_APP_EDITOR_URI_SCHEME") or profile["url_protocol"]
    profile["data_folder"] = os.environ.get("MCP_APP_EDITOR_DATA_FOLDER") or profile["data_folder"]
    profile["extensions_dir"] = os.environ.get("MCP_APP_EDITOR_EXT_DIR") or str(
        pathlib.Path.home() / profile["data_folder"] / "extensions"
    )
    profile["found"] = bool(cli)
    return profile


# A companion VS Code extension would register a URI handler and call
# `simpleBrowser.show`. Until one exists, the vscode target cannot work — see the
# comment in _open_vscode.
_VSCODE_HELPER_EXTENSION = "ccmitchellusa.mcp-app-viewer"


def _open_vscode(url: str, position: str = "right") -> bool:
    """Show the app in the editor's built-in Simple Browser, beside your work.

    **The editor has no CLI for this**, and the obvious guess is a trap. An earlier
    version ran ``code --open-url <url>`` and returned True on exit 0. Measured on VS
    Code 1.128.1: ``--open-url`` is not in ``--help`` at all, and ``code`` exits **0**
    for a bogus URL *and* for a completely invented flag. So the exit code carries no
    signal, and that check reported success while opening nothing.

    Worse than a no-op: ``code --open-url http://example.invalid/nonsense`` made VS
    Code pop up "The extension 'example.invalid' cannot be installed because it was
    not found" — it parses the URL's host as a ``publisher.name`` extension id. So the
    old code took an active wrong action on every render, invisibly.

    Simple Browser is reachable only through the ``simpleBrowser.show`` *command*, and
    commands cannot be invoked from the CLI. So the route is the companion extension in
    ``vscode-extension/``, installed by ``install.sh``, registering a URI handler the
    CLI CAN reach. Without it this target is honestly unavailable: say so and fall back.

    Works against any VS Code fork — the scheme and paths come from the build's own
    product.json (see ``editor_profile``), never from a hardcoded guess.

    Unlike iTerm2, the editor has real editor-group placement, so ``position`` means
    what it says in all four directions.
    """
    profile = editor_profile()
    if not profile["found"]:
        _warn(
            "vscode target needs an editor CLI on PATH "
            f"(looked for: {', '.join(_EDITOR_CLI_CANDIDATES)}). "
            "Set MCP_APP_EDITOR_CLI if yours is named something else."
        )
        return False

    cli = profile["cli"]
    installed = subprocess.run([cli, "--list-extensions"], capture_output=True, text=True)
    if _VSCODE_HELPER_EXTENSION not in installed.stdout.split():
        _warn(
            f"{cli}: the companion extension is not installed, and there is no CLI that "
            "opens Simple Browser, so success cannot be detected. Showing the app in a "
            "browser instead. Run install.sh to add it, then reload the editor."
        )
        return False

    pos = (position or "right").strip().lower()
    handler_uri = (
        f"{profile['url_protocol']}://{_VSCODE_HELPER_EXTENSION}/open"
        f"?url={quote(url, safe='')}&position={quote(pos, safe='')}"
    )
    subprocess.run([cli, "--open-url", handler_uri], capture_output=True, text=True)
    # VERIFIED end to end on VS Code 1.128.1 (2026-08-07): renders a Simple Browser
    # pane in a real editor group on the right. NOT yet verified on any fork — the
    # discovery above removes the guessing, but a fork that stripped Simple Browser
    # would still have no pane. Not verifiable from out here either way: the exit code
    # means nothing, so the extension logs what it actually did to its own output
    # channel, which is where a "nothing appeared" report gets diagnosed.
    return True


def open_app(
    url: str,
    target: str | None,
    position: str = "right",
    browser: str | None = None,
    allow_text: bool = False,
    theme: str | None = None,
    fallback: bool = True,
) -> str:
    """Open ``url`` on ``target``; returns the target actually used.

    ``fallback=False`` means the CALLER has already arranged to display the app
    itself, so degrading to the OS browser here would not be a fallback — it would
    be a second, unasked-for copy. See the comment on ``_degrade``.
    """
    name = (target or "system").strip().lower()
    if name in {"none", "off", ""}:
        return "none"

    def _degrade() -> str:
        """The last resort: show it *somewhere* rather than nowhere.

        Unless the caller opted out. mcp-app.sh renders the ``terminal`` target
        inline in the user's own terminal when nothing can split, and this process
        cannot see that: it is detached, owns no tty, and only knows its split
        failed. So it fell back to the system browser while the shell went on to
        run carbonyl — measured directly, a Safari tab on 127.0.0.1:8777 with the
        same app drawing in the terminal beside it. The caller knows its own
        intent; ``--no-fallback`` is how it says so.
        """
        if not fallback:
            return "none (fallback declined)"
        if _open_system(url):
            return "system (fallback)"
        return "none"

    if name in {"terminal", "term"}:
        import terminal_browser

        ok, message = terminal_browser.open_in_split(
            url, position, browser, allow_text, theme
        )
        if ok:
            return f"terminal: {message}"
        # The message is the whole value here — "no terminal browser installed" and
        # "lynx cannot render this" need completely different actions from the user.
        _warn(message)
        return _degrade()

    if name in {"iterm2", "iterm"}:
        if _open_iterm2(url, position):
            return "iterm2"
        return _degrade()
    if name in {"vscode", "code"}:
        if _open_vscode(url, position):
            return "vscode"
        return _degrade()

    if name == "system":
        _open_system(url)
        return "system"

    if _open_named(url, name):
        return name
    return _degrade()


if __name__ == "__main__":
    # `display_targets.py --editor-profile` prints the discovered triple as JSON, so
    # install.sh installs the extension where THIS editor will look for it rather than
    # where VS Code would. One discovery implementation, two callers.
    if "--editor-profile" in sys.argv:
        print(json.dumps(editor_profile(), indent=2))
    else:
        print(__doc__)
