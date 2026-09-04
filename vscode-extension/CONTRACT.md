# VS Code companion contract

This file defines the runtime contract between `bin/display_targets.py` and the
bundled companion extension in `vscode-extension/extension.js`.

Status: internal contract for this repo. If the extension is ever split into a
separate repository or published independently, this file becomes the minimum
compatibility surface.

1. Identity

- Extension id: `ccmitchellusa.mcp-app-viewer`
- Package publisher: `ccmitchellusa`
- Activation event: `onUri`
- Main entrypoint: `./extension.js`

2. Launch mechanism

Runtime launches the extension by opening a handler URI of the form:

`<scheme>://ccmitchellusa.mcp-app-viewer/open?url=<encoded>&position=<encoded>`

Where:

- `<scheme>` is discovered from the editor build's `product.json`
- `url` is the localhost viewer URL served by `mcp_app_server.py`
- `position` is one of `right`, `left`, `top`, `bottom`

Runtime requirements:

- Discover CLI binary, URI scheme, and extensions directory from the target
  editor's `product.json` where possible
- Verify the companion extension is installed before claiming success
- Fall back honestly when the target is unavailable

3. Accepted query parameters

Required:

- `url`

Optional:

- `position`

Unknown query parameters must be ignored unless the extension adopts them in a
future documented revision.

4. Viewer URL policy

The extension must reject any URL that is not:

- `http:`
- loopback-hosted

Allowed hosts:

- `127.0.0.1`
- `localhost`
- `::1`
- `[::1]`

Security rationale: any web page can trigger a `vscode://`-style URI. A handler
that opens arbitrary URLs would let a hostile page render arbitrary content
inside the editor chrome.

5. Placement behavior

- `right` -> `workbench.action.newGroupRight`
- `left` -> `workbench.action.newGroupLeft`
- `top` -> `workbench.action.newGroupAbove`
- `bottom` -> `workbench.action.newGroupBelow`

The extension should remember the editor group it created and reuse it while the
column remains open.

6. Browser-open behavior

Preferred path:

- call `simpleBrowser.api.open` with placement options

Fallback path:

- call `simpleBrowser.show` when the API command is missing or fails

The extension must log which path ran so "wrong place" and "missing API"
remain diagnosable.

7. Failure behavior

The extension must surface visible failures for:

- missing `url`
- rejected non-local URL
- inability to open the requested URL via Simple Browser commands

It must also write diagnostics to the `MCP App Viewer` output channel.

8. Ownership split

Runtime (`bin/display_targets.py`) owns:

- target discovery
- extension installation checks
- URI emission
- browser fallback policy

Extension (`vscode-extension/extension.js`) owns:

- URI parsing
- URL validation
- editor group lifecycle
- Simple Browser invocation
- last-opened URL handling (`mcpAppViewer.showLast`)

9. Compatibility rules

Any change to the following requires updating both sides together and updating
`test/vscode_contract_test.py`:

- extension id
- handler path (`/open`)
- accepted position values
- allowed host policy
- package versioning policy if it diverges from repo `VERSION`
