// MCP App Viewer — VS Code companion.
//
// VS Code exposes Simple Browser only as a COMMAND (`simpleBrowser.show` /
// `simpleBrowser.api.open`), and commands cannot be invoked from the CLI. That is the
// entire reason this extension exists: it is the smallest possible bridge from a URI
// the CLI *can* trigger to a command only an extension can call.
//
// WHY WE NO LONGER USE SIMPLE BROWSER
// -----------------------------------
// Simple Browser is a SINGLETON. From its own shipped source:
//
//     show(url, options) { if (this._activeView) { this._activeView.show(...) } ... }
//
// One `_activeView`, reused. Ask it for a second app and it replaces the first — so
// "a pane with Tokyo, a pane with Raleigh, a pane with New York" collapses into one
// tab showing whichever rendered last, and nothing says the others were discarded.
//
// So this extension owns its panels: one webview per named SLOT, each holding an
// iframe on that slot's viewer port. A slot is a stable identity, which is what makes
// "put this one below the Reykjavik pane" expressible here as well as in iTerm2.
//
// Deliberately dependency-free plain JavaScript — no TypeScript, no bundler, no
// node_modules. The parent project advertises zero third-party dependencies.

const vscode = require('vscode')

// ---------------------------------------------------------------- security ---
// A URI handler is a genuine attack surface: ANY web page can navigate to a
// `vscode://` link, so a handler that opens whatever URL it is handed lets a hostile
// page render arbitrary content inside the user's editor, wearing the editor's
// chrome. This extension therefore serves exactly one purpose — showing a viewer that
// is already running on this machine — and refuses everything else.
//
// Loopback only, http only. Not "starts with localhost" (`localhost.evil.com`
// passes that), not a substring check — a parsed hostname compared against an exact
// allow-list.
const ALLOWED_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])

function isLocalViewerUrl(raw) {
  let parsed
  try {
    parsed = new URL(raw)
  } catch {
    return false
  }
  if (parsed.protocol !== 'http:') return false
  return ALLOWED_HOSTS.has(parsed.hostname)
}

// A slot name becomes a panel title and a Map key, and arrives over a URI that any
// page can trigger. Keep it boring.
function safeSlot(raw) {
  const name = (raw || 'main').trim()
  return /^[A-Za-z0-9._-]{1,64}$/.test(name) ? name : 'main'
}

// ---------------------------------------------------------------- placement ---
// VS Code has real editor-group placement, unlike iTerm2 (which only ever puts a new
// pane right or below). So `left` and `top` mean what they say here.
//
// A tab is NOT a pane. `ViewColumn.Beside` sounds like it splits, but with a single
// group and nothing to be beside of it just opens in the current one — which is how
// the first version landed the app as a tab instead of a side-by-side pane. So create
// the group EXPLICITLY, in the requested direction, and target that.
const GROUP_COMMAND = {
  right: 'workbench.action.newGroupRight',
  left: 'workbench.action.newGroupLeft',
  top: 'workbench.action.newGroupAbove',
  bottom: 'workbench.action.newGroupBelow',
}

/** slot name -> WebviewPanel. The registry that makes panes addressable. */
const panels = new Map()

function paneHtml(url) {
  // The app runs in an iframe on its own loopback origin, exactly as Simple Browser
  // does it. `allow-same-origin` is load-bearing and not a loosening: the viewer's
  // host shell and the app it embeds must be same-origin to postMessage each other,
  // and without it the app would sit on its empty state forever.
  const src = String(url).replace(/"/g, '&quot;')
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:* http://localhost:*; style-src 'unsafe-inline';">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: var(--vscode-editor-background); }
  iframe { border: 0; display: block; width: 100%; height: 100%; }
</style>
</head>
<body><iframe src="${src}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe></body>
</html>`
}

async function columnFor(position, near, log) {
  // Anchored: focus the pane we are placing relative to, so the new group is created
  // beside THAT one rather than beside whatever the user last clicked. Same reasoning
  // as the iTerm2 side, where an unanchored split follows the frontmost window.
  const anchor = near && panels.get(near)
  if (anchor) {
    anchor.reveal(anchor.viewColumn, false)
    log(`anchored to pane '${near}' in column ${anchor.viewColumn}`)
  }
  const command = GROUP_COMMAND[position] || GROUP_COMMAND.right
  await vscode.commands.executeCommand(command)
  return vscode.window.tabGroups.activeTabGroup.viewColumn
}

async function openPane(url, slot, position, near, log) {
  const pos = (position || 'right').toLowerCase()

  const existing = panels.get(slot)
  if (existing) {
    // The slot's URL never changes while it lives, and the page polls /version and
    // reloads itself when the render behind it changes. So revealing is enough —
    // and rebuilding the webview would throw away the app's state for nothing.
    existing.webview.html = paneHtml(url)
    existing.reveal(existing.viewColumn, true)
    log(`reused pane '${slot}' in column ${existing.viewColumn}`)
    return
  }

  const column = await columnFor(pos, near, log)
  const panel = vscode.window.createWebviewPanel(
    'mcpAppViewer.pane',
    slot === 'main' ? 'MCP App' : `MCP App: ${slot}`,
    { viewColumn: column, preserveFocus: true },
    { enableScripts: true, retainContextWhenHidden: true }
  )
  panel.webview.html = paneHtml(url)
  // Closing a pane by hand must actually forget it, or the next render would "reuse"
  // a disposed panel and throw where the user expected a window.
  panel.onDidDispose(() => {
    if (panels.get(slot) === panel) panels.delete(slot)
    log(`pane '${slot}' closed`)
  })
  panels.set(slot, panel)
  log(`created pane '${slot}' ${pos} -> column ${column}`)
}

function closePanes(slot, log) {
  if (slot === '--all' || slot === 'all') {
    const n = panels.size
    for (const panel of Array.from(panels.values())) panel.dispose()
    panels.clear()
    log(`closed ${n} pane(s)`)
    return
  }
  const panel = panels.get(slot)
  if (!panel) {
    log(`no pane named '${slot}' to close`)
    return
  }
  panel.dispose()
  panels.delete(slot)
}

// ------------------------------------------------------------------ wiring ---
let lastUrl = null
let output = null

// A dedicated channel, because the whole failure mode this extension exists to fix is
// "something reported success and nothing appeared." View: Output -> MCP App Viewer.
function log(message) {
  if (output) output.appendLine(`[${new Date().toISOString().slice(11, 19)}] ${message}`)
}

function activate(context) {
  output = vscode.window.createOutputChannel('MCP App Viewer')
  context.subscriptions.push(
    output,
    vscode.window.registerUriHandler({
      handleUri(uri) {
        // vscode://ccmitchellusa.mcp-app-viewer/open?url=<enc>&position=right&slot=tokyo&near=reykjavik
        // vscode://ccmitchellusa.mcp-app-viewer/close?slot=tokyo   (or slot=--all)
        const params = new URLSearchParams(uri.query)

        if (uri.path === '/close') {
          closePanes(params.get('slot') === '--all' ? '--all' : safeSlot(params.get('slot')), log)
          return
        }

        const url = params.get('url')
        if (!url) {
          vscode.window.showErrorMessage('MCP App Viewer: no url in the request.')
          return
        }
        if (!isLocalViewerUrl(url)) {
          // Naming the reason matters — a silent refusal here looks like a broken
          // extension, and the user would go hunting in the wrong place.
          vscode.window.showErrorMessage(
            'MCP App Viewer refused a non-local URL. Only http on 127.0.0.1 or ' +
              'localhost is allowed, because any web page can trigger a vscode:// link.'
          )
          return
        }
        lastUrl = url
        const slot = safeSlot(params.get('slot'))
        const near = params.get('near') ? safeSlot(params.get('near')) : null
        log(`handling ${url} slot=${slot} position=${params.get('position') || 'right'} near=${near || '-'}`)
        openPane(url, slot, params.get('position'), near, log)
      },
    }),

    vscode.commands.registerCommand('mcpAppViewer.showLast', () => {
      if (!lastUrl) {
        vscode.window.showInformationMessage(
          'MCP App Viewer: no app opened yet this session. Render one with `/mcp-app open`.'
        )
        return
      }
      openPane(lastUrl, 'main', 'right', null, log)
    }),

    vscode.commands.registerCommand('mcpAppViewer.closeAll', () => closePanes('--all', log))
  )
}

function deactivate() {}

module.exports = { activate, deactivate }
