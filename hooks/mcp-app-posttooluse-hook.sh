#!/bin/bash
# Claude Code PostToolUse hook — render an MCP App when a tool result carries one.
#
# MCP Apps (SEP-1865 / ext-apps) arrive as HTML: either a ui:// resource read, or a
# tool result whose content is `text/html;profile=mcp-app`. A terminal client shows
# none of it, so without this you only ever learn an app is blank or broken by
# someone opening it by hand.
#
# Deliberately conservative — this fires on EVERY tool call, so it must be cheap and
# it must never act on a maybe:
#   * exits immediately unless auto-open is ON (`/mcp-app on`)
#   * requires a positive MCP-App signal, never "looks like HTML"
#   * never blocks: always exit 0, so a viewer problem cannot wedge the session

PROJECT_DIR="__MCP_APP_PROJECT_DIR__"
# Which agent installed this copy. Empty for Claude Code; "codex"/"kimi"/"hermes"
# for the others, which gives each its own port, state, and auto-open toggle.
export MCP_APP_PROFILE="__MCP_APP_PROFILE__"
CONFIG_DIR="${MCP_APP_CONFIG_DIR:-$HOME/.config/mcp-app-viewer}"
STATE="$CONFIG_DIR/state${MCP_APP_PROFILE:+.$MCP_APP_PROFILE}"
LOG="${MCP_APP_LOG:-/tmp/mcp-app-viewer.log}"

log() { echo "[$(date '+%H:%M:%S')] posttool-hook: $*" >> "$LOG"; }

# Off by default: an agent that hijacks your browser uninvited is worse than one
# that shows nothing. Opt in with `/mcp-app on`.
enabled=$(grep -E '^enabled=' "$STATE" 2>/dev/null | tail -1 | cut -d= -f2)
[ "$enabled" = "1" ] || exit 0

command -v jq >/dev/null 2>&1 || { log "jq missing"; exit 0; }

input=$(cat)
transcript_path=$(echo "$input" | jq -r '.transcript_path // empty')
tool_use_id=$(echo "$input" | jq -r '.tool_use_id // empty')
transcript_path="${transcript_path/#\~/$HOME}"
[ -f "$transcript_path" ] || { log "no transcript"; exit 0; }

# Pull THIS tool call's result out of the transcript. Scanning from the end keeps
# the cost bounded regardless of session length.
result=$(tail -r "$transcript_path" 2>/dev/null | head -60 | while IFS= read -r line; do
  echo "$line" | jq -r --arg id "$tool_use_id" '
    select(.type == "user")
    | .message.content[]?
    | select(.type == "tool_result" and .tool_use_id == $id)
    | (.content // empty)
    | if type == "array" then (map(.text // empty) | join("\n")) else tostring end
  ' 2>/dev/null
done | head -c 4000000)

[ -n "$result" ] || exit 0

# Positive signal only. `profile=mcp-app` is the extension's own media type; a
# ui:// uri is the resource scheme. Matching bare "<!doctype html>" would fire on
# any tool that happens to return a web page, which is not what was asked for.
case "$result" in
  *"profile=mcp-app"*|*"ui://"*) ;;
  *) exit 0 ;;
esac

# Extract the HTML: either a resource read ({"contents":[{"text": "<html>"}]}) or a
# direct html payload. If neither yields markup, do nothing rather than serving a
# blank page that would read as a broken app.
html=$(printf '%s' "$result" | jq -r '
  (.contents[]?.text // empty),
  (.. | objects | select(.mimeType? // "" | test("mcp-app")) | .text? // empty)
' 2>/dev/null | grep -m1 -i -A100000 "<!doctype\|<html" )

if [ -z "${html//[[:space:]]/}" ]; then
  case "$result" in
    *"<!doctype"*|*"<!DOCTYPE"*|*"<html"*) html="$result" ;;
    *) log "mcp-app signal but no html extracted"; exit 0 ;;
  esac
fi

# Never inline from the hook: a foreground browser would block the agent's
# session until someone quit it, which is a wedge, not a feature.
printf '%s' "$html" | MCP_APP_NO_INLINE=1 bash "$PROJECT_DIR/bin/mcp-app.sh" open - >> "$LOG" 2>&1
log "rendered app from tool_use_id=$tool_use_id"
exit 0
