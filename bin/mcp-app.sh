#!/bin/bash
# mcp-app-viewer control surface — drives the auto-open hook and renders apps by hand.
#
# MCP Apps (SEP-1865 / ext-apps) are HTML shipped as ui:// resources. Terminal MCP
# clients do not render them, so during development they are invisible: a server can
# ship an app that is blank, broken, or unstyled and nothing tells you. This turns
# "look at the app" into one command, and optionally does it automatically.
#
#   mcp-app.sh                  status
#   mcp-app.sh on | off         auto-open on tool results carrying an MCP App
#   mcp-app.sh open <file|->    render one app's HTML now
#   mcp-app.sh url <address>    point the SAME display target at a live URL
#   mcp-app.sh last             re-open the most recently captured app
#   mcp-app.sh stop             stop the local viewer server
#   mcp-app.sh base <url|->     set/clear the origin used to resolve relative assets
#   mcp-app.sh target <name>    where to display: system|chrome|safari|firefox|iterm2|vscode|terminal|none
#   mcp-app.sh browser [name]   which TERMINAL browser the 'terminal' target uses
#   mcp-app.sh theme [auto|light|dark]  colour scheme reported to the app (auto = OS)
#   mcp-app.sh assets <dir|->   local dir serving the app's relative assets
#   mcp-app.sh position <where> pane placement for split targets: right|left|top|bottom
#   mcp-app.sh log              tail the activity log
#   mcp-app.sh oauth login|status|token|logout|watch|setup   interactive OAuth login + client setup for MCP servers

set -uo pipefail

# Derived at RUNTIME, never substituted in. install.sh used to sed a placeholder
# here, which worked exactly once: the substituted path was then COMMITTED, so the
# placeholder no longer existed and a fresh clone on another machine silently kept
# pointing at the author's home directory. Deriving it removes the failure mode
# rather than documenting it. Resolve symlinks first so PATH-level links work.
_SELF="${BASH_SOURCE[0]}"
while [ -L "$_SELF" ]; do _SELF="$(readlink "$_SELF")"; done
PROJECT_DIR="$(cd "$(dirname "$_SELF")/.." && pwd)"
CONFIG_DIR="${MCP_APP_CONFIG_DIR:-$HOME/.config/mcp-app-viewer}"
CONFIG="$CONFIG_DIR/config.sh"

# A PROFILE is one agent's overrides (codex, kimi, hermes). Each gets its own
# state and its own port, because two agents auto-opening on the same port would
# each silently kill the other's viewer and you would be looking at whichever
# app won the race.
PROFILE="${MCP_APP_PROFILE:-}"
STATE="$CONFIG_DIR/state${PROFILE:+.$PROFILE}"
LAST_HTML="$CONFIG_DIR/last-app${PROFILE:+.$PROFILE}.html"
PIDFILE="$CONFIG_DIR/server${PROFILE:+.$PROFILE}.pid"
LOG="${MCP_APP_LOG:-/tmp/mcp-app-viewer.log}"

# OAuth credentials are part of the agent-profile boundary, not just the viewer
# pane state. When a profile is set, keep its token store under a profile-scoped
# subtree unless the caller explicitly overrides MCP_OAUTH_STORE.
if [ -n "$PROFILE" ]; then
  export MCP_OAUTH_PROFILE="$PROFILE"
  : "${MCP_OAUTH_STORE:=$CONFIG_DIR/profiles/$PROFILE/oauth-tokens.json}"
  export MCP_OAUTH_STORE
fi

mkdir -p "$CONFIG_DIR"
# shellcheck disable=SC1090
[ -f "$CONFIG" ] && . "$CONFIG"
# shellcheck disable=SC1090
[ -n "$PROFILE" ] && [ -f "$CONFIG_DIR/$PROFILE.sh" ] && . "$CONFIG_DIR/$PROFILE.sh"

MCP_APP_PORT="${MCP_APP_PORT:-8777}"
MCP_APP_BASE_URL="${MCP_APP_BASE_URL:-}"
MCP_APP_TARGET="${MCP_APP_TARGET:-system}"
MCP_APP_ASSETS="${MCP_APP_ASSETS:-}"
MCP_APP_POSITION="${MCP_APP_POSITION:-right}"
PY="${MCP_APP_PYTHON:-python3}"

log() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }

_get() { # <key> <default>
  [ -f "$STATE" ] || { echo "$2"; return; }
  local v; v=$(grep -E "^$1=" "$STATE" 2>/dev/null | tail -1 | cut -d= -f2-)
  [ -n "$v" ] && echo "$v" || echo "$2"
}

_set() { # <key> <value>
  mkdir -p "$CONFIG_DIR"; touch "$STATE"
  grep -vE "^$1=" "$STATE" > "$STATE.tmp" 2>/dev/null || true
  echo "$1=$2" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# Resolve the port from state now that _get exists — `mcp-app.sh port <n>` has to
# affect the server that actually starts, not just the number printed by `status`.
MCP_APP_PORT=$(_get port "$MCP_APP_PORT")

# On a plain terminal -- xterm, Terminal.app, a serial console, SSH with no
# multiplexer -- there is nothing to split, and falling back to a windowing browser
# is no fallback at all on a headless box. So run the browser in THIS terminal.
#
# It has to happen here, not in the viewer: that process is detached (nohup, stdout
# to the log) and owns no terminal. Foreground means blocking until you quit the
# browser, which is right for a command you typed and WRONG for the auto-open hook,
# so this never runs from the hook -- MCP_APP_NO_INLINE=1 is set there.
#
# Decided BEFORE the server starts, and that ordering is the fix for a real bug:
# the viewer, unable to split and unable to see our intent, opened the SYSTEM
# browser, and then we inlined too -- a Safari tab and a carbonyl render of the
# same app at the same time. Knowing the answer up front lets us pass
# --no-fallback, so exactly one of the two draws the app.
INLINE_CMD=""
INLINE_NOTE=""
_plan_inline() {
  INLINE_CMD=""; INLINE_NOTE=""
  [ "$(_get target "$MCP_APP_TARGET")" = "terminal" ] || return 0
  [ -z "${MCP_APP_NO_INLINE:-}" ] || return 0
  [ -t 1 ] || return 0                       # not a terminal we can draw in
  [ -z "$("$PY" "$PROJECT_DIR/bin/terminal_browser.py" --host 2>/dev/null)" ] || return 0

  INLINE_CMD=$("$PY" "$PROJECT_DIR/bin/terminal_browser.py" --inline-command "http://127.0.0.1:$MCP_APP_PORT/app/index.html" 2>/dev/null)
  # No engine installed: there is nothing to inline, so the viewer's own fallback
  # to a windowing browser is the right outcome and we leave it enabled. Still say
  # why the target the user chose did not happen.
  [ -n "$INLINE_CMD" ] && return 0
  INLINE_NOTE=$'no terminal browser installed, and this terminal cannot split.\n  install one:  brew install carbonyl    (or npm i -g carbonyl)\n  or run tmux, which makes the split path work on any terminal.'
}

_run_inline() {
  if [ -n "$INLINE_CMD" ]; then
    echo "no split available here — opening in THIS terminal (quit the browser to return)"
    eval "$INLINE_CMD"
  elif [ -n "$INLINE_NOTE" ]; then
    printf '%s\n' "$INLINE_NOTE" >&2
  fi
}

_server_running() {
  [ -f "$PIDFILE" ] || return 1
  kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

stop_server() {
  if _server_running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
    rm -f "$PIDFILE"
    echo "viewer stopped (port $MCP_APP_PORT)"
    log "server stopped"
  else
    echo "viewer not running"
  fi
}

# Render one app. Reads HTML from a file or stdin, keeps a copy as `last`, and
# serves it. Restarting is deliberate: a viewer pinned to a stale app is worse
# than none, because you would be looking at the previous app believing it is
# the current one.
open_app() { # <file|->
  local src="${1:--}" html
  if [ "$src" = "-" ]; then html=$(cat); else html=$(cat "$src" 2>/dev/null); fi
  if [ -z "${html//[[:space:]]/}" ]; then
    echo "no app HTML on input — nothing to render" >&2
    return 2
  fi
  printf '%s' "$html" > "$LAST_HTML"

  _server_running && { kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; sleep 0.3; }

  local target; target=$(_get target "$MCP_APP_TARGET")
  local base;   base=$(_get base "$MCP_APP_BASE_URL")
  local assets; assets=$(_get assets "$MCP_APP_ASSETS")
  local pos; pos=$(_get position "$MCP_APP_POSITION")
  local tb;  tb=$(_get browser "")
  local th;  th=$(_get theme "auto")
  local args=(--html-file "$LAST_HTML" --port "$MCP_APP_PORT" --browser "$target" --position "$pos")
  [ -n "$base" ]   && args+=(--base-url "$base")
  [ -n "$assets" ] && args+=(--assets-dir "$assets")
  [ -n "$tb" ]     && args+=(--terminal-browser "$tb")
  [ -n "$th" ]     && args+=(--theme "$th")

  _plan_inline
  [ -n "$INLINE_CMD" ] && args+=(--no-fallback)

  # Where the log ENDS right now, before this run writes a byte. The verdict scan
  # below reads only past this mark. Scanning the whole file matched a line from a
  # previous run and reported it as this one's: from Terminal.app it printed
  # "carbonyl in an iterm2 pane" -- 34 minutes stale -- while the server was at
  # that moment logging "displayed on system (fallback)". An append-only log has
  # no "latest" without an offset.
  local log_mark=0
  [ -f "$LOG" ] && log_mark=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')

  # -u is load-bearing, not a nicety. Python BLOCK-buffers stdout when it is not a
  # TTY, and this server runs until killed -- so every print() sat in a buffer that
  # never flushed, and the log showed only the shell's own lines. That hid the one
  # message that says whether the chosen display target worked or fell back.
  nohup "$PY" -u "$PROJECT_DIR/bin/mcp_app_server.py" "${args[@]}" >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 0.5
  if _server_running; then
    echo "serving http://127.0.0.1:$MCP_APP_PORT/app/index.html"
    if [ -n "$base" ]; then echo "assets proxy: $base"; fi
    # The viewer runs under nohup with stdout redirected to the log, so WHERE it
    # displayed the app -- the one line that says whether your chosen target worked
    # or silently fell back -- never reached the terminal. Surfacing it matters more
    # than usual here: "displayed on system (fallback)" and "displayed on terminal"
    # are the difference between a working target and a broken one, and both look
    # identical when all you see is "serving".
    local shown=""
    # Up to ~5s. A browser target confirms almost instantly and pays none of it;
    # a terminal split has to run osascript AND start Chromium, which measured ~3s
    # -- and that is precisely the case where you most want to be told whether it
    # worked, so waiting is the right trade.
    for _ in $(seq 1 20); do
      shown=$(tail -c "+$((log_mark + 1))" "$LOG" 2>/dev/null | grep -a "mcp-app-viewer: displayed on" | tail -1)
      [ -n "$shown" ] && break
      sleep 0.25
    done
    if [ -n "$shown" ]; then
      echo "${shown#mcp-app-viewer: }"
    else
      echo "displayed on: (no confirmation yet — see '/mcp-app log')"
    fi
    log "opened app ($(printf '%s' "$html" | wc -c | tr -d ' ') bytes)"
    # OUTSIDE the confirmation branch, deliberately. It used to be inside, which
    # a stale match hid: on a cold log there is nothing to match, the wait times
    # out, and the browser that was the whole point of this target never launched.
    # Whether the verdict line arrived is a reporting question; whether we inline
    # was already decided, before the server even started.
    _run_inline
  else
    echo "viewer failed to start — see $LOG" >&2
    return 1
  fi
}

case "${1:-status}" in
  on)
    _set enabled 1
    echo "auto-open ON — tool results carrying an MCP App will render in your browser"
    ;;
  off)
    _set enabled 0
    echo "auto-open OFF — use '/mcp-app last' or '/mcp-app open <file>' to render manually"
    ;;
  open)
    shift; open_app "${1:--}"
    ;;
  url)
    shift
    if [ -z "${1:-}" ]; then echo "usage: mcp-app.sh url <address>" >&2; exit 2; fi
    # No local server: the page is already served by someone else. Everything else
    # -- target, position, terminal browser, theme -- is deliberately the same, so
    # "show me the live app" and "show me this app's HTML" land in the same pane
    # with the same settings rather than being two unrelated tools.
    "$PY" - "$1" "$(_get target "$MCP_APP_TARGET")" "$(_get position "$MCP_APP_POSITION")" \
             "$(_get browser "")" "$(_get theme "auto")" "$PROJECT_DIR/bin" <<'PYEOF'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent if "__file__" in dir() else "."))
sys.path.insert(0, sys.argv[6])
from display_targets import open_app
url, target, position, browser, theme = sys.argv[1:6]
used = open_app(url, target, position, browser or None, False, theme)
print(f"displayed on {used}")
print(f"url: {url}")
PYEOF
    ;;
  last)
    [ -s "$LAST_HTML" ] || { echo "no app captured yet"; exit 0; }
    open_app "$LAST_HTML"
    ;;
  stop)
    stop_server
    ;;
  base)
    shift
    if [ "${1:-}" = "-" ] || [ -z "${1:-}" ]; then
      _set base ""
      echo "asset proxy cleared (relative asset paths will 404 unless self-contained)"
    else
      _set base "$1"; echo "asset proxy origin: $1"
    fi
    ;;
  target)
    shift
    case "${1:-}" in
      system|chrome|safari|firefox|edge|brave|arc|iterm2|vscode|terminal|none)
        _set target "$1"; echo "display target: $1"
        # NOTE: written as `if`, not `[ ... ] && echo`. A trailing &&-guard that
        # evaluates false makes it the branch's last status, so the whole script
        # exits 1 on a successful command — the exact bug this project's sibling
        # (the Piper TTS integration) shipped in ll-tts-voice.sh.
        if [ "$1" = "iterm2" ]; then
          echo "  note: needs iTerm2 'browserProfiles' advanced setting + a profile named 'Browser' (restart iTerm2); otherwise it falls back to a browser"
        fi
        if [ "$1" = "chrome" ]; then
          echo "  note: chrome-devtools tooling can then inspect the rendered app"
        fi
        if [ "$1" = "terminal" ]; then
          # Both routes, not just the split one. Advertising only the splitter and
          # then printing "terminal host: none detected" reads as a failure in the
          # plain terminal — which is the one place the inline route is the point,
          # not a consolation.
          echo "  note: renders in a terminal browser, so it works over SSH on a headless box."
          echo "        In Ghostty, tmux, WezTerm, kitty or iTerm2 the app opens in a split pane."
          echo "        In a plain terminal (Terminal.app, xterm, bare SSH) there is nothing to"
          echo "        split, so it takes over THIS terminal until you quit the browser."
          "$PY" "$PROJECT_DIR/bin/terminal_browser.py" --detect 2>/dev/null
        fi
        ;;
      "") echo "display target: $(_get target "$MCP_APP_TARGET")" ;;
      *)  echo "unknown target '${1}'. Use: system|chrome|safari|firefox|edge|brave|arc|iterm2|vscode|terminal|none" >&2; exit 2 ;;
    esac
    ;;
  position)
    shift
    case "${1:-}" in
      right|left|top|bottom)
        _set position "$1"; echo "pane position: $1"
        case "$1" in
          left|top) echo "  note: iTerm2 places new panes right/below, so the app lands on the opposite side; the axis still matches (left->vertical, top->horizontal)" ;;
        esac
        ;;
      "") echo "pane position: $(_get position "$MCP_APP_POSITION")" ;;
      *)  echo "unknown position '${1}'. Use: right|left|top|bottom" >&2; exit 2 ;;
    esac
    ;;
  theme)
    shift
    case "${1:-}" in
      auto|light|dark)
        _set theme "$1"
        if [ "$1" = "auto" ]; then
          echo "theme: auto (follows your OS setting)"
        else
          echo "theme: $1 (forced)"
        fi
        echo "  note: only the terminal browser needs this. Real browsers already"
        echo "        follow your system preference via prefers-color-scheme."
        ;;
      "") echo "theme: $(_get theme "auto")" ;;
      *)  echo "unknown theme '${1}'. Use: auto|light|dark" >&2; exit 2 ;;
    esac
    ;;
  browser)
    shift
    if [ -z "${1:-}" ]; then
      echo "terminal browser: $(_get browser "auto") (auto = best engine available)"
      echo
      echo "detected:"
      "$PY" "$PROJECT_DIR/bin/terminal_browser.py" --detect 2>/dev/null
      echo
      echo "  '!' marks a TEXT-ONLY browser. Those cannot render MCP Apps -- the apps"
      echo "  build their content with JavaScript, so a text browser shows an empty page"
      echo "  for a working app. Selecting one is allowed but only previews the text"
      echo "  fallback (what a non-visual client receives), never the app itself."
    elif [ "$1" = "auto" ] || [ "$1" = "-" ]; then
      _set browser ""; echo "terminal browser: auto (best engine available)"
    else
      _set browser "$1"; echo "terminal browser: $1"
    fi
    ;;
  assets)
    shift
    if [ "${1:-}" = "-" ] || [ -z "${1:-}" ]; then _set assets ""; echo "local asset dir cleared"
    else _set assets "$1"; echo "local asset dir: $1"; fi
    ;;
  port)
    shift; _set port "${1:-8777}"; echo "viewer port: ${1:-8777} (restart to apply)"
    ;;
  log)
    tail -n "${2:-30}" "$LOG" 2>/dev/null || echo "no log yet"
    ;;
  oauth)
    # Interactive OAuth for agents whose MCP client cannot run the auth flow
    # itself. The helper (bin/mcp_oauth_login.py) does auth-code + PKCE against
    # an OIDC issuer, stores tokens under ~/.mcp-app-viewer (0600), and prints
    # a bearer token for client config. --issuer/--client-id come from
    # MCP_OAUTH_ISSUER / MCP_OAUTH_CLIENT_ID in config.sh when not passed.
    shift
    case "${1:-}" in
      login|status|token|logout|watch|setup)
        "$PY" "$PROJECT_DIR/bin/mcp_oauth_login.py" "$@"
        ;;
      ""|*)
        echo "usage: mcp-app.sh oauth login|status|token|logout|watch [--issuer <url>] [--client-id <id>] [--flow auto|authcode|passcode] [--manual] [--margin <s>] [--once]" >&2
        echo "       mcp-app.sh oauth setup [--client bob|hermes|generic] [--server <url>] [--workspace <dir>] [--issuer <url>]" >&2
        exit 2
        ;;
    esac
    ;;
  status|*)
    echo "mcp-app-viewer"
    echo "  auto-open : $([ "$(_get enabled 0)" = "1" ] && echo ON || echo OFF)"
    echo "  viewer    : $(_server_running && echo "running (pid $(cat "$PIDFILE"), port $MCP_APP_PORT)" || echo "not running")"
    echo "  target    : $(_get target "$MCP_APP_TARGET")"
    echo "  position  : $(_get position "$MCP_APP_POSITION")"
    echo "  asset base: $(_get base "${MCP_APP_BASE_URL:-}")"
    echo "  asset dir : $(_get assets "${MCP_APP_ASSETS:-}")"
    echo "  term brwsr: $(_get browser "auto")"
    echo "  theme     : $(_get theme "auto")"
    echo "  last app  : $([ -s "$LAST_HTML" ] && echo "$(wc -c < "$LAST_HTML" | tr -d ' ') bytes captured" || echo "none")"
    echo "  log       : $LOG"
    ;;
esac
