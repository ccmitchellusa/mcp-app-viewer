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
#   mcp-app.sh last             re-open the most recently captured app
#   mcp-app.sh stop             stop the local viewer server
#   mcp-app.sh base <url|->     set/clear the origin used to resolve relative assets
#   mcp-app.sh target <name>    where to display: system|chrome|safari|firefox|iterm2|vscode|none
#   mcp-app.sh assets <dir|->   local dir serving the app's relative assets
#   mcp-app.sh position <where> pane placement for split targets: right|left|top|bottom
#   mcp-app.sh log              tail the activity log

set -uo pipefail

PROJECT_DIR="/Volumes/DATA/Code/mcp-app-viewer"
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
  local args=(--html-file "$LAST_HTML" --port "$MCP_APP_PORT" --browser "$target" --position "$pos")
  [ -n "$base" ]   && args+=(--base-url "$base")
  [ -n "$assets" ] && args+=(--assets-dir "$assets")

  nohup "$PY" "$PROJECT_DIR/bin/mcp_app_server.py" "${args[@]}" >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 0.5
  if _server_running; then
    echo "serving http://127.0.0.1:$MCP_APP_PORT/app/index.html"
    if [ -n "$base" ]; then echo "assets proxy: $base"; fi
    log "opened app ($(printf '%s' "$html" | wc -c | tr -d ' ') bytes)"
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
      system|chrome|safari|firefox|edge|brave|arc|iterm2|vscode|none)
        _set target "$1"; echo "display target: $1"
        # NOTE: written as `if`, not `[ ... ] && echo`. A trailing &&-guard that
        # evaluates false makes it the branch's last status, so the whole script
        # exits 1 on a successful command — the exact bug this project's sibling
        # (claude-code-piper-tts) shipped in ll-tts-voice.sh.
        if [ "$1" = "iterm2" ]; then
          echo "  note: needs iTerm2 'browserProfiles' advanced setting + a profile named 'Browser' (restart iTerm2); otherwise it falls back to a browser"
        fi
        if [ "$1" = "chrome" ]; then
          echo "  note: chrome-devtools tooling can then inspect the rendered app"
        fi
        ;;
      "") echo "display target: $(_get target "$MCP_APP_TARGET")" ;;
      *)  echo "unknown target '${1}'. Use: system|chrome|safari|firefox|edge|brave|arc|iterm2|vscode|none" >&2; exit 2 ;;
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
  status|*)
    echo "mcp-app-viewer"
    echo "  auto-open : $([ "$(_get enabled 0)" = "1" ] && echo ON || echo OFF)"
    echo "  viewer    : $(_server_running && echo "running (pid $(cat "$PIDFILE"), port $MCP_APP_PORT)" || echo "not running")"
    echo "  target    : $(_get target "$MCP_APP_TARGET")"
    echo "  position  : $(_get position "$MCP_APP_POSITION")"
    echo "  asset base: $(_get base "${MCP_APP_BASE_URL:-}")"
    echo "  asset dir : $(_get assets "${MCP_APP_ASSETS:-}")"
    echo "  last app  : $([ -s "$LAST_HTML" ] && echo "$(wc -c < "$LAST_HTML" | tr -d ' ') bytes captured" || echo "none")"
    echo "  log       : $LOG"
    ;;
esac
