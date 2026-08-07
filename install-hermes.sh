#!/bin/bash
# Installer for mcp-app-viewer on Hermes.
#
# Hermes has no PostToolUse hook contract, so rather than pretending otherwise this
# installs a PROFILE that exposes the viewer to a hermes session: the agent (or you)
# calls bin/mcp-app.sh directly. Same viewer, same config, manual trigger.
#
# Run ./install.sh FIRST.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] \
  || die "shared config missing. Run ./install.sh first."
ok "shared config present"

PROFILE_CFG="$HOME/.config/mcp-app-viewer/hermes-cfg.sh"
if [ ! -f "$PROFILE_CFG" ]; then
  cp "$SCRIPT_DIR/config.hermes.example.sh" "$PROFILE_CFG"
  ok "hermes profile written to $PROFILE_CFG"
else
  ok "hermes profile already present (left as-is)"
fi

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
[ -d "$HERMES_HOME" ] || die "hermes home not found: $HERMES_HOME"

PROFILE="$HOME/.config/mcp-app-viewer/hermes.sh"
cat > "$PROFILE" <<PROF
# Sourced by a hermes session to expose the MCP App viewer.
export MCP_APP_PROFILE=hermes
. "\$HOME/.config/mcp-app-viewer/config.sh"
[ -f "\$HOME/.config/mcp-app-viewer/hermes-cfg.sh" ] && . "\$HOME/.config/mcp-app-viewer/hermes-cfg.sh"
mcp_app() { bash "$SCRIPT_DIR/bin/mcp-app.sh" "\$@"; }
export -f mcp_app 2>/dev/null || true
PROF
ok "hermes profile written to $PROFILE"

SKILL_DIR="$HERMES_HOME/skills/mcp-app-viewer"
mkdir -p "$SKILL_DIR"
cat > "$SKILL_DIR/SKILL.md" <<SKILL
---
name: mcp-app-viewer
description: Render an MCP App (ui:// resource HTML) in a browser or an iTerm2/VS Code pane.
---

# Viewing an MCP App

MCP Apps are HTML delivered as \`ui://\` resources. Nothing in a terminal renders
them, so an app that is blank or broken looks identical to one that works.

Render one:

    bash $SCRIPT_DIR/bin/mcp-app.sh open <file.html>
    cat app.html | bash $SCRIPT_DIR/bin/mcp-app.sh open -

Settings (persist across sessions):

    bash $SCRIPT_DIR/bin/mcp-app.sh target iterm2      # system|chrome|iterm2|vscode|none
    bash $SCRIPT_DIR/bin/mcp-app.sh position right     # right|left|top|bottom
    bash $SCRIPT_DIR/bin/mcp-app.sh assets <dir>       # local dir for relative assets
    bash $SCRIPT_DIR/bin/mcp-app.sh stop

If the app's assets live behind an authenticated origin, use \`assets\` with a local
directory — proxying to an authenticated host returns 403.
SKILL
ok "hermes skill installed to $SKILL_DIR"

echo
echo "Hermes wired (manual trigger — hermes has no PostToolUse hook)."
