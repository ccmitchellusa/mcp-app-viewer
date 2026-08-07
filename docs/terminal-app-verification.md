# Terminal.app verification — the plain-terminal path

Verified 2026-08-07 on macOS 26.6 (build 25G5065a), Terminal.app 2.15 (470.2),
carbonyl 0.0.2 (nvm, node v24.11.0), `TERM=xterm-256color`.

Terminal.app is the *plain terminal* case: no splitter, so it is the only host that
exercises `_maybe_inline`. Everything below was confirmed by screenshotting a real
Terminal.app window and measuring pixels, not by reading exit codes — this project's
own status output is one of the things that turned out to be lying (finding 1).

## Truecolor — WORKS. The premise it was written against is out of date.

Terminal.app was historically 256-colour, and the inline path was expected to degrade.
It does not, on this OS:

| probe | requested | rendered | verdict |
|---|---|---|---|
| 24-step red ramp | `0,0,0` → `253,0,0` | 24 of 24 distinct, monotonic | 24-bit |
| 6 near-identical greens | `0,100,0` → `0,110,0` (Δ2/255) | 6 of 6 distinct, monotonic | 24-bit |
| half-block `U+2580` | red fg over blue bg | red over blue | correct |

A 256-colour terminal cannot produce either result: the xterm 6×6×6 cube has only six
red levels, and greens two apart collapse to a single entry. Measured RGB is uniformly
shifted (`253` reads as `232`) because the screenshot carries the display colour
profile — irrelevant, since the test is distinctness, not absolute value.

`COLORTERM=truecolor` is set **by Terminal.app itself**. Confirmed rather than assumed:
it is absent from every shell rc (`~/.zshrc`, `~/.zprofile`, `~/.zshenv`, `/etc/zshrc`,
`/etc/zprofile`) and present in `env` of a freshly-spawned window with its own tty.

So carbonyl in Terminal.app looks the way it looks in Ghostty. Nothing to degrade.

## The inline path renders — with three defects

`printf '<html>…' | mcp-app.sh open -` in a real Terminal.app window: carbonyl took over
the window (title `bash — carbonyl ◂ inline_test.sh`) and drew the app. The requested
`#3fb950` on `#111` rendered as `(100,181,93)` on `(24,24,24)` — matching under the same
profile shift measured in the ramps. On SIGINT carbonyl exited and the shell returned,
`mcp-app.sh` exiting `130`.

### 1. `open_app` reports a STALE display target — a false pass

`bin/mcp-app.sh:167` greps the whole append-only log and takes the last match:

```sh
shown=$(grep -a "mcp-app-viewer: displayed on" "$LOG" 2>/dev/null | tail -1)
```

The log is never truncated, so a line from a previous run matches on the first
iteration and the wait loop exits immediately. Observed directly: the shell printed

    displayed on terminal: carbonyl in an iterm2 pane (right, dark theme)

from **Terminal.app**, while the server had just logged, for that same run:

    not inside a terminal that can split (looked for tmux, Ghostty, WezTerm, kitty, iTerm2)
    displayed on system (fallback)

The reported line was 34 minutes old. This is precisely the failure the surrounding
comment says it exists to prevent — "displayed on system (fallback)" and "displayed on
terminal" being indistinguishable.

Second-order effect: `_maybe_inline` is only called inside `if [ -n "$shown" ]`. On a
**fresh** log (new machine, first run) there is no stale line to match, so if the real
one takes longer than the 5s budget, `shown` is empty, the `else` branch runs, and the
inline browser never launches at all. The bug hides itself on a warm log and bites on a
cold one.

Fix: record the log size before starting the server and only scan past that offset, or
have the server write its verdict to a per-run file the shell polls.

### 2. Both the system browser and the inline browser open

For `target=terminal` with no splitter, `display_targets.open_app` falls back to
`_open_system(url)` (`bin/display_targets.py:295`) — and *then* `mcp-app.sh` runs
carbonyl inline. Confirmed: Safari held a `http://127.0.0.1:8777/app/index.html` tab
while carbonyl was drawing the same app in the terminal.

So the plain-terminal case steals focus into a browser you did not ask for, which is the
outcome the inline path was written to avoid — and on the headless box it is justified
by, the fallback is a no-op that still reports success.

The viewer cannot know the caller intends to inline. `mcp-app.sh` should tell it: decide
inline-ability *before* starting the server (target is `terminal`, no `--host`, `[ -t 1 ]`,
an inline command exists) and pass something like `--no-fallback` so the server returns
`none` instead of opening a browser.

### 3. carbonyl leaves the terminal in mouse-reporting mode

After carbonyl exits, the shell is left with SGR mouse tracking enabled: every mouse
movement writes `^[[<35;24;39M` escapes into the prompt. The captured window contents
after quitting are full of them.

This matters *only* on the inline path. The split path drops the browser into a
disposable pane, and `_keep_open` ends it; inline returns you to the shell you were
working in and hands it back broken.

`inline_command` returns bare argv — no `_keep_open`, and no restore. It should reset the
modes carbonyl set:

```sh
printf '\e[?1000l\e[?1002l\e[?1003l\e[?1006l\e[?1049l'
```

appended to the inline command (and the exit status preserved), so quitting the browser
returns a usable shell.

## Not verified

- **tmux** — not installed on this machine (`/usr/bin/screen` exists; `tmux` does not).
  `_split_tmux` has still never been executed, and the `-b` behaviour for `left`/`top` on
  older tmux is still unmeasured.
- **`target terminal`'s advice text** is now stale. It prints "renders in a terminal
  browser inside a split pane — works in Ghostty, tmux, WezTerm, kitty and iTerm2"
  followed by `terminal host: none detected`, which reads as a failure in exactly the
  terminal where the inline path is the intended route. It should say the inline path
  covers this case.

## The hook does not inline — confirmed

`hooks/mcp-app-posttooluse-hook.sh:77` places the variable on the `bash` invocation,
after the pipe, which is correct:

```sh
printf '%s' "$html" | MCP_APP_NO_INLINE=1 bash "$PROJECT_DIR/bin/mcp-app.sh" open - >> "$LOG" 2>&1
```

Two further guards make a wedge impossible even if that were wrong: `_maybe_inline`
requires `[ -t 1 ]`, and the hook redirects stdout to the log, so stdout is never a tty.
Verified empirically — `open -` run from a non-tty shell returned on its own in under
12s and launched no browser.
