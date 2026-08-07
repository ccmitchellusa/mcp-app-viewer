---
description: Render MCP Apps (ui:// resources) in a real browser, and control the auto-open hook
argument-hint: "[on|off] | open <file> | last | target <system|chrome|safari|firefox|iterm2|vscode|none> | position <right|left|top|bottom> | base <url> | assets <dir> | stop | log"
allowed-tools: Bash(bash __MCP_APP_PROJECT_DIR__/bin/mcp-app.sh:*)
---

mcp-app-viewer output:

!`bash __MCP_APP_PROJECT_DIR__/bin/mcp-app.sh $ARGUMENTS`

Present the result above concisely.

- With no arguments it prints status — show it as-is; it is already compact.
- `on` / `off` toggles whether a tool result carrying an MCP App renders automatically.
  Mention that it is off by default deliberately: an agent that opens browser windows
  uninvited is worse than one that shows nothing.
- `target` chooses where the app appears. If the user picked `iterm2` and the output
  says a browser profile is missing, pass that setup line through verbatim — it is a
  one-time iTerm2 change (Settings > Advanced > `browserProfiles`, add a profile named
  `Browser`, restart iTerm2) and the viewer falls back to a normal browser until then.
  If they picked `chrome`, note that chrome-devtools tooling can then inspect the
  rendered app, not just display it.
- `position` sets pane placement for split targets. iTerm2 places new panes right or
  below, so `left`/`top` select the same axis rather than silently doing something
  else — say so if the output does.
- `base` sets an origin to proxy the app's relative assets from; `assets` points at a
  local directory instead. Use `assets` when the origin needs auth — a proxied fetch
  against an authenticated host returns 403, and the viewer reports that rather than
  serving an empty file.
- If a URL is printed, give it to the user plainly; the viewer keeps serving until
  `/mcp-app stop`.

Do not run any other command or edit any file for this request.
