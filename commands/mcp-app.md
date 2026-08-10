---
description: Render MCP Apps (ui:// resources) in a real browser, control the auto-open hook, and log in to OAuth-protected MCP servers
argument-hint: "[on|off] | open <file> | last | target <system|chrome|safari|firefox|iterm2|vscode|none> | position <right|left|top|bottom> | base <url> | assets <dir> | oauth <login|status|token|logout|watch|setup> | stop | log"
allowed-tools: Bash(bash __MCP_APP_PROJECT_DIR__/bin/mcp-app.sh:*)
---

mcp-app-viewer output:

!`bash __MCP_APP_PROJECT_DIR__/bin/mcp-app.sh $ARGUMENTS`

Present the result above concisely.

- With no arguments it prints status — show it as-is; it is already compact.
- `on` / `off` toggles whether a tool result carrying an MCP App renders automatically.
  Mention that it is off by default deliberately: an agent that opens browser windows
  uninvited is worse than one that shows nothing.
- `target` chooses where the app appears. If the user picked `iterm2` and the output
  says a browser profile is missing, pass that setup line through verbatim — it is a
  one-time iTerm2 change (Settings > Advanced > `browserProfiles`, add a profile named
  `Browser`, restart iTerm2) and the viewer falls back to a normal browser until then.
  If they picked `chrome`, note that chrome-devtools tooling can then inspect the
  rendered app, not just display it.
- `position` sets pane placement for split targets. iTerm2 places new panes right or
  below, so `left`/`top` select the same axis rather than silently doing something
  else — say so if the output does.
- `base` sets an origin to proxy the app's relative assets from; `assets` points at a
  local directory instead. Use `assets` when the origin needs auth — a proxied fetch
  against an authenticated host returns 403, and the viewer reports that rather than
  serving an empty file.
- If a URL is printed, give it to the user plainly; the viewer keeps serving until
  `/mcp-app stop`.

## OAuth login (`oauth login|status|token|logout|watch|setup`)

For MCP servers protected by the MCP authorization spec (401 + `WWW-Authenticate`,
`/.well-known/oauth-protected-resource`), this runs the interactive auth-code + PKCE
flow once and stores tokens (`~/.mcp-app-viewer/oauth-tokens.json`, mode 0600), so
agents that don't implement MCP OAuth natively can still connect:

- `oauth login` — browser sign-in against the OIDC issuer (loopback redirect on an
  ephemeral 127.0.0.1 port, RFC 8252). `--manual` for headless/SSH: it prints the
  authorize URL and accepts the pasted redirect URL.
- `oauth status` — stored subject/email/account/expiry per issuer, one block per
  stored account (never token values).
- `oauth token` — prints a valid access token, refreshing first if expired. This is
  the one command that emits a token; pipe it into client config. With several
  accounts stored for the issuer, pass `--account <guid>` to select one.
- `oauth logout` — deletes the stored entry (`--account <guid>` to remove just one
  account's login).
- `oauth watch` — keeps EVERY stored login fresh: loops over the token store and
  refreshes any entry within `--margin` seconds of expiry (default 600), sleeping
  `min(60s, time-to-nearest-refresh/2)` between passes. `--account <guid>` restricts
  the watch to one account's logins. Logs refresh events to
  stderr (issuer + account + new expiry, never token values); a login whose refresh token is
  revoked/expired is reported by issuer as needing re-login while the others keep
  being watched. Clean exit on SIGINT/SIGTERM. `oauth watch --once` does a single
  refresh pass and exits. Run it in the background during long test suites — IAM
  access tokens live 60 minutes, so a token fetched at suite start expires mid-run
  without it:

  ```sh
  bash bin/mcp-app.sh oauth watch >> /tmp/mcp-oauth-watch.log 2>&1 &
  ```

`--issuer` / `--client-id` default from `MCP_OAUTH_ISSUER` / `MCP_OAUTH_CLIENT_ID`
(set them in `~/.config/mcp-app-viewer/config.sh`). For IBM Cloud (per-user
login; no client id/secret needed for the passcode flow):

```sh
export MCP_OAUTH_ISSUER="https://iam.cloud.ibm.com/identity"
```

For other OIDC providers (auth-code + PKCE), set `MCP_OAUTH_ISSUER` /
`MCP_OAUTH_CLIENT_ID` per the provider's app registration, plus
`MCP_OAUTH_CLIENT_SECRET` when the provider requires HTTP Basic at the token
endpoint. Loopback redirect URIs (`http://127.0.0.1:*`) must be registered with
the provider first.

After `oauth login` completes, tell the user the sign-in succeeded and who as
(subject/email from the output); after `oauth token`, do NOT echo the token into
the transcript — say it was printed for config use.

## Two login flows (`--flow auto|authcode|passcode`)

`--flow auto` (the default) picks by issuer host: `iam.cloud.ibm.com` → passcode,
anything else → authcode.

- **authcode** (App ID and other OIDC issuers): auth-code + PKCE as above. Use
  this when the MCP server trusts an App ID issuer — the token proves identity
  to the server, and IBM Cloud calls run as the server's service identity.
- **passcode** (IBM Cloud IAM, `https://iam.cloud.ibm.com/identity`): headless
  IBMid login. The helper opens `…/identity/passcode` (from IAM's discovery
  doc), the user signs in and pastes the one-time code, and the helper exchanges
  it at the IAM token endpoint as the well-known public CLI client `bx` (HTTP
  Basic `bx:bx` — required by IAM even though there is no real secret; no PKCE,
  no client registration). No `--client-id` needed; the entry is stored under
  the `iam-passcode` slot and refreshes via IAM's refresh_token grant.

  ```sh
  bash bin/mcp-app.sh oauth login --flow passcode \
    --issuer https://iam.cloud.ibm.com/identity
  ```

  Use this when the MCP server does per-user authorization: an IAM access token
  presented to the server is forwarded to IBM Cloud APIs, so calls execute with
  the USER's own IBMid permissions (tenant = the token's `account.bss`, i.e.
  the account targeted at login). The token is a
  real IBM Cloud credential: treat `oauth token` output accordingly.

  **Switching accounts:** the store is multi-account — entries are keyed
  `issuer|slot|account.bss`, so re-running the passcode login targeting another
  account ADDS a second stored login instead of overwriting the first. `oauth
  status` lists every stored account; `oauth token|logout --account <guid>`
  selects one, and `oauth watch --account <guid>` watches only that account.
  When several accounts are stored and `--account` is omitted, token/logout
  print the choices and exit 2 — pick one explicitly. (Logins from before this
  change are re-keyed automatically on first read; issuers whose tokens carry
  no `account.bss` keep the old `issuer|slot` key and behave as before.)

## One-shot client setup (`oauth setup`)

`oauth setup` does everything except the browser sign-in, so an agent can
onboard itself to an OAuth-protected MCP server unaided:

```sh
bash bin/mcp-app.sh oauth setup --client bob --server https://<deployment> [--workspace <dir>]
```

- With no valid stored login it prints the EXACT command the human must run
  (`mcp-app.sh oauth login --flow passcode --issuer …`) and exits 2 — agents
  should detect exit 2 and hand that one step to the user, then re-run setup.
- Otherwise it fetches a fresh token (never printed) and, per `--client`:
  - `bob` — merges an `ibmcloud` entry into `<workspace>/.bob/mcp.json`
    (url + transportType http + Authorization header; existing servers and
    top-level keys are preserved) and prints the entry with the token redacted.
  - `hermes` — prints the exact `hermes mcp add ibmcloud --url <url> --auth oauth`
    + `hermes mcp login ibmcloud` commands (hermes' native OAuth path).
  - `generic` (default) — prints the JSON config snippet (token redacted) and
    a curl verification command using `$(mcp-app.sh oauth token)`.
- Finally it self-checks: `initialize` + `tools/list` against `<server>/mcp`
  with the token (honouring `mcp-session-id`), printing PASS with the tool
  count, or FAIL with a re-login hint on 401 (exit 1).

`--server` accepts the deployment URL with or without a trailing `/mcp`.

Do not run any other command or edit any file for this request.
