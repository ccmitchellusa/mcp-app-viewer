#!/bin/bash
# Claude Code PostToolUse hook — render an MCP App when a tool call produces one.
#
# MCP Apps (SEP-1865 / ext-apps) are HTML shipped as ui:// resources. A terminal
# client shows none of it, so without this you only ever learn an app is blank or
# broken by opening it by hand.
#
# Deliberately conservative — this fires on EVERY tool call, so it must be cheap and
# it must never act on a maybe:
#   * exits immediately unless auto-open is ON (`/mcp-app on`)
#   * requires a positive MCP-App signal, never "looks like HTML"
#   * never blocks: always exit 0, so a viewer problem cannot wedge the session
#
# TWO PATHS, because an app and its data arrive in SEPARATE tool calls
# --------------------------------------------------------------------
# Reading `ui://server/app` gives you the HTML and no data. Calling the tool gives
# you the data and no HTML — the binding between them lives in the tool's
# `_meta.ui.resourceUri`, which a PostToolUse hook never sees (verified by capturing
# real hook input: `tool_response` for an MCP tool call is the structured result and
# nothing else). Neither call alone can render a populated app.
#
# So: a resource read CACHES the app HTML per server, and a later tool call from that
# server renders the cached app with its result as the payload. Read once, then every
# call shows real data.
#
# LIMITATION, stated because it is invisible otherwise: the cache is keyed by SERVER,
# not by tool. Once a server's app is cached, every tool call to that server re-renders
# it. For a one-app server (the common case, and the demo) that is exactly right. For a
# server with many tools and one app, unrelated calls will re-render it with data the
# app does not understand — typically its empty state. Clear a server's cache with
# `rm ~/.config/mcp-app-viewer/apps/<server>.html`.

PROJECT_DIR="__MCP_APP_PROJECT_DIR__"
# Which agent installed this copy. Empty for Claude Code; "codex"/"kimi"/"hermes"
# for the others, which gives each its own port, state, and auto-open toggle.
export MCP_APP_PROFILE="__MCP_APP_PROFILE__"
CONFIG_DIR="${MCP_APP_CONFIG_DIR:-$HOME/.config/mcp-app-viewer}"
STATE="$CONFIG_DIR/state${MCP_APP_PROFILE:+.$MCP_APP_PROFILE}"
APPS_DIR="$CONFIG_DIR/apps${MCP_APP_PROFILE:+.$MCP_APP_PROFILE}"
LOG="${MCP_APP_LOG:-/tmp/mcp-app-viewer.log}"

log() { echo "[$(date '+%H:%M:%S')] posttool-hook: $*" >> "$LOG"; }
cache_name() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# Off by default: an agent that hijacks your browser uninvited is worse than one
# that shows nothing. Opt in with `/mcp-app on`.
enabled=$(grep -E '^enabled=' "$STATE" 2>/dev/null | tail -1 | cut -d= -f2)
[ "$enabled" = "1" ] || exit 0

command -v jq >/dev/null 2>&1 || { log "jq missing"; exit 0; }

input=$(cat)
tool_name=$(printf '%s' "$input" | jq -r '.tool_name // empty')
[ -n "$tool_name" ] || exit 0

# `tool_response` is the tool's result, straight from the hook payload.
#
# This used to be read out of the transcript by tool_use_id. Two bugs, both silent:
# the transcript is not always flushed when PostToolUse fires (the map demo's own
# resource read no-opped for exactly this reason), and the jq/grep extraction
# mangled what it did find — one render captured the app TWICE concatenated (7030
# bytes for a 3515-byte app), another captured 604 bytes of it. The payload on stdin
# has neither problem.
#
# NORMALISE IT. The shape is not consistent across tools: a resource read arrives as
# an OBJECT, while an MCP tool call arrives as a STRING containing JSON. That cost a
# debugging round — the hand-written test fixture used an object, passed, and the
# real call silently exited at the type check below because "string" != "object".
# Test fixtures that do not match the wire prove nothing.
resp=$(printf '%s' "$input" | jq -c '
  .tool_response
  | if type == "string" then (try fromjson catch .) else . end
  | if . == null then empty else . end
' 2>/dev/null)
[ -n "$resp" ] && [ "$resp" != "null" ] || exit 0

mkdir -p "$APPS_DIR"

# ---------------------------------------------------------------- path A: the app
# A resource read carrying app HTML. Cache it under the server it came from, then
# render it — with no data, because a resource read has none.
case "$resp" in
  *"profile=mcp-app"*|*"ui://"*)
    html=$(printf '%s' "$resp" | jq -r '
      [ .. | objects | select((.mimeType? // "") | test("mcp-app")) | .text? // empty ] as $tagged
      | ( $tagged + [ .. | objects | .contents? | arrays | .[]? | .text? // empty ] )
      | map(select(test("(?i)<!doctype|<html")))
      | .[0] // empty
    ' 2>/dev/null)
    if [ -n "${html//[[:space:]]/}" ]; then
      server=$(printf '%s' "$input" | jq -r '.tool_input.server // empty')
      if [ -n "$server" ]; then
        server_key=$(cache_name "$server")
        printf '%s' "$html" > "$APPS_DIR/$server_key.html"
        resource_uri=$(printf '%s' "$input" | jq -r '
          .tool_input.uri
          // .tool_input.resource_uri
          // .tool_input.resourceUri
          // empty
        ' 2>/dev/null)
        if [ -z "$resource_uri" ]; then
          resource_uri=$(printf '%s' "$resp" | jq -r '
            [ .. | objects | .uri? // empty ]
            | map(select(startswith("ui://")))
            | .[0] // empty
          ' 2>/dev/null)
        fi
        if [ -n "$resource_uri" ]; then
          printf '%s' "$html" > "$APPS_DIR/${server_key}__$(cache_name "$resource_uri").html"
          log "cached app for server '$server' resource '$resource_uri' ($(printf '%s' "$html" | wc -c | tr -d ' ') bytes)"
        else
          log "cached app for server '$server' ($(printf '%s' "$html" | wc -c | tr -d ' ') bytes)"
        fi
      fi
      printf '%s' "$html" | MCP_APP_NO_INLINE=1 MCP_APP_AUTO=1 bash "$PROJECT_DIR/bin/mcp-app.sh" open - >> "$LOG" 2>&1
      log "rendered app from $tool_name"
      exit 0
    fi
    log "mcp-app signal but no html extracted from $tool_name; checking for cached app data path"
    ;;
esac

# --------------------------------------------------------------- path B: the data
# An MCP tool call: mcp__<server>__<tool>. If that server has a cached app, render it
# with this result as the payload the app receives.
case "$tool_name" in
  mcp__*) ;;
  *) exit 0 ;;
esac

server=$(printf '%s' "$tool_name" | awk -F'__' '{print $2}')
[ -n "$server" ] || exit 0
server_key=$(cache_name "$server")
resource_uri=$(printf '%s' "$resp" | jq -r '
  ._meta."ui/resourceUri"
  // ._meta.ui.resourceUri
  // ._meta."openai/outputTemplate"
  // empty
' 2>/dev/null)
if [ -z "$resource_uri" ] && [ "$server" = "diagrammatic" ]; then
  view_type=$(printf '%s' "$input" | jq -r '.tool_input.view_type // empty' 2>/dev/null)
  case "$tool_name:$view_type" in
    mcp__diagrammatic__view_scene:3d) resource_uri="ui://diagrammatic/scene-viewer-3d" ;;
    mcp__diagrammatic__view_scene:*) resource_uri="ui://diagrammatic/scene-viewer-2d" ;;
    mcp__diagrammatic__view_scene_3d:*) resource_uri="ui://diagrammatic/scene-viewer-3d" ;;
    mcp__diagrammatic__view_chart:*) resource_uri="ui://diagrammatic/metrics-chart" ;;
    mcp__diagrammatic__view_dashboard:*) resource_uri="ui://diagrammatic/dashboard" ;;
    mcp__diagrammatic__view_theme_preview:*) resource_uri="ui://diagrammatic/theme-preview" ;;
  esac
fi
if [ -z "$resource_uri" ] && [ "$server" = "ibmcloud" ]; then
  case "$tool_name" in
    mcp__ibmcloud__ibmcloud_browse_resource) resource_uri="ui://ibmcloud/resource-views" ;;
    mcp__ibmcloud__ibmcloud_kg_visualize)    resource_uri="ui://ibmcloud/kg-viewer" ;;
    mcp__ibmcloud__ibmcloud_search_resources_ui) resource_uri="ui://ibmcloud/search" ;;
    mcp__ibmcloud__ibmcloud_support_browse)  resource_uri="ui://ibmcloud/support" ;;
    mcp__ibmcloud__ibmcloud_usage_browse)    resource_uri="ui://ibmcloud/usage" ;;
  esac
fi
if [ -n "$resource_uri" ] && [ -s "$APPS_DIR/${server_key}__$(cache_name "$resource_uri").html" ]; then
  cached="$APPS_DIR/${server_key}__$(cache_name "$resource_uri").html"
else
  cached="$APPS_DIR/$server_key.html"
fi
[ -s "$cached" ] || exit 0

# Only an OBJECT is a plausible structuredContent. A tool returning a bare string or
# a number has nothing an app could bind to, and rendering on it would open a window
# for every unrelated call.
kind=$(printf '%s' "$resp" | jq -r 'type' 2>/dev/null)
[ "$kind" = "object" ] || exit 0

data_file="$CONFIG_DIR/hook-data${MCP_APP_PROFILE:+.$MCP_APP_PROFILE}.json"
printf '%s' "$resp" > "$data_file"

# Never inline from the hook: a foreground browser would block the agent's session
# until someone quit it, which is a wedge, not a feature.
MCP_APP_NO_INLINE=1 MCP_APP_AUTO=1 bash "$PROJECT_DIR/bin/mcp-app.sh" open "$cached" "$data_file" >> "$LOG" 2>&1
log "rendered '$server' app with data from $tool_name"
exit 0
