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
tmux 3.7b · VS Code 1.132.0 (verification was against **1.128.1**) ·
carbonyl 0.0.2 · node v24.11.0 · Python 3.14.2

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
| `vscode` | **VERIFIED** on VS Code 1.128.1, forks UNVERIFIED | Renders a Simple Browser pane in a real editor group (6acbb6b). Pane reuse verified: two consecutive renders updated one pane in place (68db12b). Fork discovery via `product.json` removes the guessing but **has never run against an actual fork**; a build that stripped Simple Browser would still show nothing. The installed VS Code has since moved to 1.132.0 — unverified on that build. |
| `chrome`, `safari`, `firefox`, `edge`, `brave`, `arc` | **UNVERIFIED individually** | `_open_named` shells out to `open -a <app>`. Only the `system` default (Safari) has actually been watched to open. The others are the same three lines of code, but nobody has run them. |
| `iterm2` | **NEVER WORKED HERE** | Requires the `browserProfiles` advanced setting *and* a profile named `Browser`. Checked on this machine: iTerm2 3.6.11 has no `Browser`/`Web`/`WebView` profile, so the target correctly refuses and falls back. The rendering path has therefore never executed. Do not confuse this with `terminal` + iTerm2 host, below — that is a different function and does work. |

## Terminal hosts — the `terminal` target's split route

`detect_host()` picks the host; each has its own splitter. tmux wins over the outer
terminal deliberately, since inside tmux the visible panes are tmux's.

| host | status | evidence |
|---|---|---|
| **tmux** | **VERIFIED — all four positions** (2026-08-07) | First execution of `_split_tmux` in the project's life. Pane geometry read back from `list-panes` and each position confirmed by screenshot of carbonyl rendering: right `left=61`, left `left=0` (shell pushed to 61), top `top=0` (shell at 15), bottom `top=15`. `-b` is honoured on 3.7b, so `left`/`top` genuinely place the pane *before* the current one. |
| **Ghostty** | **VERIFIED** on 1.3.1 | First execution of `_split_ghostty` found three defects, each measured not inferred (62a5793): `focused terminal` is a property of a tab and the bare specifier raised `-1728`; splitting the *focused* pane made `position` drift, so right/bottom landed beside the shell but then left landed beside the bottom **browser** pane; `wait after command` is needed to hold a pane whose command never launches. Anchoring to a pane that reports a working directory fixes the drift. |
| **iTerm2** | **PROBABLY WORKS — not visually confirmed** | `_split_iterm2` uses the *default* profile running a command, so unlike the `iterm2` target it needs no browser profile. A real run logged `displayed on terminal: carbonyl in an iterm2 pane (right, dark theme)`. That is the tool's own success message, which this project has caught lying before, and nobody has looked at the pane. Treat as unconfirmed until someone does. |
| **WezTerm** | **UNVERIFIED** | Not installed. `_split_wezterm` has never executed. |
| **kitty** | **UNVERIFIED** | Not installed. `_split_kitty` has never executed. It additionally needs `allow_remote_control yes`, which nobody has exercised. |

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

## Working configurations

Combinations watched end to end. Anything not listed may still work; nobody has looked.

1. **Terminal.app, no multiplexer** — `target terminal`, carbonyl, inline. Takes over
   the window; Ctrl-C returns a clean shell and exit 130. Truecolor.
2. **tmux in Terminal.app** — `target terminal`, carbonyl, any of the four positions.
   The split path; the shell returns to its prompt immediately.
3. **Ghostty** — `target terminal`, carbonyl, split pane, all four directions.
4. **VS Code 1.132.0 running the companion extension** — `target vscode`, real
   four-way editor-group placement, panes reused across renders. (Verified at 1.128.1.)
5. **Anywhere, any OS** — `target system`. The universal fallback, and what every
   other target degrades to.
6. **Headless / remote agent** — `target none`, or `target terminal` over SSH. `none`
   is correct when opening a browser would either fail or open it on the wrong machine.

## Known limitations

### Measured — these are real and understood

- **`iterm2` target needs manual one-time setup** and cannot self-configure: the
  `browserProfiles` advanced setting and a `Browser` profile are neither on by default
  nor creatable from AppleScript. Until they exist the target falls back and says why.
- **`left`/`top` on iTerm2 select the axis, not the side.** iTerm2 places new panes
  right/below. The tool says so rather than silently doing something else.
- **`_keep_open` does not survive Ctrl-C.** A pane closes outright on SIGINT, because
  the wrapper dies at the semicolon before printing. It *does* hold the pane for
  non-signal failures — `/usr/bin/false` leaves
  `[mcp-app-viewer] browser exited (1). Press Enter to close this pane.` Left as is: a
  pane is disposable and dismiss-on-Ctrl-C is the behaviour you want there. Only the
  inline path, which hands your own shell back, needed the signal trapped.
- **One surface per render, for the browser and iTerm2 targets.** Each render creates a
  new tab/pane rather than reusing one. Does *not* apply to `vscode` (verified reusing
  a pane) — see [pane-reuse-design.md](pane-reuse-design.md).
- **The inline path ignores the configured browser and theme.** `--inline-command` is
  invoked with the URL only, so `inline_command` re-derives both from defaults instead
  of the saved state. They agree today only because `browser` is unset and `theme` is
  `auto`; set `browser lynx` or `theme light` and `status` starts lying. Unfixed.
- **`inline_command` refuses only the `text` engine, not `stub`.** `open_in_split` got
  a `stub` guard when Chawan was reclassified; the inline path did not. `browser cha`
  would be launched inline and render an empty page. Unfixed.

### Untested paths — no evidence either way

- WezTerm and kitty splitters (neither installed).
- browsh, and all five text browsers, including the `--fallback-preview` opt-in.
- Named browser targets other than the system default.
- The `iterm2` target's actual rendering (no `Browser` profile on this machine).
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
