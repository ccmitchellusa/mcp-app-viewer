#!/bin/bash
# Installer for mcp-app-viewer on Claude Code.
#
# Installs the PostToolUse hook into ~/.claude/hooks, registers it in
# ~/.claude/settings.json, and installs the /mcp-app command. Run this FIRST —
# the per-agent installers (install-codex.sh, install-kimi.sh, install-hermes.sh)
# reuse the shared config and viewer this sets up.
#
# Safe to re-run: it replaces its own entries and leaves other hooks alone.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

command -v jq >/dev/null 2>&1 || die "jq is required (brew install jq)"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# ------------------------------------------------------------------ config ---
CONFIG_DIR="$HOME/.config/mcp-app-viewer"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.sh" ]; then
  sed "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/config.example.sh" \
    > "$CONFIG_DIR/config.sh"
  ok "config written to $CONFIG_DIR/config.sh"
else
  ok "config already present (left as-is)"
fi

# ------------------------------------------------------------------- hooks ---
HOOKS_DIR="$HOME/.claude/hooks"
mkdir -p "$HOOKS_DIR"
sed -e "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" -e "s|__MCP_APP_PROFILE__||g" \
  "$SCRIPT_DIR/hooks/mcp-app-posttooluse-hook.sh" \
  > "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
chmod +x "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
ok "hook installed to $HOOKS_DIR"

SETTINGS="$HOME/.claude/settings.json"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.mcp-app-backup.$(date +%s)"

# APPEND to PostToolUse rather than replacing it — other tools (e.g. the sibling
# piper-tts project) register their own hooks, and clobbering them would be a
# silent, confusing breakage of an unrelated feature.
tmp=$(mktemp)
jq --arg cmd "bash $HOOKS_DIR/mcp-app-posttooluse-hook.sh" '
  .hooks //= {} |
  .hooks.PostToolUse //= [] |
  .hooks.PostToolUse |= (map(select(
      (.hooks // []) | map(.command // "") | any(test("mcp-app-posttooluse")) | not
    )) + [{"hooks": [{"type": "command", "command": $cmd, "timeout": 15}]}])
' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
ok "registered PostToolUse hook (existing hooks preserved)"

# ---------------------------------------------------------------- command ----
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
sed "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/commands/mcp-app.md" \
  > "$CMD_DIR/mcp-app.md"
ok "/mcp-app command installed"

# ------------------------------------------------------------------- bins ----
sed -i.bak "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/bin/mcp-app.sh"
rm -f "$SCRIPT_DIR/bin/mcp-app.sh.bak"
chmod +x "$SCRIPT_DIR/bin/"*.sh "$SCRIPT_DIR/bin/"*.py
ok "viewer scripts ready"

echo
echo "Installed. Auto-open is OFF by default — turn it on when you want it:"
echo "    /mcp-app on"
echo "    /mcp-app target iterm2      # or chrome | vscode | system"
echo "    /mcp-app position right     # right | left | top | bottom"
echo
echo "Render one by hand any time:  /mcp-app open <file.html>"
