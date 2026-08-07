#!/bin/bash
# Installer for mcp-app-viewer on Codex CLI.
#
# Codex hooks use the same stdin-JSON / exit-code contract as Claude Code, but live
# in ~/.codex/hooks rather than settings.json. Run ./install.sh FIRST — this reuses
# its config, viewer, and display targets; only the hook wiring differs.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] \
  || die "shared config missing. Run ./install.sh first."
ok "shared config present"

PROFILE_CFG="$HOME/.config/mcp-app-viewer/codex.sh"
if [ ! -f "$PROFILE_CFG" ]; then
  cp "$SCRIPT_DIR/config.codex.example.sh" "$PROFILE_CFG"
  ok "codex profile written to $PROFILE_CFG"
else
  ok "codex profile already present (left as-is)"
fi

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
[ -d "$CODEX_HOME" ] || die "codex home not found: $CODEX_HOME"

HOOKS_DIR="$CODEX_HOME/hooks"
mkdir -p "$HOOKS_DIR"
sed -e "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" -e "s|__MCP_APP_PROFILE__|codex|g" \
  "$SCRIPT_DIR/hooks/mcp-app-posttooluse-hook.sh" \
  > "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
chmod +x "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
ok "hook installed to $HOOKS_DIR"

CMD_DIR="$CODEX_HOME/prompts"
mkdir -p "$CMD_DIR"
sed "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/commands/mcp-app.md" \
  > "$CMD_DIR/mcp-app.md"
ok "/mcp-app prompt installed to $CMD_DIR"

echo
echo "Codex wired. Auto-open stays OFF until you run: /mcp-app on"
