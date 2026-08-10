# mcp-app-viewer shared config. Copied to ~/.config/mcp-app-viewer/config.sh on install.
# Runtime settings live in ~/.config/mcp-app-viewer/state and are changed with /mcp-app.

# Local port the viewer serves on.
export MCP_APP_PORT="${MCP_APP_PORT:-8777}"

# Default display target: system | chrome | safari | firefox | edge | brave | arc
#                         | iterm2 | vscode | none
export MCP_APP_TARGET="${MCP_APP_TARGET:-system}"

# Pane placement for split targets (iterm2, vscode): right | left | top | bottom
export MCP_APP_POSITION="${MCP_APP_POSITION:-right}"

# Origin to proxy an app's RELATIVE asset paths from (e.g. https://your-mcp-host).
# Leave empty for self-contained apps. Note an authenticated origin will 403 —
# use MCP_APP_ASSETS instead when that happens.
export MCP_APP_BASE_URL="${MCP_APP_BASE_URL:-}"

# Local directory serving the app's relative assets, mounted at /.
# Preferred over MCP_APP_BASE_URL when you have the assets on disk.
export MCP_APP_ASSETS="${MCP_APP_ASSETS:-}"

export MCP_APP_LOG="${MCP_APP_LOG:-/tmp/mcp-app-viewer.log}"
export MCP_APP_PYTHON="${MCP_APP_PYTHON:-python3}"

# Interactive OAuth login (`/mcp-app oauth ...`): the OIDC issuer and registered
# public client id of the MCP server you log in to. For the IBM Cloud MCP server
# these are the App ID oauthServerUrl and the MCP client application id (see
# commands/mcp-app.md). Both are non-secret.
export MCP_OAUTH_ISSUER="${MCP_OAUTH_ISSUER:-}"
export MCP_OAUTH_CLIENT_ID="${MCP_OAUTH_CLIENT_ID:-}"
