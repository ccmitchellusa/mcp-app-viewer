# Verification matrix — what is measured, what is assumed

This project exists because a broken MCP App and an unrendered one look identical.
That failure mode applies to the viewer itself, and has bitten repeatedly: a display
target reported success while opening nothing, a browser passed feature detection and
rendered zero bytes, the CLI printed a 34-minute-old status line as if it were this
run's. So the useful question about any path here is not "is it implemented" but
**"has anyone watched it work, and how."**

This file answers that for every path, in one place. Every "verified" row names what
was measured and against which build. Everything else is explicitly *not* verified —
that is the point of the file, and rows must not be promoted without a measurement.

**Rule for editing this file:** an exit code is not a measurement. Neither is reading
the code. A row moves to VERIFIED when someone saw the app rendered, or read the
terminal's actual bytes, and wrote down what they saw.

## Environment the measurements were taken on

macOS 26.6 (25G5065a) · Terminal.app 2.15 (470.2) · Ghostty 1.3.1 · iTerm2 3.6.11 ·
tmux 3.7b · VS Code 1.132.0 (df53daa, x64) ·
carbonyl 0.0.2 · node v24.11.0 · Python 3.14.2

Not installed on this machine, so untestable here: **WezTerm**, **kitty**, **browsh**,
every text browser — and **every VS Code fork**. `code` is the only editor CLI present;
`codium`, `cursor`, `windsurf` and `bob` are all absent from `PATH`.

Not installed on this machine, so untestable here: **WezTerm**, **kitty**, **browsh**,
and every text browser (`w3m`, `lynx`, `links2`, `links`, `elinks`). Chawan was
measured at 0.4.4 but is no longer installed.

---

## Display targets

| target | status | evidence |
|---|---|---|
| `system` | **VERIFIED** | Opens the OS default browser. Exercised repeatedly, most recently as the hook's non-tty fallback: reported `displayed on system (fallback)` and one Safari tab appeared on `127.0.0.1:8777`. |
| `none` | **VERIFIED** | Serves and reports `displayed on none`; no browser process appeared, no `--no-fallback` on argv. |
| `terminal` | **VERIFIED** on both routes | Split route and inline route each confirmed visually. See the two tables below — this target's status is really five statuses. |
| `vscode` | **VERIFIED** on VS Code 1.132.0 (2026-08-07) for the OWN-WEBVIEW route | The extension no longer delegates to Simple Browser (a singleton reusing one `_activeView`); it creates a `createWebviewPanel` per slot. That route has now been executed and watched. One render put a tab titled **`MCP App`** — not `Simple Browser` — into a new editor group to the right, and the app inside it drew a live Leaflet map reading **`Raleigh, NC — 35.77960, -78.63820`**. Reading the payload's own label off the screen is what makes this a measurement and not a pane sighting: the empty state says `Waiting for a location…`, so the label can only appear if the data arrived. Fork discovery via `product.json` still **has never run against an actual fork** — see the fork row below. |
| `vscode` — the iframe CSP/sandbox (the riskiest claim) | **VERIFIED** | Both halves held under `default-src 'none'; frame-src http://127.0.0.1:* http://localhost:*` with `sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`. `frame-src` admitted the iframe: the page loaded and began polling, and `mcp-app.sh panes` flipped the slot to `live` (liveness *is* the heartbeat the page writes by polling `/version`, so `live` cannot be produced by a blank pane). `allow-same-origin` carried the postMessage handoff: the host shell reached `frame.contentWindow`, delivered the tool result, and the app rendered it. No CSP errors needed hunting in the webview devtools — the rendered label settled it. Leaflet's CDN stylesheet and the OSM tiles also loaded, confirming the outer webview CSP does not constrain the framed document, which is served from the loopback origin with its own headers. |
| `chrome`, `safari`, `firefox`, `edge`, `brave`, `arc` | **UNVERIFIED individually** | `_open_named` shells out to `open -a <app>`. Only the `system` default (Safari) has actually been watched to open. The others are the same three lines of code, but nobody has run them. |
| `iterm2` | **VERIFIED** on 3.6.11 (2026-08-07) | Renders in a real browser pane (`profile=MCP App Viewer`, `tty=missing value` — a web view, not a shell). The previous verdict here was wrong in its *reason*: the host had both prerequisites all along, and the target failed because the code called AppleScript that does not exist. Each candidate was measured by pointing it at a logging HTTP server and counting requests: `set URL of session` → `-10003`, the `session` class has no URL property; `split … command "<url>"` → **0 requests**, `command` is a SHELL command, so iTerm2 ran the URL as one, it failed, and `Close Sessions On End` shut the pane before the error could be read (this was the "red error that closed too fast"); `open -a iTerm <url>` → **0 requests**. A **dynamic profile carrying `Initial URL`** → **1 request**. That is the mechanism now used; it loads live, needs no restart, and no longer requires a hand-made `Browser` profile — only the `browserProfiles` advanced setting. |

## Named panes (slots)

Several apps on screen at once, each with its own server, port and identity.

| claim | status | evidence |
|---|---|---|
| Three simultaneous panes, each its own app | **VERIFIED** (iTerm2, 2026-08-07) | `tokyo` 8778 → "Tokyo", `reykjavik` 8779 → "Reykjavik", `nyc` 8780 → "New York City"; all three `live` in `mcp-app.sh panes`, each URL serving its own payload. |
| Placement relative to a named pane (`--near X --position below`) | **VERIFIED** (iTerm2) | `split` returns the new session and `id of p` yields a stable UUID; selecting a session by that id and splitting from it both confirmed. Tokyo placed below Reykjavik, NYC below Tokyo. |
| Panes land beside the AGENT, not the frontmost window | **VERIFIED by failure, then fixed** | Reported from use: after switching to another full-screen iTerm2 window, the next render opened there. Unanchored splits target `current session of current window`. New panes now anchor to `ITERM_SESSION_ID`. |
| Pane REUSE instead of a new pane per render | **VERIFIED** | Two consecutive automatic renders held the pane count at 2 and reported `reused the open pane`. An already-open page followed a render with no navigation: `Raleigh, NC` → `Reykjavik` at the same URL (headless Chromium). Liveness is the heartbeat's age, so a closed pane self-corrects. |
| Named panes in VS Code | **VERIFIED** (VS Code 1.132.0, 2026-08-07) | Four webview panes on screen at once, each its own server, port and app: `main` 8777 → "Raleigh, NC", `tokyo` 8778 → "Tokyo", `reykjavik` 8779 → "Reykjavik", `nyc` 8780 → "New York City". All four `live` in `mcp-app.sh panes`, all four maps drawn simultaneously with their own labels, tabs titled `MCP App`, `MCP App: tokyo`, `MCP App: reykjavik`, `MCP App: nyc`. Four distinct labels on screen together is the direct refutation of the singleton collapse this route was written to fix. Confirmed independently by the maintainer watching it happen ("four different panes opened"). |
| Placement in VS Code (`--near X --position below`) | **VERIFIED** | `tokyo` landed right of `main`; `reykjavik` below `tokyo`; `nyc` below `reykjavik` — the requested geometry, read off a screenshot. The anchoring is the extension's own: `columnFor` reveals the named pane first, so `newGroupBelow` splits *that* group rather than wherever focus had drifted. Note this worked **despite** `mcp-app.sh` printing `no pane named 'tokyo' to place this beside` on every `--near` — see the defect below. |
| Pane REUSE in VS Code | **VERIFIED** | Re-rendering slot `tokyo` with a different payload left the pane count at four and the tab still titled `MCP App: tokyo`, with its map now showing **Paris**. Updated in place; no fifth pane. |
| `close --all` in VS Code | **VERIFIED** | Reported `closed 4 pane(s)`; all four editor groups disappeared from the window (screenshot), and the listening viewer ports went from four to **zero**, so the servers were reaped and not merely orphaned behind a closed pane. |

## Terminal hosts — the `terminal` target's split route

`detect_host()` picks the host; each has its own splitter. tmux wins over the outer
terminal deliberately, since inside tmux the visible panes are tmux's.

| host | status | evidence |
|---|---|---|
| **tmux** | **VERIFIED — all four positions** (2026-08-07) | First execution of `_split_tmux` in the project's life. Pane geometry read back from `list-panes` and each position confirmed by screenshot of carbonyl rendering: right `left=61`, left `left=0` (shell pushed to 61), top `top=0` (shell at 15), bottom `top=15`. `-b` is honoured on 3.7b, so `left`/`top` genuinely place the pane *before* the current one. |
| **Ghostty** | **VERIFIED** on 1.3.1 | First execution of `_split_ghostty` found three defects, each measured not inferred (62a5793): `focused terminal` is a property of a tab and the bare specifier raised `-1728`; splitting the *focused* pane made `position` drift, so right/bottom landed beside the shell but then left landed beside the bottom **browser** pane; `wait after command` is needed to hold a pane whose command never launches. Anchoring to a pane that reports a working directory fixes the drift. |
| **iTerm2** | **VERIFIED** | `_split_iterm2` uses the *default* profile running a command, so unlike the `iterm2` target it needs no browser profile. Confirmed visually by the maintainer across several renders in one session, not from the tool's own success message: a real MCP App (`ui://longleaf/cast-builder`) rendered and its text was read aloud back ("barely readable — white background with light yellow text", which became bug #1885); the live production app rendered, was logged into, and was used interactively ("just like in a browser, just lower resolution"); and the dark-theme flag was confirmed by eye ("It's rendering dark!"). Reading specific on-screen text is stronger evidence than a pane appearing — it cannot be produced by a blank or stale pane. |
| **GNU screen** | **VERIFIED BROKEN** on 4.00.03 | Not detected at all, and the render is unusable. See below — this is the reason for the Terminal.app recommendation. |
| **WezTerm** | **UNVERIFIED** | Not installed. `_split_wezterm` has never executed. |
| **kitty** | **UNVERIFIED** | Not installed. `_split_kitty` has never executed. It additionally needs `allow_remote_control yes`, which nobody has exercised. |

### GNU screen mangles the render, and is not detected — measured

macOS ships **screen 4.00.03 (2006)**. Running the `terminal` target inside it produced
horizontal white stripes across the whole window with raw mouse-report escapes leaking
into the buffer — not the app, and not recognisably anything. Screenshot taken; there is
no ambiguity about it.

Root cause, measured rather than guessed:

| | outer Terminal.app | inside screen |
|---|---|---|
| `TERM` | `xterm-256color` | `screen` |
| `COLORTERM` | `truecolor` | `truecolor` ← **inherited straight through** |
| `STY` | unset | `40402.diag` |
| `detect_host()` | `""` | `""` ← **screen is not detected** |

`COLORTERM` is an ordinary environment variable, so it passes into screen untouched.
carbonyl therefore believes it has 24-bit colour and emits it, while screen 4.00.03's
own `TERM` cannot carry it — that version predates truecolor support entirely. The
result is a mangled frame that looks like a broken app, which is the exact false
negative this project exists to eliminate.

Compounding it: `detect_host()` checks `TMUX` but never `STY`, so screen is invisible to
it. With no host detected the tool takes the **inline** route and draws directly into the
multiplexer that cannot render it. Worse, `GHOSTTY_RESOURCES_DIR` and friends are also
inherited through screen, so under Ghostty it would try to split the *outer* terminal and
put the browser in a pane behind the session.

**Not fixed.** The obvious fix is small — detect `$STY`, refuse with a message naming the
reason — but it was descoped in favour of the recommendation below. Anyone picking it up
should note that screen 5.x does support truecolor and was **not** tested, so a blanket
refusal would be conservative rather than precise.

## The `terminal` target's inline route (plain terminals)

Used when no splitter exists — Terminal.app, xterm, a serial console, SSH with no
multiplexer. Fully documented in [terminal-app-verification.md](terminal-app-verification.md).

| aspect | status | evidence |
|---|---|---|
| renders at all | **VERIFIED** in Terminal.app 2.15 | carbonyl takes over the window and draws the app in the requested colours. |
| truecolor | **VERIFIED — 24-bit** | 24 of 24 distinct red steps, 6 of 6 greens 2/255 apart, half-block correct. `COLORTERM=truecolor` is set by Terminal.app itself, confirmed absent from every shell rc. The "Terminal.app is 256-colour" premise is obsolete. |
| reports the right target | **VERIFIED** | Was reporting a stale log line. Fixed, and confirmed across successive runs sharing one append-only log that each printed their own verdict — including four consecutive tmux runs reporting four different positions, which the pre-fix scan could not have done. |
| only one browser opens | **VERIFIED** | Safari held at 1 window / 10 tabs / **0** on `127.0.0.1` while carbonyl drew the app. |
| shell usable afterwards | **VERIFIED** | After SIGINT plus ~50 cursor warps across the window, the prompt is clean. Exit status 130 preserved. |
| hook never inlines | **VERIFIED** | `MCP_APP_NO_INLINE=1` is on the `bash` invocation, plus two independent guards (`[ -t 1 ]`, stdout redirected to the log). Confirmed empirically from a non-tty shell. |

## Terminal browsers (engines)

The engine is the whole point: MCP Apps build their content in JavaScript, so a
browser without a working custom-element lifecycle shows an empty page for a healthy
app — a false negative, which is worse than showing nothing.

| browser | engine | status | evidence |
|---|---|---|---|
| **carbonyl** | `real` | **VERIFIED** at 0.0.2 | Every rendering measurement in this file. Also needs two non-obvious fixes to launch at all: absolute path (a fresh pane has not sourced nvm) and its *interpreter's* directory on `PATH` (carbonyl is a bash script that calls `node`). |
| **cha** (Chawan) | `stub` | **VERIFIED BROKEN** at 0.4.4 | Ships QuickJS, reports `customElements=YES` and `attachShadow=YES`, and `customElements.define()` throws nothing — but `connectedCallback` **never fires**. A real MCP App produced **zero bytes**. Refused with no opt-in: diagnostically worse than lynx, because it looks capable. Binary no longer installed. |
| **browsh** | `real` (claimed) | **UNVERIFIED** | Not installed, never run. Classified `real` on the basis that it drives a headless Firefox. That is an inference, not a measurement. |
| **w3m, lynx, links2, links, elinks** | `text` | **UNVERIFIED** | None installed. The refusal logic (and the `--fallback-preview` opt-in that bypasses it) has never run against a real binary. |

## Theme

| aspect | status | evidence |
|---|---|---|
| carbonyl follows the OS setting | **VERIFIED** | Carbonyl is headless Chromium and reports **light** regardless of the desktop; measured on a Dark system, the media query evaluated false. The flag was found by probing, not assumed: `preferredColorScheme=0` → dark, `=1` → light, `--force-dark-mode` → light (it is Chrome's auto-darkening of light pages, a different feature that does not set the media query). |
| every other target | **by construction** | Real browsers read `prefers-color-scheme` themselves. Nothing to verify. |

---

## Terminal.app: use it two ways

Terminal.app has **no scriptable splitter** of its own, so there is no direct pane route.
That leaves two supported setups, both verified:

**1. External system browser** — the recommended default.

```
mcp-app.sh target system
```

**2. tmux + carbonyl** — if you want the app beside your work, in the terminal.

```
tmux                          # then, inside it:
mcp-app.sh target terminal
mcp-app.sh position right     # or left / top / bottom, all verified
```

tmux supplies the splitter Terminal.app lacks, and carbonyl renders in the new pane.
Verified in all four positions, geometry read back from `list-panes` and each confirmed
by screenshot.

### What not to do, and the third route

- **Do not run GNU screen.** It is the obvious alternative to tmux and it does not work:
  the frame comes out as stripes, and the tool does not even detect screen, so it fails
  into the inline route silently. Measured above.
- **The inline route works but is not the desktop answer.** With no multiplexer,
  `target terminal` renders carbonyl in the window you are working in and holds it until
  you quit. Verified working — truecolor, clean exit, shell restored — and it is the
  **only** thing that works over SSH on a headless box, where opening a window is
  impossible rather than merely inconvenient. Right tool there; wrong default here,
  especially with auto-open on, where it would seize your terminal on a tool result.

The recommendation is a maintainer decision rather than a measurement — it deliberately
steers away from a path that does work — so it is recorded here rather than left
implicit. It is also worth noting the inline route needed three defect fixes before it
was safe at all: it reported a stale display target, opened a second browser alongside
itself, and handed back a shell stuck in mouse-reporting mode.

## Working configurations

Combinations watched end to end. Anything not listed may still work; nobody has looked.

1. **Terminal.app** — `target system`. **The recommended setup**; see above.
2. **Terminal.app, no multiplexer** — `target terminal`, carbonyl, inline. Verified
   working: takes over the window, Ctrl-C returns a clean shell and exit 130, truecolor
   confirmed 24-bit. Supported, and correct over SSH, but not the recommended desktop
   default.
3. **tmux in Terminal.app** — `target terminal`, carbonyl, any of the four positions.
   The split path; the shell returns to its prompt immediately. The right choice if you
   want the app beside your work in Terminal.app.
4. **Ghostty** — `target terminal`, carbonyl, split pane, all four directions.
5. **iTerm2** — `target iterm2`, browser pane beside the session (needs the one-time
   `browserProfiles` setup).
6. **VS Code 1.132.0 running the companion extension** — `target vscode`, several named
   webview panes at once, placement relative to a named pane, reuse across renders, and
   `close --all`. Verified at 1.132.0 on 2026-08-07.

   **On the reload `install.sh` tells you to do:** this run never reloaded the window,
   and the new extension still took effect. That is not a reason to skip it. The
   extension declares `activationEvents: ["onUri"]`, so it is `require`d from disk at
   the *first* URI — if it has not been activated yet in that window, the first render
   after an install picks up the new file for free. If it *has* already been activated,
   the old module stays resident and a reload is genuinely required, with no symptom
   beyond behaving like the previous version. Reload unless you know the window has
   never rendered an app. Note also that the URI goes to the **last active window**, not
   a window of your choosing: with several windows open, the panes appear wherever focus
   last was.
7. **Anywhere, any OS** — `target system`. The universal fallback, and what every
   other target degrades to.
8. **Headless / remote agent** — `target none`, or `target terminal` over SSH. `none`
   is correct when opening a browser would either fail or open it on the wrong machine.

**Do not use:** GNU screen. Not detected, and the render is unusable — see above.

## Known limitations

### Measured — these are real and understood

- **`iterm2` target needs one manual setting** and cannot self-configure it: the
  `browserProfiles` advanced setting is off by default and not settable from
  AppleScript. Until it is on the target falls back and says why. The second
  prerequisite this bullet used to name — a hand-made `Browser` profile — no longer
  exists; the tool ships its own dynamic profile. (Corrected 2026-08-07: the bullet had
  outlived the change recorded in the `iterm2` row above.)
- **`left`/`top` on iTerm2 select the axis, not the side.** iTerm2 places new panes
  right/below. The tool says so rather than silently doing something else.
- **`_keep_open` does not survive Ctrl-C.** A pane closes outright on SIGINT, because
  the wrapper dies at the semicolon before printing. It *does* hold the pane for
  non-signal failures — `/usr/bin/false` leaves
  `[mcp-app-viewer] browser exited (1). Press Enter to close this pane.` Left as is: a
  pane is disposable and dismiss-on-Ctrl-C is the behaviour you want there. Only the
  inline path, which hands your own shell back, needed the signal trapped.
- **One surface per render, for the browser targets.** Each render creates a new tab
  rather than reusing one. Does *not* apply to `vscode` or `iterm2`, both of which are
  verified reusing a pane — see [pane-reuse-design.md](pane-reuse-design.md).
- **The inline path ignores the configured browser and theme.** `--inline-command` is
  invoked with the URL only, so `inline_command` re-derives both from defaults instead
  of the saved state. They agree today only because `browser` is unset and `theme` is
  `auto`; set `browser lynx` or `theme light` and `status` starts lying. Unfixed.
- **`inline_command` refuses only the `text` engine, not `stub`.** `open_in_split` got
  a `stub` guard when Chawan was reclassified; the inline path did not. `browser cha`
  would be launched inline and render an empty page. Unfixed.
- **GNU screen is undetected and unusable.** `detect_host()` reads `TMUX` but never
  `STY`, so the tool inlines into a multiplexer that mangles the frame. Unfixed by
  decision; use tmux instead. Full measurement above.
- **`--near` always warns on the `vscode` target, and the warning is false.**
  Measured: every `open --slot X --near Y` printed `no pane named 'Y' to place this
  beside` on stderr while placing the pane correctly anyway. `mcp-app.sh` resolves the
  anchor by reading `$SLOTS_DIR/$NEAR/session`, and that file is only ever written by
  the **iTerm2** path, which scrapes a `mcp-app-viewer: pane session <id>` line out of
  the log. The vscode path never emits one, so the lookup always misses. Placement
  survives because `--pane-near` is forwarded to the extension independently of the
  anchor, and the extension resolves the name against its own `panels` map. So the
  behaviour is right and the message is wrong — which is the failure mode this project
  exists to eliminate, pointed the other way: an alarming message about a working path
  teaches you to ignore messages. Unfixed.
- **`mcp-app.sh panes` can never report an empty list.** Measured: after `close --all`
  removed every slot directory, `panes` still printed `main port 8777 closed?`. The
  listing itself recreates it — `_use_slot` runs at script startup and `mkdir -p`s the
  focused slot before any subcommand dispatches. Deleting the whole slots directory and
  running `panes` recreates `slots/main` from nothing. The `closed?` marker is the only
  thing distinguishing a resurrected ghost from a real pane, and the "no panes yet"
  branch is unreachable once a focus has ever been set. Cosmetic, but it means the
  listing overstates what exists. Unfixed.
- **Slot names are not namespaced per agent profile, so profiles fight over panes.**
  Not exercised, but structural and worth stating: profiles namespace their *state*
  (`slots.codex`, `state.codex`) and their *base port* (Codex ships 8778 vs 8777), yet
  the extension keys its `panels` map on the bare slot name. Claude Code's `main` and
  Codex's `main` are therefore the same webview panel, and whichever renders second
  silently repoints the other's pane at its own port. Codex's base port 8778 is also
  exactly the first port Claude Code hands to a *named* slot; `_alloc_port` scans only
  its own profile's slots directory, and the only thing preventing the clash is the
  bind test, which cannot see a server that is momentarily down.

### Untested paths — no evidence either way

- **Every VS Code fork**, including **Bob IDE**, VSCodium, Cursor and Windsurf. None is
  installed here. The extension's own API surface should port — `createWebviewPanel`,
  `window.tabGroups` and `workbench.action.newGroup*` are all stable VS Code API well
  below the declared `^1.74` engine — so the likely failure is *discovery*, not
  rendering, and it has three specific edges nobody has run. (1) `editor_profile()`
  takes the **first** CLI found in `("code", "codium", "cursor", "windsurf", "bob",
  "code-insiders")`, so on any machine that also has `code` — this one — a fork is never
  chosen unless `MCP_APP_EDITOR_CLI` says so. (2) `install.sh` copies the extension into
  exactly one editor's extensions directory, the discovered one; a fork needs its own
  install run with that override. (3) The URI scheme is read from the fork's
  `product.json` `urlProtocol` and falls back to `vscode` when absent — which would fire
  the URI at VS Code instead of the fork, and `--open-url` would still exit 0, so
  nothing would say so.
- **The VS Code Codex/ChatGPT extension (`openai.chatgpt`), which is installed here, was
  not exercised.** Worth separating from the Codex *CLI*: `install-codex.sh` wires
  `~/.codex/hooks`, which is a Codex CLI mechanism, so the in-editor extension gets no
  auto-open hook and no `/mcp-app` prompt from it. The display path itself is
  agent-agnostic — it is `code --open-url` into our extension, and nothing in it knows
  which assistant called — so a Codex CLI running in a VS Code terminal should behave
  exactly as measured above, under `MCP_APP_PROFILE=codex`. Unmeasured either way, and
  see the slot-namespacing limitation above before running two agents at once.
- WezTerm and kitty splitters (neither installed).
- browsh, and all five text browsers, including the `--fallback-preview` opt-in.
- Named browser targets other than the system default.
- The `url` subcommand on the inline route: it shares the target/position/theme
  settings but has no inline path of its own, so on a plain terminal it opens a
  windowing browser. Not a bug that has been triaged, just untested territory.
- Any non-macOS platform. Every splitter but tmux is macOS-only in practice, and the
  Linux paths (`gsettings` theme detection, `webbrowser` targets) have not been run.

### Assumptions still carried in the code

These are written as fact in comments but have never been measured. They are the most
likely places for the next false pass.

- **Older tmux ignores `-b`**, landing right/below — "the same axis, which is the
  honest degradation." Verified only that 3.7b *honours* it. No old tmux was tested.
- **browsh has a real engine.** Inferred from "it drives headless Firefox".
- **Text browsers show the text fallback** rather than something worse. Chawan was
  assumed to be `partial` on similar reasoning and turned out to render nothing.

## How to verify — the method that works here

The agent shell has **no controlling tty** (`/dev/tty` is "device not configured"), so
printing escape sequences to your own stdout proves nothing at all. Drive a real
window instead:

- **`code --open-url` exit codes are worthless as evidence.** Measured on 1.132.0: it
  exits **0** for a dead port, for `vscode://no.such.extension.at.all/...`, for a string
  that is not a URI at all, and for a URL the extension itself refuses. The CLI hands
  the URI to the running instance and returns; nothing downstream reports back. Every
  claim about this path has to come from the pane, the heartbeat, or the log.
- **Screen Recording permission is per-app, and an agent inherits its host's.** From a
  shell under the VS Code extension host, `screencapture` fails with `could not create
  image from display` even with the display awake (`IODisplayWrangler … USEABLE`) —
  VS Code holds no Screen Recording grant. Terminal.app does, so route the capture
  through it and the same command succeeds:
  `osascript -e 'tell application "Terminal" to do script "screencapture -x /path.png; exit"'`.
  Do not read the failure as a sleeping display or a broken screenshot tool.
- **`System Events` is still blocked** — it does not error, it *hangs* and eventually
  returns `AppleEvent timed out (-1712)` after ~2 minutes. Budget for the stall.

- `osascript -e 'tell application "Terminal" to do script "bash /path/script.sh"'`
- Raise by window id, then `screencapture -T <n> -x full.png`. `screencapture -R <rect>`
  **fails** ("could not create image from rect") — capture full screen and crop with
  PIL. The screen is 2× Retina (3584×2240 for 1792×1120 pt), so
  `bounds of window id N` × 2 crops exactly to the window.
- `get contents of tab 1 of window id N` reads the screen as **text**, which beats
  pixels for anything involving escape residue. This is what caught a "fix" that had
  changed nothing.
- System Events keystroke is **blocked** (no accessibility permission). Send signals to
  the process group instead: `kill -INT -<pgid>` is exactly what Ctrl-C does.
- The cursor *can* be moved without that permission, via `CGWarpMouseCursorPosition`
  through `ctypes` on ApplicationServices. Mouse-reporting bugs are invisible without
  it.
- Inside tmux, prefer tmux's own instrumentation: `send-keys` to drive,
  `list-panes -F '#{pane_left},#{pane_top}'` to read geometry back, `capture-pane -p`
  to read a pane as text. Screenshot anyway to confirm the browser actually drew.
- Screenshots carry a display colour profile (253 reads as ~232). Compare
  **distinctness**, never absolute RGB.


## Correction, 2026-08-07: the `iterm2` probe was invalid, not merely failing

This file recorded the `iterm2` target as unable to work here, on the strength of a
profile query returning nothing. The query was **not valid AppleScript**: iTerm2's
dictionary has no `profile` class (only application/session/tab/window), so
`get name of every profile` fails with `-2741` on *every* machine. The code turned
that error into "iTerm2 has no browser profile" — a malformed query reported as a
finding about the user's setup.

The conclusion was right here for the wrong reason, which is the worst way to be
right: it would have said the same thing on a correctly configured machine.

Replaced with a preferences read (`defaults export com.googlecode.iterm2`), which
checks **both** prerequisites separately and names the one that is missing:

| prerequisite | this host |
|---|---|
| `browserProfiles` advanced setting | **enabled** |
| a profile named `Browser` / `Web` / `WebView` | **absent** — only `Default` |

So the target still cannot render here, and now says so accurately.
