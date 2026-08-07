# Design: reuse the open pane instead of opening a new one

Status: **designed, not built.** Deferred deliberately — see "Why not just skip the
open" for the trap that makes the obvious version worse than the bug it fixes.

## The problem

`mcp-app.sh open` restarts the server and then unconditionally calls the display
target. So every render opens a **new** surface: `iterm2` splits again, a browser
target opens another tab. Used by hand that is a minor annoyance. With auto-open on it
is a pane fountain — one per tool result carrying an MCP App, which during an active
session is a lot.

## Why not just skip the open

The one-line fix is "if the server is already running, don't call the display target."
That gets you one pane and a **stale** one. The URL never changes
(`http://127.0.0.1:<port>/app/index.html`); what changes is the HTML behind it. Nothing
tells the already-loaded page to reload, so the pane keeps showing the previous app.

That is a strictly worse failure. A missing pane is visible — you notice nothing
opened. A pane showing the previous app is invisible: it looks like a rendered app, so
you conclude the current app renders fine when you have not seen it at all. This
project exists to close exactly that gap, so trading a visible annoyance for an
invisible wrong answer is not an acceptable trade.

Any reuse design must therefore make the pane self-updating **before** it makes the
open conditional. Ordering matters: shipping the skip first leaves a window where the
tool actively misleads.

## Design

### 1. A version token

The server keeps a monotonic counter, bumped whenever new HTML is staged. It is
exposed two ways:

- `GET /version` → `{"version": 7}` — cheap, no HTML body
- the served page is a **shell**, not the app HTML directly

### 2. The shell

`/app/index.html` becomes a minimal document holding the app in an iframe pointed at
`/app/content.html`, plus a poller:

```js
let current = window.__MCP_APP_VERSION__
setInterval(async () => {
  const { version } = await (await fetch('/version')).json()
  if (version !== current) { current = version; frame.contentWindow.location.reload() }
}, 1000)
```

The iframe matters: reloading the whole page would restart the poller and lose scroll
position on every render, and an app that navigates internally would fight the shell.
Note this changes the app's origin story slightly — it is same-origin with the shell,
so the poller can reload it, but an app that expects to be the top-level document may
behave differently. Verify against a real app before shipping, and prefer replacing
the iframe's `src` over `contentWindow.location.reload()` if it does.

Poll rather than push: SSE or a WebSocket is the tidier mechanism, but it adds a
connection to keep alive and a reconnect path, and a 1s poll of a loopback endpoint
returning ~20 bytes costs nothing measurable. Revisit only if that proves wrong.

### 3. The open decision

Track per profile, in the existing state file:

```
pane_opened_at=<epoch>
pane_target=iterm2
```

Open the display target when **any** of these hold, otherwise skip:

- no viewer is running (nothing can be showing)
- `pane_target` differs from the current target (you asked for somewhere else)
- no `pane_opened_at` recorded (first render for this profile)

### 4. The part that cannot be solved, only mitigated

**The process cannot tell whether you closed the pane.** There is no callback; a closed
iTerm2 pane and a closed browser tab both look identical to a server that no one is
polling. So the marker will sometimes claim a pane exists when it does not, and the
render goes nowhere.

Mitigate with a **liveness heartbeat**, which the poller gives us for free: the server
records the timestamp of the last `/version` request. If nothing has polled for, say,
5 seconds when a render arrives, treat the pane as gone and open a new one. That is
self-correcting rather than clever — a closed pane stops polling, so it is detected on
the next render rather than assumed away.

`/mcp-app open` invoked **explicitly** should always open, heartbeat or not. Someone
typing the command wants to see the app now; inferring they already can is the kind of
helpfulness that reads as a broken tool. Only the hook's automatic path consults the
marker.

## Slices

1. `/version` + shell + poller. No behaviour change (still opens every time), but the
   pane becomes self-updating. Verifiable on its own: render twice, watch the pane
   change without a new one appearing.
2. Heartbeat + `pane_opened_at` marker; the hook path skips the open when a pane is
   live. Manual `open` still always opens.
3. Target-change and stale-marker handling.

Slice 1 is the one that must land first, and is independently useful.

## What this does not address

If two profiles (Claude Code and Codex, say) each hold a pane, they hold two — by
design, since they run on different ports and have separate state. Consolidating them
into one pane would mean a shared broker process and cross-agent state, which is a
much larger change for a benefit nobody has asked for.
