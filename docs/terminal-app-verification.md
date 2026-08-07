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

## The three defects are FIXED and re-verified (2026-08-07)

All three were reproduced, fixed, and confirmed the same way they were found: by
screenshotting a real Terminal.app window, not by reading exit codes. One frame
carries all of it — see "Evidence" below. The findings are kept in full because the
*reasoning* is what stops them coming back.

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

**FIXED.** `open_app` records `log_mark=$(wc -c < "$LOG")` before starting the server
and the wait loop scans `tail -c "+$((log_mark + 1))"` — only bytes this run wrote.
`_run_inline` also moved OUT of the `if [ -n "$shown" ]` branch, which closes the
cold-log hole: whether the verdict line arrived is a reporting question, and whether
we inline was already settled before the server started.

Confirmed twice, both against a warm log whose last entry disagreed with the truth:

| run | last stale line in log | line the shell printed |
|---|---|---|
| terminal, inline | `displayed on system (fallback)` | `displayed on none (fallback declined)` |
| terminal, from a non-tty (hook) | `displayed on none` | `displayed on system (fallback)` |

Each printed its own verdict, and the second is the stronger test: the two runs are
consecutive in one log and reported *different* answers, which the old code could not
do — it returned the previous line on the first loop iteration every time.

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

**FIXED.** `_plan_inline` runs before `nohup`, and when it produces a command
`--no-fallback` goes on the server's argv. `display_targets.open_app` gained a
`fallback` parameter; every degrade-to-`_open_system` site now goes through one
`_degrade()` helper that returns `"none (fallback declined)"` instead.

Confirmed by counting Safari's tabs across the run rather than trusting the log:
1 window / 10 tabs / **0** on `127.0.0.1` before, during and after, while carbonyl
drew the app in Terminal.app. `--no-fallback` was verified present on the real
process argv (`ps`), not just in the code.

The flag is correctly *absent* on the two paths that still need the browser fallback:
`target none` (nothing to inline) and the hook's non-tty invocation, which duly
reported `displayed on system (fallback)` and opened one tab.

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

**FIXED — but appending is not enough, and the first attempt at this failed.**

Appending exactly that produced a wrapper of the shape
`sh -c 'carbonyl …; printf reset'`, which looked right and did nothing. Ctrl-C is how
you quit carbonyl, and Ctrl-C sends SIGINT to the whole foreground process **group** —
the wrapper included. The wrapper died at the semicolon and never reached the printf.
Measured, with the fix supposedly in place:

    bash-3.2$ 35;79;28M35;77;25M35;76;23M35;75;21M35;74;19M …

i.e. the exact defect, unchanged, on the only exit path anyone uses. Had it been signed
off on "the printf is in the command string", this would have shipped as a fourth false
pass.

`_restore_modes` therefore **traps** rather than appends:

```sh
__mcp_app_reset() { printf '\033[?1000l…\033[?1049l'; }
trap '__mcp_app_reset; exit 130' INT
trap '__mcp_app_reset; exit 143' TERM
trap '__mcp_app_reset; exit 129' HUP
<browser>; __mcp_app_status=$?; __mcp_app_reset; exit "$__mcp_app_status"
```

Trapping converts death-by-signal into an ordinary exit that runs the reset first, and
each path exits with the status it should. Confirmed: after Ctrl-C-equivalent SIGINT to
the process group and ~50 cursor warps across the window (`CGWarpMouseCursorPosition`),
the prompt is `bash-3.2$` and nothing else. `mcp-app.sh` still exits 130.

Side benefit, unlooked-for: with the bare argv, SIGINT also killed the *script* that
called `mcp-app.sh` — a harness around it never got its exit status. Under the trapped
wrapper the caller survives and receives 130.

Escapes are written as octal (`\033`), not literal ESC bytes: the command string is
eval'd, logged and read by humans, and `printf` expands them itself.

### 4. `target terminal` advertised only the route that does not apply here

It printed "renders in a terminal browser inside a split pane — works in Ghostty, tmux,
WezTerm, kitty and iTerm2" followed by `terminal host: none detected` — a failure
report, in the one terminal where the inline route is the *point* rather than a
consolation.

**FIXED.** The note now names both routes and which one you are about to get, and
`--detect` says what "none detected" means instead of leaving it as a bare negative:

```
  note: renders in a terminal browser, so it works over SSH on a headless box.
        In Ghostty, tmux, WezTerm, kitty or iTerm2 the app opens in a split pane.
        In a plain terminal (Terminal.app, xterm, bare SSH) there is nothing to
        split, so it takes over THIS terminal until you quit the browser.
    carbonyl  real     Chromium rendering into the terminal — closest to the real thing

terminal host: none detected — no splitter here, so the app opens in THIS terminal
               (foreground; quit the browser to get your shell back)
```

## Evidence

One Terminal.app frame after quitting carbonyl carries all four results at once —
this run's verdict line, no second browser, the exit status, and a clean prompt after
the cursor was warped across the window:

```
TEST: inline path after fixes 1-3   pid=92472
serving http://127.0.0.1:8777/app/index.html
displayed on none (fallback declined)         <- this run's line, not a stale one (1)
no split available here — opening in THIS terminal (quit the browser to return)
=== mcp-app.sh exit status: 130 ===           <- status preserved through the wrapper
=== POST-EXIT PROMPT BELOW: mouse moves must leave NO escape garbage ===
bash-3.2$                                     <- clean. Before the fix: 35;79;28M35;77;25M… (3)
```

Safari across the same run: 1 window, 10 tabs, 0 on `127.0.0.1` (2).

Method, for whoever verifies this next — the agent shell has no controlling tty
(`/dev/tty` is "device not configured"), so printing escapes to your own stdout proves
nothing. Drive a real window instead:

- `osascript -e 'tell application "Terminal" to do script "bash /path/script.sh"'`
- raise by window id, `screencapture -T <n> -x full.png`; `screencapture -R <rect>`
  fails ("could not create image from rect"), so capture full screen and crop with PIL.
  Screen is 2× Retina (3584×2240 for 1792×1120 pt); `bounds of window id N` × 2 crops
  exactly to the window.
- `get contents of tab 1 of window id N` reads the screen as TEXT — far better than
  pixels for checking escape residue, and it is what caught the failed first attempt.
- System Events keystroke is blocked (no accessibility permission). Send signals to the
  process group instead: `kill -INT -<pgid>` is exactly what Ctrl-C does.
- The cursor CAN be moved without accessibility permission, via
  `CGWarpMouseCursorPosition` through `ctypes` on ApplicationServices. That is what
  provokes the mouse-reporting escapes; without it the test proves nothing.
- Screenshots carry a display colour profile (253 reads as ~232) — compare
  distinctness, never absolute RGB.

## tmux — verified, all four positions (2026-08-07)

tmux installed with the operator's approval (`brew install tmux`, 3.7b). `_split_tmux`
had **never executed** before this; it now has, in a real tmux session inside
Terminal.app, with the pane geometry read back from tmux itself and each result also
confirmed by screenshot.

| position | pane geometry (`list-panes`) | browser pane | verdict line |
|---|---|---|---|
| right  | browser `left=61`, shell `left=0`  | right  | `carbonyl in a tmux pane (right, dark theme)` |
| left   | browser `left=0`, shell `left=61`  | left   | `… (left, dark theme)` |
| top    | browser `top=0`, shell `top=15`    | top    | `… (top, dark theme)` |
| bottom | browser `top=15`, shell `top=0`    | bottom | `… (bottom, dark theme)` |

So `-b` is honoured on 3.7b: `left`/`top` really do place the pane *before* the current
one, not merely on the same axis. The **older-tmux degradation is still unmeasured** —
3.7b is far too new to exercise it. The code's claim that older tmux "ignores -b,
landing right/below" remains an assumption, and is the one thing here not backed by a
measurement.

Two things this also confirmed in passing: inside tmux `_plan_inline` correctly finds a
host and stands down (`--no-fallback` absent from the server's argv, split path taken),
and the four runs printed four *different* correct verdict lines into one shared log —
which the pre-fix scan could not have done.

### `_keep_open` survives failures, not Ctrl-C — measured

Same shape as defect 3, and worth writing down before someone "fixes" it:

- `/usr/bin/false` in the wrapper → pane **stays**, showing
  `[mcp-app-viewer] browser exited (1). Press Enter to close this pane.`
  The absent-vs-broken case it was written for works.
- SIGINT to the pane's process group → pane **closes** immediately; the wrapper dies at
  the semicolon exactly as `_restore_modes` did before it was trapped.

Left alone deliberately: a pane is disposable, and vanishing on a deliberate Ctrl-C is
the behaviour you want there. It is only on the *inline* path — where the shell is
handed back to you — that the signal path had to be trapped.

## Not verified

- **Terminal.app's View > Split Pane.** Terminal does have a split-pane command in its
  View menu, which would make it a genuine splitter rather than an inline-only host.
  Not investigated further because it cannot be reached from here: Terminal's
  AppleScript dictionary has no split verb, and the menu route needs System Events,
  which is refused (`osascript is not allowed assistive access`, -1719). Even granted
  that, a Terminal split pane offers no way to specify the command it runs — it would
  need `write text` into the new shell, which is the racy approach this codebase
  already rejected (it mangled a command into `llecho`; see `_open_iterm2`).
- **The inline path ignores the configured terminal browser and theme.** `--host` and
  `--inline-command` are called with the URL only (`terminal_browser.py:440` passes
  `sys.argv[i+1:i+2]`), so `inline_command` re-derives both from defaults instead of
  taking `_get browser` / `_get theme`. Today they agree by luck — `browser` is unset
  and `theme` is `auto` — so `status` reporting `term brwsr: carbonyl` happens to be
  true. Set `browser lynx` or `theme light` and the inline path will quietly do
  something else while `status` claims otherwise. Same class as finding 1; left unfixed
  because it was outside this pass's brief.
- **`inline_command` does not refuse the `stub` engine.** A parallel session reclassified
  Chawan `partial` → `stub` (3112324) and added a `stub` guard to `open_in_split`
  (`terminal_browser.py:415`). `inline_command` still tests only `== "text"`
  (`terminal_browser.py:484`), so `browser cha` would be launched inline and show an
  empty page for a working app — precisely the false negative both guards exist to
  prevent. Not fixed here to avoid editing that session's function underneath it.

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
