#!/bin/bash
# Installer for mcp-app-viewer on Kimi Code.
#
# Kimi Code uses the same stdin-JSON / exit-code hook contract, registered as
# [[hooks]] entries in ~/.kimi-code/config.toml. Run ./install.sh FIRST.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] \
  || die "shared config missing. Run ./install.sh first."
ok "shared config present"

PROFILE_CFG="$HOME/.config/mcp-app-viewer/kimi.sh"
if [ ! -f "$PROFILE_CFG" ]; then
  cp "$SCRIPT_DIR/config.kimi.example.sh" "$PROFILE_CFG"
  ok "kimi profile written to $PROFILE_CFG"
else
  ok "kimi profile already present (left as-is)"
fi

KIMI_HOME="${KIMI_HOME:-$HOME/.kimi-code}"
CFG="$KIMI_HOME/config.toml"
[ -f "$CFG" ] || die "kimi config not found: $CFG"

HOOKS_DIR="$KIMI_HOME/hooks"
mkdir -p "$HOOKS_DIR"
sed -e "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" -e "s|__MCP_APP_PROFILE__|kimi|g" \
  "$SCRIPT_DIR/hooks/mcp-app-posttooluse-hook.sh" \
  > "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
chmod +x "$HOOKS_DIR/mcp-app-posttooluse-hook.sh"
ok "hook installed to $HOOKS_DIR"

cp "$CFG" "$CFG.mcp-app-backup.$(date +%s)"
# Append only if absent — re-running must not stack duplicate hook entries.
if grep -q "mcp-app-posttooluse-hook" "$CFG"; then
  ok "config.toml already registers the hook (left as-is)"
else
  cat >> "$CFG" <<TOML

[[hooks]]
event = "PostToolUse"
command = "bash $HOOKS_DIR/mcp-app-posttooluse-hook.sh"
timeout = 15
TOML
  ok "registered PostToolUse hook in $CFG"
fi

echo
echo "Kimi wired. Auto-open stays OFF until you run: /mcp-app on"
