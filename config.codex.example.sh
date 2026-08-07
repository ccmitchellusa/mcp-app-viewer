# mcp-app-viewer: Codex profile. Copied to ~/.config/mcp-app-viewer/codex.sh by
# install-codex.sh and loaded only when MCP_APP_PROFILE=codex.
#
# Inherits the shared config, then overrides. Plain `export`, not ':=' — the
# shared config has already set these, so ':=' would be a no-op here.

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] && . "$HOME/.config/mcp-app-viewer/config.sh"

# A distinct port so Codex and Claude Code can each hold a viewer open without
# killing each other's server.
export MCP_APP_PORT=8778
