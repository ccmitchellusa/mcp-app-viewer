// MCP App Viewer — VS Code companion.
//
// VS Code exposes Simple Browser only as a COMMAND (`simpleBrowser.show` /
// `simpleBrowser.api.open`), and commands cannot be invoked from the CLI. That is the
// entire reason this extension exists: it is the smallest possible bridge from a URI
// the CLI *can* trigger to a command only an extension can call.
//
// Deliberately dependency-free plain JavaScript — no TypeScript, no bundler, no
// node_modules. The parent project advertises zero third-party dependencies and this
// should not be the thing that breaks that promise for ~80 lines.

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

// Remember the column we made. Creating a group per render would fan out into a
// column farm after a few tool calls — the same one-surface-per-render bug the
// parent project documents for panes.
let appColumn = null

function columnStillOpen(column) {
  if (column === null) return false
  return vscode.window.tabGroups.all.some((g) => g.viewColumn === column)
}

async function openInSimpleBrowser(url, position, log) {
  const pos = (position || 'right').toLowerCase()

  if (!columnStillOpen(appColumn)) {
    const groupCommand = GROUP_COMMAND[pos] || GROUP_COMMAND.right
    await vscode.commands.executeCommand(groupCommand)
    appColumn = vscode.window.tabGroups.activeTabGroup.viewColumn
    log(`created editor group ${pos} -> column ${appColumn}`)
  } else {
    log(`reusing column ${appColumn}`)
  }

  // `simpleBrowser.api.open` takes placement options; `simpleBrowser.show` does not.
  // Prefer the former; the fallback still renders the app but lands wherever focus is,
  // so log which path ran — "it opened in the wrong place" and "the api command is
  // missing" look identical from the outside otherwise.
  try {
    await vscode.commands.executeCommand('simpleBrowser.api.open', vscode.Uri.parse(url), {
      viewColumn: appColumn,
      preserveFocus: true,
    })
    log('opened via simpleBrowser.api.open')
  } catch (err) {
    log(`simpleBrowser.api.open failed (${err && err.message}); falling back to simpleBrowser.show`)
    await vscode.commands.executeCommand('simpleBrowser.show', url)
  }
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
        // vscode://ccmitchellusa.mcp-app-viewer/open?url=<encoded>&position=right
        const params = new URLSearchParams(uri.query)
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
        log(`handling ${url} position=${params.get('position') || 'right'}`)
        openInSimpleBrowser(url, params.get('position'), log)
      },
    }),

    vscode.commands.registerCommand('mcpAppViewer.showLast', () => {
      if (!lastUrl) {
        vscode.window.showInformationMessage(
          'MCP App Viewer: no app opened yet this session. Render one with `/mcp-app open`.'
        )
        return
      }
      openInSimpleBrowser(lastUrl, 'right', log)
    })
  )
}

function deactivate() {}

module.exports = { activate, deactivate }
