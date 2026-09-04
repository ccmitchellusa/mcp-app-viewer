# Editor fork verification checklist

This file records what should be measured for the VS Code-family display target.
It complements `docs/verification-matrix.md`, which tracks the project-wide
status. Use this file when verifying a specific fork such as Cursor or Bob IDE.

Status legend:

- VERIFIED — watched end to end on a real build
- UNVERIFIED — implemented, but not yet measured on a real build
- BLOCKED — cannot be measured on this host right now

## What to verify for each fork

1. CLI binary exists on PATH or can be targeted explicitly
2. `product.json` discovery returns the right:
   - CLI name
   - URI scheme
   - data folder / extension directory
3. `install.sh` copies the companion into the correct extension directory
4. `<scheme>://ccmitchellusa.mcp-app-viewer/open?...` reaches the right editor
5. Simple Browser exists in the build
6. `right`, `left`, `top`, `bottom` each create the expected editor group
7. repeated renders reuse one pane instead of opening a new one
8. `View -> Output -> MCP App Viewer` shows the command path taken

## Current fork matrix

| fork | status | notes |
|---|---|---|
| VS Code | VERIFIED | See `docs/verification-matrix.md`; measured originally on 1.128.1. |
| Cursor | UNVERIFIED | Compatibility is inferred through `product.json` discovery and shared editor APIs, but not yet measured end to end. |
| Bob IDE | UNVERIFIED | Same as Cursor: supported by discovery design, not yet measured end to end. |
| Windsurf | UNVERIFIED | Same as above. |
| VSCodium | UNVERIFIED | Same as above. |

## Command checklist

Discovery:

```bash
python3 bin/display_targets.py --editor-profile
```

Extension presence in the targeted fork:

```bash
<editor-cli> --list-extensions | grep -x ccmitchellusa.mcp-app-viewer
```

Render flow:

1. `./install.sh`
2. Reload the editor window
3. `/mcp-app target vscode`
4. `/mcp-app open <file.html>`
5. repeat with a second app and confirm pane reuse

## Recording evidence

For each verified fork, record:

- editor build/version
- detected CLI, URI scheme, data folder
- whether the correct editor opened
- whether all four positions behaved correctly
- whether pane reuse was confirmed
- whether the output channel was needed for diagnosis

Do not promote a fork to VERIFIED from code inspection alone.
