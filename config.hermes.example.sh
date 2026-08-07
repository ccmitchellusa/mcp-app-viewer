# mcp-app-viewer: Hermes profile. Copied to ~/.config/mcp-app-viewer/hermes-cfg.sh
# by install-hermes.sh and loaded only when MCP_APP_PROFILE=hermes.
#
# Hermes usually runs headless or on a remote host, where opening a browser is
# either impossible or opens it on the WRONG machine. So the default here is to
# serve and print the URL and let a human decide — set MCP_APP_TARGET yourself if
# hermes is running on your desktop.

[ -f "$HOME/.config/mcp-app-viewer/config.sh" ] && . "$HOME/.config/mcp-app-viewer/config.sh"

export MCP_APP_PORT=8780
export MCP_APP_TARGET=none
