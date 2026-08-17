#!/bin/bash
# Installer for mcp-app-viewer on Codex CLI.
#
# Codex hooks use the same stdin-JSON / exit-code contract as Claude Code, but are
# registered through ~/.codex/hooks.json and reviewed/trusted from /hooks. Run
# ./install.sh FIRST — this reuses its config, viewer, and display targets; only the
# hook wiring differs.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] \
  || die "shared config missing. Run ./install.sh first."
ok "shared config present"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

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

HOOKS_JSON="$CODEX_HOME/hooks.json"
[ -f "$HOOKS_JSON" ] || printf '{}\n' > "$HOOKS_JSON"
cp "$HOOKS_JSON" "$HOOKS_JSON.mcp-app-backup.$(date +%s)"
python3 - "$HOOKS_JSON" "bash $HOOKS_DIR/mcp-app-posttooluse-hook.sh" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
command = sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
except json.JSONDecodeError as exc:
    raise SystemExit(f"{path} is not valid JSON: {exc}") from exc

hooks = data.setdefault("hooks", {})
post = hooks.setdefault("PostToolUse", [])
filtered = []
for group in post:
    handlers = group.get("hooks") if isinstance(group, dict) else None
    commands = [h.get("command", "") for h in handlers or [] if isinstance(h, dict)]
    if not any("mcp-app-posttooluse" in c for c in commands):
        filtered.append(group)

filtered.append({"hooks": [{"type": "command", "command": command, "timeout": 15}]})
hooks["PostToolUse"] = filtered
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
ok "registered PostToolUse hook in $HOOKS_JSON (existing hooks preserved)"

CMD_DIR="$CODEX_HOME/prompts"
mkdir -p "$CMD_DIR"
sed "s|__MCP_APP_PROJECT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/commands/mcp-app.md" \
  > "$CMD_DIR/mcp-app.md"
ok "/mcp-app prompt installed to $CMD_DIR"

echo
echo "Codex wired. Auto-open stays OFF until you run: /mcp-app on"
