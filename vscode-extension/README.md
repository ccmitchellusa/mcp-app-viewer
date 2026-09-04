# MCP App Viewer companion extension

This directory holds the bundled VS Code-family companion extension for
mcp-app-viewer.

It is not a separate product. It is one display target inside the larger
mcp-app-viewer system, alongside browser, iTerm2, and terminal targets.

What it does

- Registers a URI handler for the extension id `ccmitchellusa.mcp-app-viewer`
- Validates that the requested viewer URL is local loopback HTTP only
- Opens the viewer URL in VS Code's built-in Simple Browser
- Reuses the editor group it created so repeated renders replace the app in place

Why it exists

VS Code exposes Simple Browser only as editor commands such as
`simpleBrowser.api.open` and `simpleBrowser.show`. The CLI can trigger a URI, but
it cannot invoke those commands directly. The extension is therefore the narrow
bridge between:

- runtime-side discovery and launch in `bin/display_targets.py`
- editor-side placement and rendering in `vscode-extension/extension.js`

Installation model

`install.sh` discovers the active VS Code-family editor profile through
`bin/display_targets.py --editor-profile` and copies this directory's runtime
files into that editor's extensions directory. No `.vsix`, `vsce`, npm, or build
step is required.

This companion is intentionally dependency-free plain JavaScript so the parent
project remains installable with only shell + Python prerequisites.

Install instructions

Standard install:

1. From the repo root, run `./install.sh`
2. Watch for the line saying which editor CLI the companion was installed for
3. Reload that editor window once:
   - Command Palette -> `Developer: Reload Window`
4. In your agent session, set `/mcp-app target vscode`
5. Render an app with `/mcp-app open <file.html>`

What `install.sh` actually does:

- asks `python3 bin/display_targets.py --editor-profile` which editor profile is active
- copies `vscode-extension/package.json` and `vscode-extension/extension.js` into that
  editor's extensions directory
- checks `<editor-cli> --list-extensions` to see whether the companion is discoverable

There is no separate packaging or publishing step.

Registry / VSIX packaging

This repo can now build a local VSIX artifact for registry validation.

Build it with:

```bash
bash release/build-vscode-extension.sh
```

Outputs go to `dist/vscode-extension/` by default:

- `ccmitchellusa.mcp-app-viewer-<version>.vsix`
- `ccmitchellusa.mcp-app-viewer-<version>.vsix.sha256`
- `mcp-app-viewer-vscode-extension-version.json`

Packaging notes:

- the extension version must match the repo `VERSION`
- packaging uses `npx @vscode/vsce package`
- `vscode-extension/.vscodeignore` trims repo-only files from the VSIX
- `vscode-extension/CHANGELOG.md` and `vscode-extension/LICENSE` are included for
  registry/readiness purposes

Publishing is still a deliberate step, not something `install.sh` does automatically.
The expected flow is:

1. build the VSIX locally
2. install/test it in a real editor
3. create a git tag/release for the matching version
4. publish to the target registry with the publisher credentials for `ccmitchellusa`

VS Code

- Typical CLI: `code`
- Typical URI scheme: `vscode://`
- Typical extensions dir: `~/.vscode/extensions`

If you are running from a VS Code integrated terminal, the viewer should prefer VS Code
as the current host editor automatically.

Cursor

- Typical CLI: `cursor`
- Typical URI scheme: `cursor://`
- Typical data folder: `.cursor`

If you are running from a Cursor integrated terminal, the viewer should prefer Cursor
over a separate VS Code install even when `code` is also on PATH.

Bob IDE

- Typical CLI: `bob`
- URI scheme and data folder are discovered from Bob IDE's `product.json`

If you are running from a Bob IDE integrated terminal, the viewer should prefer Bob IDE
as the current host editor.

How to verify what editor will be targeted

Run:

```bash
python3 bin/display_targets.py --editor-profile
```

It prints JSON like:

```json
{
  "cli": "cursor",
  "url_protocol": "cursor",
  "data_folder": ".cursor",
  "extensions_dir": "/Users/you/.cursor/extensions",
  "found": true,
  "source": "host",
  "host_hint": "cursor"
}
```

Meaning of the extra fields:

- `source=env` — `MCP_APP_EDITOR_CLI` overrode detection
- `source=host` — the current integrated editor host was detected and chosen
- `source=path` — no host editor was detected, so the first matching CLI on PATH won
- `host_hint` — best-effort guess of the current host editor, even if PATH fallback won

Manual overrides

If detection is wrong or your editor build has unusual metadata, override any piece with:

- `MCP_APP_EDITOR_CLI`
- `MCP_APP_EDITOR_URI_SCHEME`
- `MCP_APP_EDITOR_DATA_FOLDER`
- `MCP_APP_EDITOR_EXT_DIR`

Example:

```bash
export MCP_APP_EDITOR_CLI=cursor
export MCP_APP_EDITOR_URI_SCHEME=cursor
python3 bin/display_targets.py --editor-profile
```

Troubleshooting

Wrong editor opens:

1. Run `python3 bin/display_targets.py --editor-profile`
2. Check `cli`, `source`, and `host_hint`
3. If `source=path` but you expected Cursor or Bob IDE, run the agent from that editor's
   integrated terminal, or set `MCP_APP_EDITOR_CLI` explicitly

Nothing opens:

1. Make sure you reloaded the editor after `./install.sh`
2. Run `<editor-cli> --list-extensions | grep -x ccmitchellusa.mcp-app-viewer`
3. If it is missing, re-run `./install.sh`
4. Check `View -> Output -> MCP App Viewer`

It falls back to a browser instead of the editor:

- The runtime does this when the companion extension is not installed or not discoverable
- Re-run `./install.sh`, reload the editor, then confirm it appears in `--list-extensions`

Pane opens in the editor but not where expected:

- Preferred path is `simpleBrowser.api.open`
- Fallback path is `simpleBrowser.show`, which may land wherever focus is
- Check `View -> Output -> MCP App Viewer` to see which path ran

Fork-specific concern:

- The CLI name, URI scheme, and extension directory are discovered from `product.json`
- Simple Browser availability is not discoverable from outside the editor; a stripped fork
  may still accept the URI handler but lack the built-in browser surface

Ownership boundary

Runtime side owns:

- editor CLI discovery
- `product.json` discovery for fork-specific URI scheme and data folder
- extension presence checks
- fallback to browser targets when the editor target is unavailable

Extension side owns:

- URI validation
- editor-group creation and reuse
- calling Simple Browser commands
- output-channel diagnostics for "nothing appeared" failures

Contract

The wire contract between the runtime and the extension is documented in
`vscode-extension/CONTRACT.md`.

Verification

Project-level verification lives in:

- `docs/verification-matrix.md`
- `docs/editor-fork-verification.md`
- `docs/vscode-extension-design.md`

Manual smoke check

1. Run `./install.sh`
2. Reload the editor window
3. Set `/mcp-app target vscode`
4. Render an app with `/mcp-app open <file.html>`
5. Confirm the app opens in Simple Browser and that repeated renders reuse one pane
6. Check `View -> Output -> MCP App Viewer` if nothing appears
