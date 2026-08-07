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

# -------------------------------------------------------- editor extension ----
# Optional and best-effort: only the `vscode` display target needs it, and a machine
# without an editor should not see an error for a target it will never use.
#
# Works against ANY VS Code fork (VSCodium, Cursor, Windsurf, Bob IDE). The CLI name,
# URI scheme, and extensions directory are DISCOVERED from the build's own
# product.json rather than assumed — guessing them fails silently and in the worst
# way, since firing `vscode://` on a machine with two editors opens a pane in the
# wrong one instead of erroring. display_targets.py owns that discovery; this just
# asks it, so there is one implementation and not two that drift.
#
# Installed by COPY rather than a .vsix: packaging one needs `vsce`, which needs npm,
# and the extension is dependency-free plain JS precisely so this project does not
# grow a node toolchain.
EDITOR_PROFILE=$("${MCP_APP_PYTHON:-python3}" "$SCRIPT_DIR/bin/display_targets.py" --editor-profile 2>/dev/null)
EDITOR_FOUND=$(printf '%s' "$EDITOR_PROFILE" | sed -n 's/.*"found": *\([a-z]*\).*/\1/p')

if [ "$EDITOR_FOUND" = "true" ]; then
  EDITOR_CLI=$(printf '%s' "$EDITOR_PROFILE" | sed -n 's/.*"cli": *"\([^"]*\)".*/\1/p')
  EDITOR_EXT_DIR=$(printf '%s' "$EDITOR_PROFILE" | sed -n 's/.*"extensions_dir": *"\([^"]*\)".*/\1/p')
  EDITOR_SCHEME=$(printf '%s' "$EDITOR_PROFILE" | sed -n 's/.*"url_protocol": *"\([^"]*\)".*/\1/p')

  EXT_DEST="$EDITOR_EXT_DIR/ccmitchellusa.mcp-app-viewer-0.1.0"
  mkdir -p "$EXT_DEST"
  cp "$SCRIPT_DIR/vscode-extension/package.json" "$SCRIPT_DIR/vscode-extension/extension.js" "$EXT_DEST/"

  if "$EDITOR_CLI" --list-extensions 2>/dev/null | grep -qx "ccmitchellusa.mcp-app-viewer"; then
    ok "editor extension installed for '$EDITOR_CLI' (scheme ${EDITOR_SCHEME}://) — reload the editor to activate"
  else
    # Copied but not discovered — report it rather than claim success, since the
    # vscode target checks exactly this listing before it will try.
    printf '  \033[33m!!\033[0m copied to %s but "%s --list-extensions" does not show it; the vscode target will fall back\n' \
      "$EXT_DEST" "$EDITOR_CLI"
  fi
else
  ok "no editor CLI found — skipping the companion extension (only the vscode target needs it)"
fi

# ------------------------------------------------------------------- bins ----
# No path substitution here any more: bin/mcp-app.sh derives PROJECT_DIR from its
# own location. Substituting used to work exactly once -- the rewritten path was
# then committed, so a fresh clone elsewhere kept pointing at the author's machine.
chmod +x "$SCRIPT_DIR/bin/"*.sh "$SCRIPT_DIR/bin/"*.py
ok "viewer scripts ready"

echo
echo "Installed. Auto-open is OFF by default — turn it on when you want it:"
echo "    /mcp-app on"
echo "    /mcp-app target iterm2      # or chrome | vscode | system"
echo "                                # vscode needs a VS Code reload after install"
echo "    /mcp-app position right     # right | left | top | bottom"
echo
echo "Render one by hand any time:  /mcp-app open <file.html>"
