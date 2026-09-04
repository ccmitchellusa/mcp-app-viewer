# Design and delivery plan: VS Code companion extension

Status: accepted plan, implemented as a bundled subproject in this repository.

## Decision

Keep the VS Code-family extension in this repository as a first-class subproject,
not a separate repository.

## Why

The extension is not an independent product today. It is one display target in a
larger mcp-app-viewer system whose behavior depends on repo-local pieces:

- `bin/mcp_app_server.py` serves the localhost viewer URL
- `bin/display_targets.py` discovers editor forks and emits handler URIs
- `install.sh` installs the companion into the detected editor extension dir
- `README.md` and `docs/verification-matrix.md` describe the end-to-end behavior

Splitting the extension now would create coordination overhead for a tiny,
lockstep contract:

- extension id and handler URI format
- install/discovery assumptions
- fallback semantics when the extension is absent
- verification and release documentation

## Goals

1. Make the extension feel like a clean internal subproject
2. Document the runtime/extension boundary explicitly
3. Add cheap contract verification so the two sides do not drift
4. Keep one release artifact and one version stream for now
5. Preserve a clean future split path if the extension later becomes a product

## Non-goals

- Adding npm, TypeScript, a bundler, or marketplace packaging
- Replacing the shell/server core with an extension-only architecture
- Claiming fork compatibility beyond measured verification

Note: local VSIX packaging is now supported as release prep. Full marketplace
publication is still not the primary delivery path for day-to-day use.

## Subproject layout

The extension subproject lives under `vscode-extension/`:

- `package.json` — extension metadata and activation contract
- `extension.js` — URI handler, validation, editor-group placement, diagnostics
- `README.md` — subproject purpose, installation model, ownership boundary
- `CONTRACT.md` — wire contract with `bin/display_targets.py`
- `CHANGELOG.md` / `LICENSE` / `.vscodeignore` — packaging and registry-prep assets

Release-prep helper:

- `release/build-vscode-extension.sh` — builds a `.vsix` plus checksum and version metadata

## Ownership boundary

Runtime side responsibilities:

- discover the target editor CLI / URI scheme / data folder from `product.json`
- determine whether the extension is installed
- emit the handler URI
- fall back honestly when the editor route is unavailable

Extension side responsibilities:

- parse the handler URI
- reject non-loopback URLs
- create or reuse the target editor group
- open the page in Simple Browser
- record diagnostics in the output channel

## Verification plan

Add lightweight verification rather than a new toolchain.

Implemented checks:

- `test/vscode_contract_test.py`
  - package version matches repo `VERSION`
  - package id matches the runtime constant
  - `onUri` activation remains present
  - the handler path and position contract remain aligned
  - expected Simple Browser commands and allowed-host policy remain present

Manual verification documentation:

- `docs/editor-fork-verification.md`
  - per-fork checklist for VS Code, Cursor, Bob IDE, Windsurf, VSCodium
- `test/display_targets_test.py`
  - host-aware editor targeting prefers the current editor host over PATH order
  - env override, host hint, and PATH fallback precedence stay explicit

## Future slices

If the extension grows, do it in this order:

1. Multi-fork installation
   - install the companion into all detected forks, then target the active one
2. Stronger fork verification
   - explicitly measure Cursor and Bob IDE behavior, not just infer compatibility
3. Richer editor UX only if needed
   - reload/pin/history commands or a custom webview wrapper

## Split criteria

Revisit a separate repository only if most changes become extension-driven rather
than runtime-driven, for example:

- independent marketplace publishing
- separate release cadence
- significant editor-only UI and packaging work
- reuse by projects other than mcp-app-viewer

Until then, the monorepo keeps one source of truth and the lowest coordination
cost.
