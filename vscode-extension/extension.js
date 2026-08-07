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
const GROUP_COMMAND = {
  left: 'workbench.action.newGroupLeft',
  top: 'workbench.action.newGroupAbove',
  bottom: 'workbench.action.newGroupBelow',
}

async function openInSimpleBrowser(url, position) {
  const pos = (position || 'right').toLowerCase()
  let viewColumn = vscode.ViewColumn.Beside // 'right' — the default

  const groupCommand = GROUP_COMMAND[pos]
  if (groupCommand) {
    // Create the group on the requested side, then target the now-active one.
    await vscode.commands.executeCommand(groupCommand)
    viewColumn = vscode.ViewColumn.Active
  }

  // `simpleBrowser.api.open` takes placement options; `simpleBrowser.show` does not.
  // Prefer the former, fall back so a VS Code without the api command still works
  // rather than failing to render at all (position is the part we lose, not the app).
  try {
    await vscode.commands.executeCommand('simpleBrowser.api.open', vscode.Uri.parse(url), {
      viewColumn,
      preserveFocus: true,
    })
  } catch {
    await vscode.commands.executeCommand('simpleBrowser.show', url)
  }
}

// ------------------------------------------------------------------ wiring ---
let lastUrl = null

function activate(context) {
  context.subscriptions.push(
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
        openInSimpleBrowser(url, params.get('position'))
      },
    }),

    vscode.commands.registerCommand('mcpAppViewer.showLast', () => {
      if (!lastUrl) {
        vscode.window.showInformationMessage(
          'MCP App Viewer: no app opened yet this session. Render one with `/mcp-app open`.'
        )
        return
      }
      openInSimpleBrowser(lastUrl, 'right')
    })
  )
}

function deactivate() {}

module.exports = { activate, deactivate }
