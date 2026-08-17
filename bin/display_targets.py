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
import time
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


# Our own browser profile, written as a DYNAMIC PROFILE. iTerm2 watches this
# directory and loads changes live — no restart, and the user's own profiles are
# never touched.
_ITERM2_PROFILE_DIR = (
    pathlib.Path.home() / "Library" / "Application Support" / "iTerm2" / "DynamicProfiles"
)
_ITERM2_PROFILE_FILE = _ITERM2_PROFILE_DIR / "mcp-app-viewer.json"
_ITERM2_PROFILE_NAME = "MCP App Viewer"


def _iterm2_browser_readiness() -> tuple[bool, str]:
    """Can iTerm2 render a web view in a pane? Returns (ready, why-not).

    Read from PREFERENCES, not AppleScript. The original probe ran
    `get name of every profile`, which is not valid AppleScript at all: iTerm2's
    dictionary has no `profile` class, only application/session/tab/window. It
    therefore failed with -2741 on every machine and the code turned that into
    "iTerm2 has no browser profile" — a malformed query reported as a finding about
    the user's setup. The conclusion happened to be right here, for the wrong
    reason, which is the worst way to be right.

    ONE prerequisite now, not two. This used to also demand a hand-made profile
    called "Browser"; we ship our own as a dynamic profile instead, which is both
    less setup and more reliable — a profile we write is a profile we know carries
    the initial URL.
    """
    export = subprocess.run(
        ["defaults", "export", "com.googlecode.iterm2", "-"],
        capture_output=True,
    )
    if export.returncode != 0:
        return False, "could not read iTerm2 preferences"
    try:
        import plistlib

        prefs = plistlib.loads(export.stdout)
    except Exception:  # noqa: BLE001
        return False, "could not parse iTerm2 preferences"

    if not prefs.get("browserProfiles"):
        return False, (
            "iTerm2's browser panes are off. Settings > Advanced > search "
            "'browserProfiles' > on, then restart iTerm2."
        )
    return True, ""


def _write_iterm2_profile(url: str, name: str, path: pathlib.Path) -> tuple[bool, bool]:
    """Point our dynamic browser profile at ``url``. Returns (ok, changed).

    THE ONLY WAY TO AIM AN ITERM2 BROWSER PANE. Verified against iTerm2 3.6.11 by
    pointing each candidate at a logging HTTP server and checking for a request:

      * `set URL of session` — the session class has no URL property. Fails -10003.
      * `split ... command "<url>"` — `command` is a SHELL command. iTerm2 ran the
        URL as one, it failed, and `Close Sessions On End` shut the pane before the
        error could be read. No request, and a red flash the user cannot catch.
      * `open -a iTerm "<url>"` — no request.
      * the Python API's `load_url` — real, but needs the API server enabled AND the
        `iterm2` package installed. Too much setup to require.

    A dynamic profile carrying `Initial URL` works, needs no restart, and asks the
    user for nothing beyond the advanced setting they already had to enable.
    """
    payload = {
        "Profiles": [
            {
                "Name": name,
                "Guid": f"mcp-app-viewer-{name}",
                # What marks a profile as browser-mode rather than a shell.
                "Custom Command": "Browser",
                "Initial URL": url,
                "Close Sessions On End": False,
            }
        ]
    }
    text = json.dumps(payload, indent=2) + "\n"
    try:
        if path.exists() and path.read_text("utf-8") == text:
            return True, False  # already correct — no write, and no wait needed
        _ITERM2_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True, True
    except OSError as exc:
        _warn(f"could not write the iTerm2 dynamic profile: {exc}")
        return False, False


def _open_iterm2(url: str, position: str = "right", pane_profile: str | None = None,
                 anchor: str | None = None) -> bool:
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
    ready, why_not = _iterm2_browser_readiness()
    if not ready:
        _warn(f"{why_not} Using a browser instead.")
        return False

    profile_name = pane_profile or _ITERM2_PROFILE_NAME
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    ok, changed = _write_iterm2_profile(url, profile_name, _ITERM2_PROFILE_DIR / f"{safe}.json")
    if not ok:
        return False
    if changed:
        # iTerm2 watches the directory; the reload is not instantaneous. Splitting
        # before it lands opens the pane on the PREVIOUS url, which looks like a
        # stale render rather than a race.
        time.sleep(1.2)

    pos = (position or "right").strip().lower()
    axis = "horizontally" if pos in {"top", "bottom"} else "vertically"
    if pos in {"left", "top"}:
        _warn(f"iTerm2 opens new panes right/below; showing the app on the {axis[:-2]} side")

    # WHERE to split from. Without an anchor iTerm2 splits the current session — and
    # "current" MOVES: creating a pane focuses it, so the next unanchored render
    # would split the pane we just made, marching across the window. An anchor is a
    # session id captured when a pane was created, so a slot can be updated or
    # placed relative to a specific pane no matter where focus has wandered.
    if anchor:
        locate = f'''
      set found to missing value
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (id of s) is "{anchor}" then set found to s
          end repeat
        end repeat
      end repeat
      if found is missing value then error "anchor pane is gone"
      tell found
        set p to (split {axis} with profile "{profile_name}")
      end tell'''
    else:
        locate = f'''
      tell current session of current window
        set p to (split {axis} with profile "{profile_name}")
      end tell'''

    script = f'''
    tell application "iTerm2"{locate}
      return id of p
    end tell
    '''
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if done.returncode != 0:
        err = done.stderr.strip()[:140]
        if anchor and "anchor pane is gone" in err:
            # The pane this slot used to live in was closed. Retry unanchored rather
            # than refusing: the user asked to see an app, not to see an error about
            # a pane they closed themselves.
            _warn("the anchor pane was closed; opening a new pane instead")
            return _open_iterm2(url, position, pane_profile, anchor=None)
        _warn(f"iTerm2 browser pane failed: {err}")
        return False
    # How the caller learns which pane this slot now owns. The server is detached,
    # so stdout is the log and mcp-app.sh scrapes this line — the same channel that
    # already carries "displayed on".
    sid = done.stdout.strip()
    if sid:
        print(f"mcp-app-viewer: pane session {sid}")
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


def _open_vscode(url: str, position: str = "right", pane_slot: str | None = None,
                 pane_near: str | None = None) -> bool:
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
    # slot/near are what make panes addressable in the editor, the same way a session
    # id does in iTerm2. The extension keys its own webview panels by slot, because
    # Simple Browser is a singleton and would collapse every slot into one tab.
    handler_uri = (
        f"{profile['url_protocol']}://{_VSCODE_HELPER_EXTENSION}/open"
        f"?url={quote(url, safe='')}&position={quote(pos, safe='')}"
        f"&slot={quote(pane_slot or 'main', safe='')}"
        + (f"&near={quote(pane_near, safe='')}" if pane_near else "")
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
    pane_profile: str | None = None,
    anchor: str | None = None,
    pane_slot: str | None = None,
    pane_near: str | None = None,
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

    if name in {"vscode", "code", "editor"}:
        if _open_vscode(url, position, pane_slot, pane_near):
            return "vscode"
        return _degrade()

    if name in {"iterm2", "iterm"}:
        if _open_iterm2(url, position, pane_profile, anchor):
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


def close_vscode_pane(slot: str) -> bool:
    """Ask the editor to dispose a pane (or all of them, with "--all").

    Lives here because editor discovery does: which CLI, which URL scheme, and
    whether the companion extension is present are all answered in one place.
    """
    profile = editor_profile()
    if not profile["found"]:
        return False
    uri = (
        f"{profile['url_protocol']}://{_VSCODE_HELPER_EXTENSION}/close"
        f"?slot={quote(slot, safe='')}"
    )
    subprocess.run([profile["cli"], "--open-url", uri], capture_output=True, text=True)
    return True
