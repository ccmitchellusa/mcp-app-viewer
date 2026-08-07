# MCP App Viewer

Render **MCP Apps** — the HTML UIs that MCP servers ship as `ui://` resources — in a
real browser, from a terminal coding agent that cannot draw them.

Optionally it does this automatically: a PostToolUse hook watches tool results, and
when one carries an MCP App it opens in a browser window, or in a pane beside your
session.

Sibling project to [claude-code-piper-tts](../claude-code-piper-tts); same shape, same
per-agent install pattern.

## Why this exists

The MCP Apps extension (SEP-1865, `ext-apps`) lets a server ship an interactive HTML
view alongside a tool's data. Terminal MCP clients render none of it. That is not
merely a missing convenience — it is a **verification hole**: a server can ship an app
that is blank, unstyled, throwing on load, or missing every asset, and nothing in the
transcript distinguishes it from one that works. You discover it when a user opens it.

This closes the loop. One command puts the app in front of you.

## Architecture

```
tool result ──> PostToolUse hook ──> mcp-app.sh open - ──> mcp_app_server.py
                (positive signal          (state,             (serves /app/index.html,
                 only, never blocks)       last-app)           proxies relative assets)
                                                                      │
                                                            display_targets.py
                                                     system │ chrome │ iterm2 │ vscode
```

Three pieces, each replaceable:

- **`bin/mcp_app_server.py`** — serves one app's HTML on localhost. MCP App HTML often
  references assets by relative path (`/static/...`), so it can proxy those to an
  origin (`--base-url`) or serve them from a local directory (`--assets-dir`).
- **`bin/display_targets.py`** — *where* the app appears. Pluggable, because the right
  answer depends on where you are sitting: a browser window is fine from a plain
  terminal, but inside iTerm2 or VS Code you want it beside the session.
- **`bin/mcp-app.sh`** — the control surface, and what `/mcp-app` calls.

## Install

```bash
git clone <this repo> && cd mcp-app-viewer
./install.sh                 # Claude Code: hook + /mcp-app command
./install-codex.sh           # optional, run install.sh first
./install-kimi.sh
./install-hermes.sh
```

`install.sh` **appends** its hook to `.hooks.PostToolUse` in `~/.claude/settings.json`
rather than replacing the array — clobbering a sibling project's hooks would be a
silent breakage of an unrelated feature. It backs the file up either way.

Requires `jq` and `python3`. No third-party Python packages.

## Usage

Auto-open is **OFF** by default. An agent that opens browser windows uninvited is
worse than one that shows nothing.

```
/mcp-app                    status
/mcp-app on | off           auto-open when a tool result carries an MCP App
/mcp-app open <file.html>   render one now  (or `-` for stdin)
/mcp-app url <address>      point the same target at a LIVE url
/mcp-app last               re-open the most recently captured app
/mcp-app target <name>      system | chrome | safari | firefox | edge | brave | arc
                            | iterm2 | vscode | none
/mcp-app position <where>   right (default) | left | top | bottom
/mcp-app base <url>         origin to proxy relative assets from
/mcp-app assets <dir>       local directory serving relative assets instead
/mcp-app port <n>           viewer port
/mcp-app stop               stop the local server
/mcp-app log                tail the activity log
```

## Display targets

| target | what it does |
|---|---|
| `system` | OS default browser. Always works; every other target falls back to it. |
| `chrome`, `safari`, … | A named browser. **Chrome is worth choosing** — chrome-devtools tooling attaches to it, so an app opened there can be *inspected* programmatically, not just looked at. |
| `iterm2` | Splits the current iTerm2 window and renders in a browser pane beside the session. |
| `vscode` | VS Code's built-in Simple Browser, beside the editor, via the bundled companion extension. Real four-way placement. |
| `terminal` | Splits **any** terminal (Ghostty, tmux, WezTerm, kitty, iTerm2) and renders in a terminal browser. The only target that works headless over SSH. Engine matters — see below. |
| `none` | Serve only, print the URL. Correct for headless or remote agents, where opening a browser either fails or opens it on the wrong machine. |

### iTerm2 setup (one time)

iTerm2 3.6 ships a built-in browser, but panes need **both** the `browserProfiles`
advanced setting *and* a profile to render in — both off by default, and neither
creatable from AppleScript:

1. Settings > Advanced > search `browserProfiles` > turn on
2. Settings > Profiles > `+` > name it **`Browser`**
3. Restart iTerm2

The viewer checks for that profile **before** it splits. An earlier version split
first and echoed the URL into the new shell as a signpost; that was wrong twice over
— it leaves a stray terminal pane that renders nothing, and `write text` races the
shell's own startup, which mangled the command into `llecho`. A pane that cannot show
the app should not be created at all. Until the profile exists, the target falls back
to a browser and says why.

`position` picks the split axis. iTerm2 places a new pane to the right of a vertical
split and below a horizontal one, so `left`/`top` select the same axis and say so
rather than silently doing something else.

### VS Code (and forks): why it needs a companion extension

VS Code exposes Simple Browser only as the `simpleBrowser.show` **command**, and
commands cannot be invoked from the CLI. The obvious guess is a trap, measured on
1.128.1:

- `--open-url` is not in `code --help` at all
- `code` exits **0** for a bogus URL *and* for a completely invented flag — so an
  exit-code check reports success while opening nothing
- worse, `code --open-url http://example.invalid/nonsense` pops up *"The extension
  'example.invalid' cannot be installed because it was not found"* — it parses the
  URL's host as a `publisher.name` extension id, taking an **active wrong action**,
  invisibly, on every render

So `vscode-extension/` is the smallest possible bridge: ~80 lines of plain
dependency-free JavaScript registering a URI handler that the CLI *can* reach, which
calls the command only an extension can call. `install.sh` copies it into
`~/.vscode/extensions/` when `code` is on PATH — no `.vsix`, no `vsce`, no npm.
**Reload VS Code once after install** (`Developer: Reload Window`).

It is also the one target with **real four-way placement**: VS Code has genuine editor
groups, so unlike iTerm2, `left` and `top` mean what they say.

**Security.** A URI handler is a real attack surface — any web page can navigate to a
`vscode://` link, so a handler that opens whatever it is handed would let a hostile
page render arbitrary content inside the editor, wearing the editor's chrome. This one
accepts **http on loopback only**, by parsed hostname against an exact allow-list (a
`startsWith('localhost')` check would pass `localhost.evil.com`), and it says why it
refused rather than failing silently.

**Any VS Code fork works** — VSCodium, Cursor, Windsurf, Bob IDE. Three things differ
per fork and all three fail *silently* if assumed: the CLI binary name, the URI scheme
its handler answers on, and the extensions directory. Firing `vscode://` on a machine
with two editors installed opens a pane in the **wrong editor** rather than erroring.

So they are discovered, not guessed. Every fork ships a `product.json` carrying exactly
`applicationName`, `urlProtocol`, and `dataFolderName`, and it is authoritative for that
build. `install.sh` and the viewer both read it through one implementation:

```bash
python3 bin/display_targets.py --editor-profile
{ "cli": "code", "url_protocol": "vscode", "data_folder": ".vscode", ... }
```

A fork nobody has heard of works with no code change. Override any part with
`MCP_APP_EDITOR_CLI`, `MCP_APP_EDITOR_URI_SCHEME`, `MCP_APP_EDITOR_DATA_FOLDER`, or
`MCP_APP_EDITOR_EXT_DIR` if a build's product.json is absent or wrong.

One thing that is *not* discoverable: whether a fork still ships Simple Browser. It is a
built-in (`vscode.simple-browser`) and most forks keep built-ins, but a stripped build
has no pane at any position. The extension's output channel (`View -> Output -> MCP App
Viewer`) says which command path ran, which is how that gets diagnosed rather than
guessed at.

**The Claude Code and Codex VS Code extensions change nothing here.** They run the same
CLI and the same hooks, so the hook fires normally inside VS Code; only the display
surface differs. Note also that the old "Debugger for Chrome" extension is deprecated in
favour of `ms-vscode.js-debug` — irrelevant to this project, which uses the built-in
Simple Browser and depends on neither.

### Terminal browsers: the engine is the whole point

Most terminals split *terminals*, not web views — iTerm2's browser pane is the
exception, not the rule. But a pane that runs a program can run a browser, which
solves "no web view" for every terminal at once, and is the only thing that works on
a headless box where a window cannot open and a printed URL is useless.

**Which browser decides whether this helps or harms.** MCP Apps render through web
components — custom elements whose content JavaScript builds into shadow DOM:

| browser | engine | what you get |
|---|---|---|
| `carbonyl` | Chromium | real rendering; closest to the truth |
| `browsh` | headless Firefox | real rendering; needs Firefox |
| `cha` (Chawan) | partial JS | better than text, not authoritative |
| `lynx`, `w3m`, `links`, `elinks` | **none** | an **empty page** — for a working app |

A text browser showing nothing for a healthy app is a **false negative**, and acting on
it means debugging a bug that does not exist. That is the confusion this project
exists to remove, so text browsers are **refused by default** and say why.

They are still useful for the *opposite* question. A text-only render is exactly what a
non-visual client receives from the MCP Apps text fallback, so `--fallback-preview`
enables them deliberately — for previewing the fallback, never for judging the app.

```
/mcp-app target terminal
/mcp-app theme              # auto (OS) | light | dark
/mcp-app browser            # list detected, with engine class
/mcp-app browser carbonyl   # pin one
/mcp-app browser auto       # best engine available (default)
```

**Light/dark follows your OS.** Real browsers read `prefers-color-scheme` themselves, so
every other target honours your system setting for free. Carbonyl does not — it is
headless Chromium, which reports **light** no matter what the desktop says (measured:
on a Dark system, `prefers-color-scheme: dark` evaluated false). Since the Longleaf
palette is driven by that media query, the whole app rendered in the wrong theme inside
a dark terminal.

So the OS preference is detected and passed through. The flag was found by testing, not
assumption:

| flag | `prefers-color-scheme: dark` |
|---|---|
| *(none)* | false — even on a Dark system |
| `--blink-settings=preferredColorScheme=0` | **true** |
| `--blink-settings=preferredColorScheme=1` | false |
| `--force-dark-mode` | false — Chrome's auto-darkening, a different feature |

Override with `/mcp-app theme light|dark` to check the other one deliberately.

**How the pixels happen:** these do not use sixel or the kitty graphics protocol. They
use 24-bit truecolor ANSI plus the Unicode half-block `▀` (U+2580) — foreground colour
paints the top half of a cell, background the bottom, so each cell carries two vertical
pixels. Carbonyl additionally hooks Chromium's compositor so *text draws as real text*
rather than as coloured blocks, which is why it stays crisp and selectable.

The honest consequence is resolution: a 100x50 pane is roughly 100x100 effective
pixels. Ample for *is it blank, is the content there, is the layout roughly right* —
useless for judging typography or spacing. For that, use `chrome`.

## Assets

MCP App HTML is frequently *not* self-contained: it pulls components and styles from
the server that shipped it. Two ways to resolve those:

- `/mcp-app base https://your-mcp-host` — proxy them.
- `/mcp-app assets /path/to/static` — serve them from disk.

Prefer `assets` when the origin needs auth. A proxied fetch against an authenticated
host returns **403**, and the viewer reports that status rather than serving an empty
file — an app missing its stylesheet should not be indistinguishable from an app whose
stylesheet is broken.

## Several agents, one machine

Each per-agent installer writes a profile (`codex.sh`, `kimi.sh`, `hermes-cfg.sh`) that
inherits the shared config and overrides the port. The hook exports
`MCP_APP_PROFILE`, which also gives each agent its own state file — its own auto-open
toggle, its own `last` app, its own server pid. Without that, two agents auto-opening
would each kill the other's viewer and you would be looking at whichever won the race.

Hermes has no PostToolUse hook contract, so `install-hermes.sh` does not pretend
otherwise: it installs a skill plus a shell profile, and the agent calls the viewer
directly. Its default target is `none`, since hermes usually runs somewhere that is
not your desktop.

## Known gap: one surface per render (browser and iTerm2 targets)

For `system`, a named browser, and `iterm2`, every render opens a **new** tab or pane;
it does not reuse the one already showing. By hand that is a minor annoyance. With
auto-open on it is a pane fountain — one per tool result carrying an app.

The naive fix (skip the open when a viewer is already running) trades it for a worse
bug: the existing surface keeps showing the PREVIOUS app, because the HTML behind the
URL changed and nothing told the page to reload. Looking at a stale app while believing
it is the current one is exactly the failure this project exists to prevent. A real fix
needs a version token the served shell polls, plus a per-profile "surface is open"
marker — and even then the process cannot see whether you closed it, only infer it.

Designed but not built: [`docs/pane-reuse-design.md`](docs/pane-reuse-design.md).

**`vscode` does not have this gap.** The companion extension remembers the editor group
it created and reuses it, and Simple Browser re-navigates on each render — so the second
app replaces the first *in place*. Verified: two consecutive renders updated one pane
rather than opening two. That makes `vscode` the best-behaved target today, which is a
fair reason to prefer it over `iterm2` if you work in an editor.

## What the hook will and will not do

It fires on **every** tool call, so it is deliberately cheap and deliberately timid:

- exits immediately unless auto-open is on
- requires a positive MCP-App signal (`profile=mcp-app` media type, or a `ui://` URI)
  — never "this looks like HTML", which would fire on any tool returning a web page
- scans only the tail of the transcript, so cost does not grow with session length
- always exits 0, so a viewer problem cannot wedge your session

## Credits

Structure and per-agent install pattern follow `claude-code-piper-tts`.
MCP Apps: [SEP-1865](https://github.com/modelcontextprotocol/modelcontextprotocol),
`ext-apps` extension.
