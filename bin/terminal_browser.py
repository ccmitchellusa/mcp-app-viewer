#!/usr/bin/env python3
"""Render an MCP App in a TERMINAL pane, using a terminal-based browser.

Most terminals split terminals, not web views. iTerm2 is the outlier — 3.6 ships an
actual browser pane, which is why the ``iterm2`` target exists at all. Ghostty,
WezTerm, kitty, tmux and Terminal.app all give you panes that run a program and
nothing else.

But a pane that runs a program can run a *browser*. That turns "no web view" into a
solved problem for every terminal at once, and it is the only target that works over
SSH on a headless box — where opening a window is not merely inconvenient but
impossible, and printing a URL is useless.

    THE ENGINE IS THE WHOLE POINT
    -----------------------------
    MCP Apps render through web components: custom elements whose content JavaScript
    builds into shadow DOM. A browser with no JS engine shows an EMPTY PAGE for a
    perfectly healthy app.

    That is not a degraded view, it is a false negative — you would conclude the app
    is broken and go debug a bug that does not exist. This project exists to stop
    exactly that confusion, so shipping it as a feature would be self-defeating.

    Hence ``engine``: browsers that run a real engine are used freely; text-only
    browsers are REFUSED for render verification and say why.

Text browsers are still genuinely useful — for the opposite job. A text-only render
is precisely what a non-visual client receives from the MCP Apps text content
fallback ("Servers SHOULD provide text-only fallback behavior for all UI-enabled
tools"). So ``allow_text=True`` enables them explicitly, for previewing the fallback
rather than the app. Same tool, opposite question; conflating the two is the trap.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

# engine: "real"    — a genuine browser engine; what you see is what the app does
#         "partial" — some JS support; better than nothing, not authoritative
#         "text"    — no JS at all; shows the text fallback, NOT the app
BROWSERS: dict[str, dict[str, str]] = {
    "carbonyl": {
        "engine": "real",
        "note": "Chromium rendering into the terminal — closest to the real thing",
    },
    "browsh": {
        "engine": "real",
        "note": "drives a headless Firefox; needs Firefox installed",
    },
    "cha": {
        "engine": "partial",
        "note": "Chawan; has a JS engine but incomplete coverage",
    },
    "w3m": {"engine": "text", "note": "no JavaScript — shows the text fallback only"},
    "lynx": {"engine": "text", "note": "no JavaScript — shows the text fallback only"},
    "links2": {"engine": "text", "note": "no JavaScript — shows the text fallback only"},
    "links": {"engine": "text", "note": "no JavaScript — shows the text fallback only"},
    "elinks": {"engine": "text", "note": "no JavaScript — shows the text fallback only"},
}

# Real engines first: if several are installed, the one that actually answers the
# question should win without the user having to know which that is.
_PREFERENCE = ["carbonyl", "browsh", "cha", "w3m", "lynx", "links2", "links", "elinks"]


def detect() -> list[str]:
    """Installed terminal browsers, best-for-this-purpose first."""
    return [name for name in _PREFERENCE if shutil.which(name)]


def describe() -> str:
    found = detect()
    if not found:
        names = ", ".join(_PREFERENCE[:3])
        return f"no terminal browser installed (try: brew install {names})"
    lines = []
    for name in found:
        info = BROWSERS[name]
        mark = {"real": "  ", "partial": "~ ", "text": "! "}[info["engine"]]
        lines.append(f"  {mark}{name:9} {info['engine']:8} {info['note']}")
    return "\n".join(lines)


def detect_system_theme() -> str:
    """The OS light/dark preference: "dark", "light", or "" if unknown.

    Real browsers read this themselves, so every other target honours your system
    setting for free. Carbonyl does not: it is headless Chromium, which defaults to
    LIGHT no matter what the desktop says. Measured on a Dark system, carbonyl
    reported ``prefers-color-scheme: dark`` as FALSE.

    That mattered more than cosmetics here — the Longleaf palette is
    prefers-color-scheme driven, so a wrong answer renders the whole app in the wrong
    theme inside a dark terminal.
    """
    if sys.platform == "darwin":
        done = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True
        )
        # The key is ABSENT in light mode rather than set to "Light", so a non-zero
        # exit is the documented way light is reported — not an error to log.
        return "dark" if done.returncode == 0 and "dark" in done.stdout.lower() else "light"
    done = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
        capture_output=True,
        text=True,
    )
    if done.returncode == 0:
        return "dark" if "dark" in done.stdout.lower() else "light"
    return ""


# Chromium's own switch. Verified empirically rather than assumed:
#   preferredColorScheme=0 -> prefers-color-scheme: dark  matches
#   preferredColorScheme=1 -> light
#   --force-dark-mode      -> light (it is Chrome's auto-darkening of light pages,
#                             a different feature that does NOT set the media query)
_COLOR_SCHEME_FLAG = {"dark": "0", "light": "1"}


def _theme_argv(browser: str, theme: str) -> list[str]:
    """Flags that make ``browser`` report ``theme`` to prefers-color-scheme."""
    if browser != "carbonyl":
        # browsh drives a real Firefox, which follows the desktop already; text
        # browsers have no concept of it. Only carbonyl needs telling.
        return []
    value = _COLOR_SCHEME_FLAG.get(theme)
    return [f"--blink-settings=preferredColorScheme={value}"] if value else []


def _browser_argv(name: str, url: str) -> list[str]:
    # ABSOLUTE path, not the bare name. A split pane starts a fresh shell, and version
    # managers (nvm, asdf, pyenv) put their shims on PATH only after their init script
    # runs — carbonyl installs under nvm by default. A bare name would give
    # "command not found" in a pane the user can see but we cannot read, which reads
    # as "the app failed to render".
    return [shutil.which(name) or name, url]


def _runtime_path_prefix(name: str) -> str:
    """PATH entry the browser's own INTERPRETER lives in.

    An absolute path finds the entrypoint but not what it shells out to. carbonyl is a
    bash script whose body is `"$(node "$0".js)" "$@"` — so resolving carbonyl and
    stopping there yields `node: command not found`, exit 127, in a fresh pane that
    never sourced nvm.

    In every version-manager layout (nvm, asdf, pyenv) and in Homebrew, the interpreter
    is a SIBLING of the entrypoint. So prepend that one directory rather than trying to
    replicate a shell's init.
    """
    resolved = shutil.which(name)
    return os.path.dirname(resolved) if resolved else ""


def _keep_open(command: str) -> str:
    """Wrap so the pane SURVIVES the browser exiting, and shows why it exited.

    Terminals run a pane's command directly and close the pane when it returns. So a
    crash, a missing binary, and a clean quit all look identical: the pane appears and
    vanishes. That is unreadable — and it is the same absent-vs-broken confusion this
    whole project exists to remove, reproduced in our own tooling.

    Keeping the pane open costs one keypress and turns an invisible failure into a
    legible one: exit code, plus whatever the browser printed on its way out.
    """
    return (
        "/bin/sh -c " + shlex.quote(
            f'{command}; printf "\n[mcp-app-viewer] browser exited (%s). '
            f'Press Enter to close this pane." "$?"; read _'
        )
    )


# --------------------------------------------------------------- terminal hosts ---
def detect_host() -> str:
    """Which terminal are we inside? Checked most-specific first.

    tmux wins over the outer terminal deliberately: inside tmux the visible panes are
    tmux's, so splitting the outer terminal would put the browser in a pane the user
    cannot see next to their work.
    """
    if os.environ.get("TMUX"):
        return "tmux"
    program = (os.environ.get("TERM_PROGRAM") or "").lower()
    if "ghostty" in program or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return "ghostty"
    if os.environ.get("WEZTERM_PANE") or "wezterm" in program:
        return "wezterm"
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    if os.environ.get("ITERM_SESSION_ID") or "iterm" in program:
        return "iterm2"
    return ""


def _split_tmux(command: str, position: str) -> tuple[bool, str]:
    flag = {"right": "-h", "left": "-h", "bottom": "-v", "top": "-v"}.get(position, "-h")
    argv = ["tmux", "split-window", flag]
    # tmux 3.x can place the new pane before the current one, which is what
    # left/top actually mean. Older tmux ignores -b, landing right/below — the
    # same axis, which is the honest degradation.
    if position in {"left", "top"}:
        argv.append("-b")
    argv.append(command)
    done = subprocess.run(argv, capture_output=True, text=True)
    return done.returncode == 0, done.stderr.strip()


def _applescript_str(text: str) -> str:
    """Quote a Python string as an AppleScript string literal.

    Backslash MUST be escaped before the quote, or escaping `"` would itself introduce
    backslashes that get re-escaped. This is not theoretical: the keep-open wrapper
    contains `\\n`, and AppleScript expands `\\n` inside a literal into a real newline
    — so the shell received a line break where printf expected the two characters, and
    the wrapper broke on exactly the failure path it exists to report.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _split_ghostty(command: str, position: str) -> tuple[bool, str]:
    """Ghostty splits via AppleScript, with the command set AT CREATION.

    That ordering matters. The iTerm2 target once split first and typed the command
    into the new shell afterwards; `write text` races the shell's own startup, which
    mangled it into `llecho`. A surface configuration carrying `command` has no race —
    the pane is created already running the browser.

    Three details come from Ghostty's dictionary rather than from guessing. `focused
    terminal` is a property of a TAB, not of the application, so it needs its full
    container path — bare `focused terminal` raises -1728. `wait after command` keeps
    the pane alive even when the command never launches, which the shell wrapper cannot
    do: if the wrapper itself fails to exec there is no shell left to hold the pane
    open.

    The third is the anchor. Splitting the FOCUSED pane is wrong here, because opening
    a pane moves Ghostty's focus into it — so a second `open` splits the browser pane
    we just made, and `position` starts measuring from the wrong pane. Observed
    directly: right and bottom landed beside the shell, then left landed beside the
    bottom BROWSER pane and top beside that one. A human never trips this (clicking
    back to the shell to type restores focus) but the caller here is an agent, which
    never touches focus at all — so for us it is the normal path, not the edge case.

    Our own panes are distinguishable: Ghostty reports a `working directory` only for
    surfaces running a shell with its integration loaded. A pane launched with an
    explicit `command` — every pane this module creates — reports an empty string. So
    anchor to the focused pane when it is a real shell, and otherwise to the first
    shell in the tab, which is the pane the user is actually working in.
    """
    direction = {"right": "right", "left": "left", "bottom": "down", "top": "up"}.get(
        position, "right"
    )
    script = f'''
    tell application "Ghostty"
      set anchor to focused terminal of selected tab of front window
      if (working directory of anchor) is "" then
        repeat with t in terminals of selected tab of front window
          if (working directory of t) is not "" then
            set anchor to t
            exit repeat
          end if
        end repeat
      end if
      set cfg to new surface configuration
      set command of cfg to {_applescript_str(command)}
      set wait after command of cfg to true
      set pane to split anchor direction {direction} with configuration cfg
      return id of pane
    end tell
    '''
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if done.returncode != 0:
        return False, done.stderr.strip()
    # A zero exit only means the script parsed and ran. Ghostty returns the new
    # surface's id, so an empty result means no pane was actually created — report
    # that as failure rather than claiming a split the user cannot see.
    pane = done.stdout.strip()
    if not pane:
        return False, "Ghostty reported no new surface id; no pane was created"
    return True, ""


def _split_wezterm(command: str, position: str) -> tuple[bool, str]:
    flag = {"right": "--right", "left": "--left", "bottom": "--bottom", "top": "--top"}.get(
        position, "--right"
    )
    done = subprocess.run(
        ["wezterm", "cli", "split-pane", flag, "--", *shlex.split(command)],
        capture_output=True,
        text=True,
    )
    return done.returncode == 0, done.stderr.strip()


def _split_kitty(command: str, position: str) -> tuple[bool, str]:
    location = {"right": "vsplit", "left": "vsplit", "bottom": "hsplit", "top": "hsplit"}.get(
        position, "vsplit"
    )
    done = subprocess.run(
        ["kitty", "@", "launch", "--location", location, *shlex.split(command)],
        capture_output=True,
        text=True,
    )
    # kitty needs `allow_remote_control yes`; without it this fails with a clear
    # message, which is worth passing through rather than flattening to "failed".
    return done.returncode == 0, done.stderr.strip()


def _split_iterm2(command: str, position: str) -> tuple[bool, str]:
    axis = "horizontally" if position in {"top", "bottom"} else "vertically"
    escaped = command.replace('"', '\\"')
    script = f'''
    tell application "iTerm2"
      tell current session of current window
        split {axis} with default profile command "{escaped}"
      end tell
    end tell
    '''
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return done.returncode == 0, done.stderr.strip()


_SPLITTERS = {
    "tmux": _split_tmux,
    "ghostty": _split_ghostty,
    "wezterm": _split_wezterm,
    "kitty": _split_kitty,
    "iterm2": _split_iterm2,
}


# ---------------------------------------------------------------------- entry ---
def open_in_split(
    url: str,
    position: str = "right",
    browser: str | None = None,
    allow_text: bool = False,
    theme: str | None = None,
) -> tuple[bool, str]:
    """Split the current terminal and render ``url`` in a terminal browser.

    Returns (ok, message). Never raises — the caller falls back to a real browser.
    """
    available = detect()
    if not available:
        return False, (
            "no terminal browser installed. For real rendering install one with an "
            "engine: `brew install carbonyl` (Chromium) or `browsh`. Text browsers "
            "(lynx, w3m) cannot render these apps — see below."
        )

    name = (browser or "").strip().lower()
    if name and name not in BROWSERS:
        return False, f"unknown terminal browser {name!r}. Known: {', '.join(_PREFERENCE)}"
    if name and not shutil.which(name):
        return False, f"{name} is not installed (detected: {', '.join(available)})"
    if not name:
        name = available[0]

    engine = BROWSERS[name]["engine"]
    if engine == "text" and not allow_text:
        return False, (
            f"{name} has no JavaScript engine. MCP Apps render through web components, "
            f"so {name} would show an EMPTY page for a perfectly working app — a false "
            "negative, which is worse than showing nothing. Install carbonyl or browsh "
            f"for real rendering, or pass fallback-preview to use {name} deliberately "
            "for what a text-only client receives."
        )

    host = detect_host()
    if not host:
        return False, (
            "not inside a terminal that can split (looked for tmux, Ghostty, WezTerm, "
            "kitty, iTerm2). Run inside one, or use a browser target."
        )

    want = (theme or "auto").strip().lower()
    resolved_theme = detect_system_theme() if want == "auto" else want
    parts = _browser_argv(name, url)
    parts[1:1] = _theme_argv(name, resolved_theme)
    argv = " ".join(shlex.quote(part) for part in parts)
    bindir = _runtime_path_prefix(name)
    if bindir:
        argv = f"PATH={shlex.quote(bindir)}:$PATH {argv}"
    command = _keep_open(argv)
    ok, err = _SPLITTERS[host](command, (position or "right").strip().lower())
    if not ok:
        return False, f"{host} split failed: {err[:160]}"

    caveat = ""
    if engine == "partial":
        caveat = " (partial JS — confirm anything surprising in a real browser)"
    elif engine == "text":
        caveat = " (text-only: this is the FALLBACK, not the app)"
    theme_note = f", {resolved_theme} theme" if resolved_theme else ""
    article = "an" if host[0] in "aeiou" else "a"
    return True, f"{name} in {article} {host} pane ({position}{theme_note}){caveat}"


if __name__ == "__main__":
    if "--detect" in sys.argv:
        print(describe())
        print(f"\nterminal host: {detect_host() or 'none detected'}")
    else:
        print(__doc__)
