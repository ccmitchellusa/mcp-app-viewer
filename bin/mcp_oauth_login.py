#!/usr/bin/env python3
"""Interactive OAuth2/OIDC login helper for MCP servers behind an OIDC issuer.

Terminal MCP agents that do not implement the MCP authorization flow (401 +
``WWW-Authenticate`` -> ``/.well-known/oauth-protected-resource`` -> auth-code
+ PKCE) cannot log in to an OAuth-protected MCP server on their own. This
helper runs that flow once against any OIDC issuer (built for IBM Cloud
App ID, kept generic): it opens the authorize page in a browser, receives the
loopback redirect (RFC 8252) on an ephemeral 127.0.0.1 port, exchanges the
code, and stores the tokens. Afterwards ``token`` prints a valid access token
-- refreshing first when expired -- for piping into client config.

Two details it exists to get right, because both fail SILENTLY otherwise:

1. **PKCE + state are not optional.** Public clients hold no secret, so the
   code challenge and the state check are the whole proof that the browser
   callback belongs to this login attempt. A helper that skips them "works"
   and is wrong.

2. **The token store is a credential file.** It is chmod 0600, token values
   are never logged, and only the explicit ``token`` command prints one --
   that is its entire job.

Usage:
    mcp_oauth_login.py login  --issuer <url> --client-id <id> [--manual]
    mcp_oauth_login.py login  --issuer https://iam.cloud.ibm.com/identity --flow passcode
    mcp_oauth_login.py status [--issuer <url>]
    mcp_oauth_login.py token  --issuer <url> --client-id <id> [--account <guid>]
    mcp_oauth_login.py logout --issuer <url> --client-id <id> [--account <guid>]
    mcp_oauth_login.py watch  [--margin <seconds>] [--once] [--account <guid>]
    mcp_oauth_login.py setup  --client bob|hermes|generic --server <url> [--workspace <dir>]

Multi-account: store entries are keyed ``issuer|slot|account`` when the
token carries an IBM Cloud account (the JWT ``account.bss`` claim), so
logins to several accounts at one issuer coexist. ``token``/``logout``/
``watch`` take ``--account <guid>`` to select one; without it, a single
stored entry is used and several are reported as a choice (exit 2).
Issuers whose tokens carry no bss claim keep the legacy ``issuer|slot``
key and behave exactly as before.

--issuer and --client-id default to $MCP_OAUTH_ISSUER / $MCP_OAUTH_CLIENT_ID.

Flows (--flow auto|authcode|passcode; auto selects passcode when the issuer
host is iam.cloud.ibm.com):

- authcode: authorization-code + PKCE against any OIDC issuer (IBM Cloud
  App ID), loopback redirect or --manual paste.
- passcode: IBM Cloud IAM's headless login — open the passcode page, sign in
  with an IBMid, paste the one-time code, exchange it at the token endpoint
  (public client: the well-known IBM Cloud CLI client ``bx``; IAM requires
  HTTP Basic even for public grants). The resulting IAM access token carries
  the caller's own IBM Cloud permissions.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

def token_store() -> Path:
    """Credential store path for OAuth tokens.

    Precedence:
    1. Explicit $MCP_OAUTH_STORE override.
    2. Profile-scoped store under $MCP_APP_CONFIG_DIR/profiles/<profile>/ when
       $MCP_OAUTH_PROFILE or $MCP_APP_PROFILE is set.
    3. Legacy single-user store under ~/.mcp-app-viewer/oauth-tokens.json.
    """
    explicit = os.environ.get("MCP_OAUTH_STORE")
    if explicit:
        return Path(explicit).expanduser()
    profile = (
        os.environ.get("MCP_OAUTH_PROFILE") or os.environ.get("MCP_APP_PROFILE") or ""
    ).strip()
    if profile:
        config_dir = Path(
            os.environ.get("MCP_APP_CONFIG_DIR", Path.home() / ".config" / "mcp-app-viewer")
        ).expanduser()
        return config_dir / "profiles" / profile / "oauth-tokens.json"
    return (Path.home() / ".mcp-app-viewer" / "oauth-tokens.json").expanduser()


CALLBACK_PATH = "/oauth/callback"
# Refresh this far ahead of expiry so a token handed out is not already stale
# by the time the client's request lands.
REFRESH_MARGIN_S = 60
LOGIN_TIMEOUT_S = 300
# Watch mode: refresh this far ahead of expiry (IAM access tokens live 60 min,
# so 10 min keeps a comfortable buffer for long-running consumers), and never
# sleep longer than this between passes.
WATCH_MARGIN_S = 600
WATCH_MAX_SLEEP_S = 60

# IBM Cloud IAM passcode flow (headless IBMid login). IAM's token endpoint
# requires HTTP Basic even for public grants; ``bx:bx`` is the well-known
# public IBM Cloud CLI client, not a secret (it ships in the open-source CLI).
IAM_ISSUER_HOST = "iam.cloud.ibm.com"
PASSCODE_GRANT = "urn:ibm:params:oauth:grant-type:passcode"
PASSCODE_BASIC = ("bx", "bx")
# Client slot used in the token store for passcode logins (no client
# registration exists for this flow).
PASSCODE_STORE_SLOT = "iam-passcode"


def _warn(msg: str) -> None:
    print(f"mcp-app-viewer: {msg}", file=sys.stderr)


class OAuthError(Exception):
    """A failure the user can act on: discovery, callback, or token endpoint."""


def _http_json(
    url: str,
    data: dict | None = None,
    timeout: int = 30,
    basic_auth: tuple[str, str] | None = None,
) -> dict:
    """GET a JSON document, or POST a form and parse the JSON response.

    Token-endpoint errors carry ``error``/``error_description`` — those are
    the actionable part and are safe to show. Response bodies that are not
    JSON (a proxy error page, an HTML login wall) are reported as such rather
    than surfaced raw, because they can contain anything.

    ``basic_auth`` sends HTTP Basic client credentials (client_id:secret) —
    required by providers such as IBM App ID whose token endpoint rejects
    secret-less clients even for auth-code + PKCE.
    """
    if data is None:
        req = urllib.request.Request(url)
    else:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    if basic_auth:
        import base64

        encoded = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {encoded}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            desc = payload.get("error_description") or payload.get("error")
            if desc:
                raise OAuthError(f"{url}: {desc}") from None
        except json.JSONDecodeError:
            pass
        raise OAuthError(f"{url}: HTTP {exc.code} (non-JSON error body)") from None
    except urllib.error.URLError as exc:
        raise OAuthError(f"{url}: {exc.reason}") from None
    except json.JSONDecodeError:
        raise OAuthError(f"{url}: response was not JSON") from None


def _discover(issuer: str) -> dict:
    """Fetch the issuer's OIDC discovery document at runtime.

    Endpoints are never hardcoded beyond this one well-known path: an issuer
    that moves its token endpoint breaks a hardcoded helper invisibly.
    """
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    doc = _http_json(url)
    for field in ("authorization_endpoint", "token_endpoint"):
        if field not in doc:
            raise OAuthError(f"discovery document at {url} has no {field}")
    return doc


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _store_key(issuer: str, client_id: str, account: str | None = None) -> str:
    base = f"{issuer.rstrip('/')}|{client_id}"
    return f"{base}|{account}" if account else base


def _load_store() -> dict:
    store_path = token_store()
    try:
        store = json.loads(store_path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # A corrupt store must not silently become an empty one — that would
        # look like a logout. Refuse and say where the file is.
        raise OAuthError(f"token store {store_path} is corrupt — fix or delete it")
    # Migration: entries written before the store became multi-account live
    # under issuer|slot. When the token carries the IBM Cloud account
    # (account.bss), re-key to issuer|slot|account so the next save writes
    # the new format; reads keep working either way (see _select_key).
    # Entries without a bss claim (non-IBM issuers) keep the legacy key.
    for key in list(store):
        if key.count("|") != 1:
            continue
        account = _entry_account(store[key])
        if account:
            store[f"{key}|{account}"] = store.pop(key)
    return store


def _save_store(store: dict) -> None:
    store_path = token_store()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(store_path.parent, 0o700)
    tmp = store_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(store_path)


def _jwt_claims(token: str) -> dict:
    """Decode a JWT payload UNVERIFIED — for status display only.

    Trust decisions belong to the resource server, which verifies the
    signature. Here the claims only answer "who did I log in as and when does
    this expire", and a forged local answer to that harms no one.
    """
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _entry_account(entry: dict) -> str | None:
    """IBM Cloud account GUID this entry is bound to (JWT account.bss), if known."""
    if entry.get("account"):
        return entry["account"]
    account = _jwt_claims(entry.get("access_token", "")).get("account")
    if isinstance(account, dict):
        return account.get("bss")
    return None


def _record_tokens(entry: dict, tokens: dict) -> dict:
    """Fold a token-endpoint response into a store entry. Never logs values."""
    if "access_token" not in tokens:
        raise OAuthError("token endpoint response had no access_token")
    entry["access_token"] = tokens["access_token"]
    # A refresh response may omit refresh_token — the old one stays valid.
    if tokens.get("refresh_token"):
        entry["refresh_token"] = tokens["refresh_token"]
    expires_in = int(tokens.get("expires_in", 3600))
    entry["expires_at"] = int(time.time()) + expires_in
    claims = _jwt_claims(tokens.get("id_token") or tokens["access_token"])
    if claims.get("sub"):
        entry["subject"] = claims["sub"]
    if claims.get("email"):
        entry["email"] = claims["email"]
    # IBM Cloud IAM tokens name the account they were issued for. Recorded
    # only when present, so a refresh response without the claim never
    # clears an existing binding.
    account = claims.get("account")
    if isinstance(account, dict) and account.get("bss"):
        entry["account"] = account["bss"]
    return entry


def _resolve_flow(issuer: str, flow: str) -> str:
    """Resolve --flow auto: passcode for the IBM Cloud IAM issuer, else authcode."""
    if flow != "auto":
        return flow
    host = urllib.parse.urlparse(issuer).hostname or ""
    return "passcode" if host == IAM_ISSUER_HOST else "authcode"


def _refresh(entry: dict, token_endpoint: str, client_id: str, client_secret: str | None = None) -> dict:
    if not entry.get("refresh_token"):
        raise OAuthError("access token expired and no refresh token stored — login again")
    if entry.get("flow") == "passcode":
        # IAM passcode login: public client (bx), no client_id form field.
        tokens = _http_json(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": entry["refresh_token"],
            },
            basic_auth=PASSCODE_BASIC,
        )
        return _record_tokens(entry, tokens)
    tokens = _http_json(
        token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": entry["refresh_token"],
            "client_id": client_id,
        },
        basic_auth=(client_id, client_secret) if client_secret else None,
    )
    return _record_tokens(entry, tokens)


def _select_key(store: dict, issuer: str, slot: str, account: str | None) -> str:
    """Resolve which store entry an invocation means.

    With ``account``: the entry bound to that account (new-format key, with
    the legacy key as fallback). Without it: the only entry for issuer|slot
    — or, when several accounts are stored, print the choices and fail so
    the caller can re-run with ``--account`` (main maps this to exit 2).
    """
    prefix = _store_key(issuer, slot)
    if account:
        key = f"{prefix}|{account}"
        if key in store:
            return key
        legacy = store.get(prefix)
        if legacy and _entry_account(legacy) in (None, account):
            return prefix
        raise OAuthError(
            f"no stored login for account {account} at {issuer} — run login first"
        )
    keys = sorted(k for k in store if k == prefix or k.startswith(prefix + "|"))
    if not keys:
        raise OAuthError(f"no stored login for issuer {issuer} — run login first")
    if len(keys) > 1:
        _warn(f"multiple stored logins for {issuer} — re-run with --account <guid>:")
        for key in keys:
            entry = store[key]
            when = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime(entry.get("expires_at", 0))
            )
            desc = f"subject {entry.get('subject', '?')}"
            if entry.get("email"):
                desc += f", {entry['email']}"
            _warn(f"  --account {_entry_account(entry) or '?'}  ({desc}, expiry {when})")
        raise OAuthError(f"multiple accounts stored for {issuer} — specify --account")
    return keys[0]


def _ensure_valid(
    issuer: str,
    client_id: str,
    store: dict,
    client_secret: str | None = None,
    account: str | None = None,
) -> tuple[str, dict, bool]:
    """(store key, entry with a live access token, refreshed?) — refreshing if needed.

    Third element is True when a refresh happened (caller must persist).
    """
    key = _select_key(store, issuer, client_id, account)
    entry = store[key]
    if entry.get("expires_at", 0) - REFRESH_MARGIN_S > time.time():
        return key, entry, False
    endpoints = _discover(issuer)
    bound_account = entry.get("account")
    entry = _refresh(entry, endpoints["token_endpoint"], client_id, client_secret)
    if bound_account:
        # A refresh must not rebind the entry to another account, whatever
        # the refreshed token's claims say.
        entry["account"] = bound_account
    return key, entry, True


def _authorize_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    challenge: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{authorization_endpoint}?{query}"


def _store_login(store: dict, issuer: str, slot: str, tokens: dict) -> tuple[str, dict]:
    """Record fresh login tokens under issuer|slot|account.

    The account comes from the token's account.bss claim; when it is absent
    (non-IBM issuers) the legacy issuer|slot key is used. A legacy-key entry
    for the SAME account is dropped so re-login does not leave an alias.
    """
    entry = _record_tokens({}, tokens)
    account = entry.get("account")
    key = _store_key(issuer, slot, account)
    legacy_key = _store_key(issuer, slot)
    existing = store.get(key) or (store.get(legacy_key) if account else None)
    if existing and "refresh_token" not in entry and existing.get("refresh_token"):
        entry["refresh_token"] = existing["refresh_token"]
    if account and legacy_key in store and _entry_account(store[legacy_key]) in (None, account):
        del store[legacy_key]
    store[key] = entry
    return key, entry


def _report_login(issuer: str, entry: dict) -> None:
    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(entry["expires_at"]))
    print(f"mcp-app-viewer: logged in to {issuer}")
    if entry.get("subject"):
        print(f"  subject     : {entry['subject']}")
    if entry.get("email"):
        print(f"  email       : {entry['email']}")
    if entry.get("account"):
        print(f"  account     : {entry['account']}")
    print(f"  token expiry: {when}")
    print(f"  refresh     : {'yes' if entry.get('refresh_token') else 'no'}")
    print(f"  stored in   : {token_store()} (mode 0600)")
    print("use 'mcp-app.sh oauth token' to print a bearer token for client config")


def cmd_login_passcode(args: argparse.Namespace) -> int:
    """IBM Cloud IAM passcode flow: browser IBMid sign-in, paste the code.

    Headless by design — no loopback listener, no client registration. The
    one-time passcode is exchanged at the IAM token endpoint as a public
    client; IAM requires HTTP Basic (the well-known ``bx`` CLI client) even
    though there is no secret to protect.
    """
    endpoints = _discover(args.issuer)
    passcode_url = endpoints.get("passcode_endpoint") or (
        args.issuer.rstrip("/") + "/passcode"
    )
    if args.manual:
        print("open this URL in a browser and sign in with your IBMid:")
        print(f"\n  {passcode_url}\n")
    else:
        from display_targets import open_app

        used = open_app(passcode_url, args.browser)
        print(f"mcp-app-viewer: passcode page displayed on {used}")
        print(f"mcp-app-viewer: sign in with your IBMid at {passcode_url}")
    print("paste the one-time passcode shown after sign-in:")
    try:
        import getpass

        code = getpass.getpass("> ").strip()
    except EOFError:
        raise OAuthError("no passcode on stdin") from None
    if not code:
        raise OAuthError("empty passcode")

    tokens = _http_json(
        endpoints["token_endpoint"],
        {"grant_type": PASSCODE_GRANT, "passcode": code},
        basic_auth=PASSCODE_BASIC,
    )
    store = _load_store()
    key, entry = _store_login(store, args.issuer, PASSCODE_STORE_SLOT, tokens)
    entry["flow"] = "passcode"
    _save_store(store)

    _report_login(args.issuer, entry)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    if _resolve_flow(args.issuer, getattr(args, "flow", "auto")) == "passcode":
        return cmd_login_passcode(args)
    endpoints = _discover(args.issuer)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    if args.manual:
        # Headless/SSH: the user runs the browser leg elsewhere and pastes the
        # redirect back. No loopback listener is possible there, so the URI
        # only needs to be one App ID accepts — the page will fail to load on
        # the far side, and the URL in its address bar is what we want.
        redirect_uri = f"http://127.0.0.1{CALLBACK_PATH}"
        url = _authorize_url(
            endpoints["authorization_endpoint"],
            args.client_id,
            redirect_uri,
            args.scope,
            state,
            challenge,
        )
        print("open this URL in a browser and sign in:")
        print(f"\n  {url}\n")
        print("after signing in the browser lands on an unreachable 127.0.0.1 page —")
        print("copy the FULL URL from its address bar and paste it here (or just the code):")
        try:
            import getpass

            pasted = getpass.getpass("> ").strip()
        except EOFError:
            raise OAuthError("no redirect URL on stdin") from None
        params = urllib.parse.parse_qs(
            urllib.parse.urlparse(pasted).query if "://" in pasted else ""
        )
        code = params["code"][0] if "code" in params else pasted
        got_state = params.get("state", [None])[0]
        if got_state is not None and got_state != state:
            raise OAuthError("pasted state mismatch — possible CSRF, login aborted")
        if not code:
            raise OAuthError("no authorization code in what was pasted")
    else:
        # Bind first, THEN open the browser to the URL carrying this port.
        pending = _CallbackServer(state, args.timeout)
        redirect_uri = f"http://127.0.0.1:{pending.port}{CALLBACK_PATH}"
        url = _authorize_url(
            endpoints["authorization_endpoint"],
            args.client_id,
            redirect_uri,
            args.scope,
            state,
            challenge,
        )
        from display_targets import open_app

        used = open_app(url, args.browser)
        print(f"mcp-app-viewer: sign-in page displayed on {used}")
        print(f"mcp-app-viewer: waiting for login on {redirect_uri} (Ctrl-C to abort)")
        code = pending.wait()["code"]

    tokens = _http_json(
        endpoints["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": args.client_id,
            "code_verifier": verifier,
        },
        basic_auth=(args.client_id, args.client_secret) if args.client_secret else None,
    )
    store = _load_store()
    _, entry = _store_login(store, args.issuer, args.client_id, tokens)
    _save_store(store)

    _report_login(args.issuer, entry)
    return 0


class _CallbackServer:
    """The loopback listener, bound up front so its port is known before the
    browser opens. Wraps ``_await_callback``'s handler in a server whose port
    we can read immediately."""

    def __init__(self, state: str, timeout: int) -> None:
        self._state = state
        self._timeout = timeout
        self._result: dict = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                outer._result.update(
                    {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                )
                body = (
                    b"<html><body><h3>mcp-app-viewer: login complete</h3>"
                    b"You can close this tab and return to the terminal.</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # quiet by default
                if "--verbose" in sys.argv:
                    super().log_message(fmt, *args)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]

    def wait(self) -> dict:
        deadline = time.monotonic() + self._timeout
        try:
            while not self._result and time.monotonic() < deadline:
                self._server.timeout = max(1.0, deadline - time.monotonic())
                self._server.handle_request()  # favicon hits don't end the wait
        finally:
            self._server.server_close()
        if not self._result:
            raise OAuthError(f"no login callback within {self._timeout}s")
        if self._result.get("error"):
            desc = self._result.get("error_description", self._result["error"])
            raise OAuthError(f"authorization failed: {desc}")
        if self._result.get("state") != self._state:
            raise OAuthError("callback state mismatch — possible CSRF, login aborted")
        if not self._result.get("code"):
            raise OAuthError("callback carried no authorization code")
        return self._result


def cmd_status(args: argparse.Namespace) -> int:
    store = _load_store()
    if args.issuer:
        keys = [k for k in store if k.startswith(args.issuer.rstrip("/") + "|")]
    else:
        keys = sorted(store)
    if not keys:
        print("no stored logins")
        return 0
    for key in keys:
        issuer, _, rest = key.partition("|")
        client_id, _, _ = rest.partition("|")
        entry = store[key]
        expires_at = entry.get("expires_at", 0)
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(expires_at))
        live = "valid" if expires_at - REFRESH_MARGIN_S > time.time() else "expired"
        print(f"{issuer}")
        print(f"  client id   : {client_id}")
        if entry.get("flow"):
            print(f"  flow        : {entry['flow']}")
        account = _entry_account(entry)
        if account:
            print(f"  account     : {account}")
        print(f"  subject     : {entry.get('subject', '?')}")
        if entry.get("email"):
            print(f"  email       : {entry['email']}")
        print(f"  access token: {live} (expiry {when})")
        print(f"  refresh     : {'yes' if entry.get('refresh_token') else 'no'}")
    return 0


def _client_slot(args: argparse.Namespace) -> str:
    """Token-store client slot for this invocation (constant for passcode)."""
    if _resolve_flow(args.issuer, getattr(args, "flow", "auto")) == "passcode":
        return PASSCODE_STORE_SLOT
    return args.client_id


def cmd_token(args: argparse.Namespace) -> int:
    store = _load_store()
    client_id = _client_slot(args)
    key, entry, refreshed = _ensure_valid(
        args.issuer, client_id, store, args.client_secret, getattr(args, "account", None)
    )
    if refreshed:
        store[key] = entry
        _save_store(store)
    # Printing the token IS this command — the caller pipes it into client
    # config. Everything else about the helper never shows a token value.
    print(entry["access_token"])
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    store = _load_store()
    slot = _client_slot(args)
    prefix = _store_key(args.issuer, slot)
    if not any(k == prefix or k.startswith(prefix + "|") for k in store):
        print(f"no stored login for {args.issuer}")
        return 0
    # Ambiguity between several accounts raises (exit 2) like anywhere else.
    key = _select_key(store, args.issuer, slot, getattr(args, "account", None))
    account = _entry_account(store[key])
    del store[key]
    _save_store(store)
    suffix = f" (account {account})" if account else ""
    print(f"mcp-app-viewer: removed stored login for {args.issuer}{suffix}")
    return 0


# Protocol version offered in the setup self-check's initialize handshake.
# Only used to verify reachability/auth — the real client negotiates its own.
MCP_PROTOCOL_VERSION = "2025-06-18"


def _normalize_server(raw: str) -> tuple[str, str]:
    """(base, mcp_url) from a deployment URL, with or without trailing /mcp."""
    server = raw.strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise OAuthError(f"--server must be an http(s) URL, got: {raw!r}")
    if server.endswith("/mcp"):
        return server[: -len("/mcp")], server
    return server, server + "/mcp"


def _merge_bob_mcp_json(workspace: Path, mcp_url: str, token: str) -> Path:
    """Merge the ibmcloud entry into <workspace>/.bob/mcp.json.

    Same shape Bob's own `bob mcp add <name> <url> -t http -H ...` writes
    (url + transportType + headers). Existing servers and unrelated top-level
    keys are preserved; a corrupt file is refused rather than overwritten.
    """
    path = workspace / ".bob" / "mcp.json"
    data: dict = {"mcpServers": {}}
    if path.exists():
        try:
            loaded = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            raise OAuthError(f"{path} is not valid JSON — fix or remove it first") from None
        if isinstance(loaded, dict):
            data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers["ibmcloud"] = {
        "url": mcp_url,
        "transportType": "http",
        "headers": {"Authorization": f"Bearer {token}"},
        "disabled": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parse_rpc_response(raw: str) -> dict | None:
    """Parse a JSON-RPC reply that may arrive as plain JSON or an SSE stream."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        candidates = [raw]
    else:  # text/event-stream: consider data: lines, last first
        candidates = [
            line[len("data:") :].strip()
            for line in raw.splitlines()
            if line.startswith("data:")
        ][::-1]
    for chunk in candidates:
        try:
            doc = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict) and ("result" in doc or "error" in doc):
            return doc
    return None


def _mcp_post(
    mcp_url: str, token: str, payload: dict, session_id: str | None, timeout: int = 20
) -> tuple[int, dict | None, object]:
    """One JSON-RPC POST. Returns (http_status, parsed_doc_or_None, headers)."""
    req = urllib.request.Request(
        mcp_url, data=json.dumps(payload).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", f"Bearer {token}")
    if session_id:
        req.add_header("mcp-session-id", session_id)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse_rpc_response(resp.read().decode("utf-8", "replace")), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_rpc_response(exc.read().decode("utf-8", "replace")), exc.headers
    except urllib.error.URLError as exc:
        raise OAuthError(f"{mcp_url}: {exc.reason}") from None


def _self_check(mcp_url: str, token: str) -> tuple[bool, str]:
    """initialize + tools/list against the MCP endpoint. Returns (ok, detail).

    detail is the tool count on success, an error summary otherwise.
    """
    status, doc, headers = _mcp_post(
        mcp_url,
        token,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-app-viewer-setup", "version": "1.0"},
            },
        },
        None,
    )
    if status == 401:
        return False, "initialize: HTTP 401 (token rejected)"
    if status >= 400:
        return False, f"initialize: HTTP {status}"
    if doc is not None and "error" in doc:
        return False, f"initialize: {doc['error']}"
    session_id = headers.get("mcp-session-id") if headers else None
    if session_id:
        _mcp_post(
            mcp_url,
            token,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
        )
    status, doc, _ = _mcp_post(
        mcp_url,
        token,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        session_id,
    )
    if status == 401:
        return False, "tools/list: HTTP 401 (token rejected)"
    if status >= 400:
        return False, f"tools/list: HTTP {status}"
    tools = (doc or {}).get("result", {}).get("tools") if doc else None
    if not isinstance(tools, list):
        err = (doc or {}).get("error")
        return False, f"tools/list: {err or 'unexpected response shape'}"
    return True, str(len(tools))


def _login_handoff(issuer: str, args: argparse.Namespace, why: str) -> int:
    """Print the exact human step and return the agent-detectable exit code."""
    flow = _resolve_flow(issuer, getattr(args, "flow", "auto"))
    login_cmd = f"mcp-app.sh oauth login --flow {flow} --issuer {issuer}"
    if flow != "passcode" and getattr(args, "client_id", None):
        login_cmd += f" --client-id {args.client_id}"
    print("mcp-app-viewer: no valid stored login — one human step is required.")
    print("ask the user to run:")
    print()
    print(f"  {login_cmd}")
    print()
    print(f"then re-run this setup command. ({why})")
    return 2


def cmd_setup(args: argparse.Namespace) -> int:
    """Configure an MCP client end-to-end — everything except the browser login.

    Agents can run this unaided: when no valid login is stored it prints the
    exact command a human must run and exits 2. The token itself is never
    printed; it goes straight into the client config (bob) or is referenced
    by the command that produces it (hermes/generic).
    """
    server = args.server
    if not server:
        try:
            server = input("MCP server URL (with or without trailing /mcp): ").strip()
        except EOFError:
            raise OAuthError("--server is required (stdin is not interactive)") from None
    base, mcp_url = _normalize_server(server)

    store = _load_store()
    client_id = _client_slot(args)
    try:
        key, entry, refreshed = _ensure_valid(
            args.issuer, client_id, store, args.client_secret, getattr(args, "account", None)
        )
    except OAuthError as exc:
        return _login_handoff(args.issuer, args, str(exc))
    if refreshed:
        store[key] = entry
        _save_store(store)
    token = entry["access_token"]

    redacted_entry = {
        "url": mcp_url,
        "transportType": "http",
        "headers": {"Authorization": "Bearer <redacted — `mcp-app.sh oauth token` prints it>"},
        "disabled": False,
    }
    print(f"server : {mcp_url}")
    print(f"issuer : {args.issuer}")
    if args.client == "bob":
        workspace = Path(args.workspace).resolve()
        path = _merge_bob_mcp_json(workspace, mcp_url, token)
        print(f"wrote  : {path} (merged — other mcpServers entries untouched)")
        print("entry  :")
        print(json.dumps({"mcpServers": {"ibmcloud": redacted_entry}}, indent=2, sort_keys=True))
    elif args.client == "hermes":
        print("hermes has native OAuth for MCP servers — run:")
        print(f"  hermes mcp add ibmcloud --url {mcp_url} --auth oauth")
        print("  hermes mcp login ibmcloud")
        print()
        print("bearer-header alternative (a stored login already exists here;")
        print("IAM tokens expire hourly — refresh with `mcp-app.sh oauth token`):")
        print(f"  hermes mcp add ibmcloud --url {mcp_url} --auth header")
    else:  # generic
        print("client config snippet:")
        print(json.dumps({"mcpServers": {"ibmcloud": redacted_entry}}, indent=2, sort_keys=True))
        print()
        print("verify with:")
        print(f"  curl -sS -X POST {mcp_url} \\")
        print('    -H "Authorization: Bearer $(mcp-app.sh oauth token)" \\')
        print('    -H "Content-Type: application/json" \\')
        print('    -H "Accept: application/json, text/event-stream" \\')
        print('    -d \'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\'')

    ok, detail = _self_check(mcp_url, token)
    if ok:
        print(f"self-check: PASS — tools/list returned {detail} tools")
        return 0
    print(f"self-check: FAIL — {detail}")
    if "401" in detail:
        print("the stored token was rejected; re-login and re-run setup:")
        print("  mcp-app.sh oauth login --flow passcode")
    return 1


def _watch_pass(margin_s: int, account: str | None = None) -> float:
    """One refresh pass over every stored login. Returns how long to sleep
    before the next pass: min(WATCH_MAX_SLEEP_S, time-to-nearest-refresh / 2).

    Refreshes any entry within ``margin_s`` of expiry. With ``account`` set,
    only entries bound to that IBM Cloud account are touched. A login whose
    refresh fails (revoked/expired refresh token) is reported by issuer and
    left in the store — the other logins keep being watched. Token values
    are never logged; only issuer, account, expiry timestamps, and the
    provider's error text.
    """
    store = _load_store()
    if not store:
        _warn("watch: no stored logins — run login first")
        return WATCH_MAX_SLEEP_S
    now = time.time()
    nearest_due_in = float("inf")
    for key, entry in store.items():
        issuer, _, rest = key.partition("|")
        client_id, _, _ = rest.partition("|")
        if account and _entry_account(entry) != account:
            continue
        due_in = entry.get("expires_at", 0) - margin_s - now
        if due_in > 0:
            nearest_due_in = min(nearest_due_in, due_in)
            continue
        try:
            endpoints = _discover(issuer)
            bound_account = entry.get("account")
            store[key] = _refresh(entry, endpoints["token_endpoint"], client_id)
            if bound_account:
                # Refresh preserves the entry's account binding.
                store[key]["account"] = bound_account
            # Persist immediately: consumers (token command, test harnesses)
            # read the store at any time and must not see a stale grant.
            _save_store(store)
            when = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime(store[key]["expires_at"])
            )
            label = issuer
            if _entry_account(store[key]):
                label += f" (account {_entry_account(store[key])})"
            _warn(f"watch: refreshed {label} (new expiry {when})")
            nearest_due_in = min(
                nearest_due_in, store[key]["expires_at"] - margin_s - now
            )
        except OAuthError as exc:
            _warn(f"watch: refresh failed for {issuer} — {exc}")
            _warn(f"watch: {issuer} needs a fresh login (run: oauth login --issuer {issuer})")
            # Retry the failed entry about once a minute instead of spinning.
            nearest_due_in = min(nearest_due_in, WATCH_MAX_SLEEP_S)
    if nearest_due_in == float("inf"):
        nearest_due_in = WATCH_MAX_SLEEP_S
    return min(float(WATCH_MAX_SLEEP_S), max(1.0, nearest_due_in / 2))


def cmd_watch(args: argparse.Namespace) -> int:
    """Keep every stored login fresh: refresh near-expiry tokens in a loop.

    Exists for long-running consumers (multi-hour test suites) that hold a
    bearer across many steps: IAM access tokens live 60 minutes, so a token
    fetched at the start silently expires mid-run. One watch process per user
    is enough — every login in the store is covered.
    """
    stop = threading.Event()

    def _stop(_sig: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if args.once:
        _watch_pass(args.margin, getattr(args, "account", None))
        return 0
    _warn(f"watch: refreshing stored logins within {args.margin}s of expiry; Ctrl-C to stop")
    while not stop.is_set():
        stop.wait(_watch_pass(args.margin, getattr(args, "account", None)))
    _warn("watch: stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, need_client: bool = True) -> None:
        p.add_argument(
            "--issuer",
            default=os.environ.get("MCP_OAUTH_ISSUER"),
            help="OIDC issuer URL (default: $MCP_OAUTH_ISSUER)",
        )
        if need_client:
            p.add_argument(
                "--client-id",
                default=os.environ.get("MCP_OAUTH_CLIENT_ID"),
                help="registered public client id (default: $MCP_OAUTH_CLIENT_ID)",
            )
            p.add_argument(
                "--client-secret",
                default=os.environ.get("MCP_OAUTH_CLIENT_SECRET"),
                help="optional client secret for providers requiring HTTP Basic at the token endpoint (default: $MCP_OAUTH_CLIENT_SECRET)",
            )
        p.add_argument(
            "--profile",
            default=os.environ.get("MCP_OAUTH_PROFILE") or os.environ.get("MCP_APP_PROFILE"),
            help="profile namespace for the OAuth token store (default: $MCP_OAUTH_PROFILE or $MCP_APP_PROFILE)",
        )

    p_login = sub.add_parser("login", help="browser sign-in, store tokens")
    common(p_login)
    p_login.add_argument("--scope", default="openid")
    p_login.add_argument(
        "--flow",
        choices=["auto", "authcode", "passcode"],
        default="auto",
        help="login flow (default: auto — passcode when the issuer is IBM Cloud IAM)",
    )
    p_login.add_argument(
        "--manual",
        action="store_true",
        help="no browser/loopback here (headless SSH): print the URL, paste the result back",
    )
    p_login.add_argument(
        "--browser",
        default="system",
        help="display target for the sign-in page (default: system)",
    )
    p_login.add_argument("--timeout", type=int, default=LOGIN_TIMEOUT_S)
    p_login.add_argument("--verbose", action="store_true")
    p_login.set_defaults(func=cmd_login)

    p_status = sub.add_parser("status", help="show stored logins (no token values)")
    common(p_status, need_client=False)
    p_status.set_defaults(func=cmd_status)

    p_token = sub.add_parser("token", help="print a valid access token (refreshes if needed)")
    common(p_token)
    p_token.add_argument(
        "--flow",
        choices=["auto", "authcode", "passcode"],
        default="auto",
        help="login flow of the stored entry (default: auto)",
    )
    p_token.add_argument(
        "--account",
        help="IBM Cloud account GUID to select when several logins exist for the issuer",
    )
    p_token.set_defaults(func=cmd_token)

    p_logout = sub.add_parser("logout", help="delete the stored login")
    common(p_logout)
    p_logout.add_argument(
        "--flow",
        choices=["auto", "authcode", "passcode"],
        default="auto",
        help="login flow of the stored entry (default: auto)",
    )
    p_logout.add_argument(
        "--account",
        help="IBM Cloud account GUID to remove when several logins exist for the issuer",
    )
    p_logout.set_defaults(func=cmd_logout)

    p_watch = sub.add_parser(
        "watch", help="keep every stored login fresh (refresh near-expiry tokens in a loop)"
    )
    p_watch.add_argument(
        "--margin",
        type=int,
        default=WATCH_MARGIN_S,
        help=f"refresh when this many seconds from expiry (default: {WATCH_MARGIN_S})",
    )
    p_watch.add_argument(
        "--once",
        action="store_true",
        help="single refresh pass over all stored logins, then exit",
    )
    p_watch.add_argument(
        "--account",
        help="only watch logins bound to this IBM Cloud account GUID",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_setup = sub.add_parser(
        "setup",
        help="configure an MCP client end-to-end (everything except the browser login)",
    )
    common(p_setup)
    p_setup.add_argument(
        "--flow",
        choices=["auto", "authcode", "passcode"],
        default="auto",
        help="login flow of the stored entry (default: auto)",
    )
    p_setup.add_argument(
        "--client",
        choices=["bob", "hermes", "generic"],
        default="generic",
        help="which client to configure (default: generic)",
    )
    p_setup.add_argument(
        "--server",
        help="deployment URL, with or without trailing /mcp (prompted if omitted)",
    )
    p_setup.add_argument(
        "--workspace",
        default=".",
        help="bob workspace dir holding .bob/mcp.json (default: cwd)",
    )
    p_setup.set_defaults(func=cmd_setup)

    args = ap.parse_args(argv)
    if getattr(args, "profile", None):
        os.environ["MCP_OAUTH_PROFILE"] = args.profile
    # status with no --issuer lists every stored login; the rest need one.
    needs_issuer = args.command != "status"
    for opt in ("issuer", "client_id"):
        if not hasattr(args, opt):
            continue
        if opt == "issuer" and not needs_issuer:
            continue
        # The passcode flow has no client registration — no client id needed.
        if (
            opt == "client_id"
            and getattr(args, "issuer", None)
            and _resolve_flow(args.issuer, getattr(args, "flow", "auto")) == "passcode"
        ):
            continue
        if not getattr(args, opt):
            env = f"MCP_OAUTH_{opt.upper()}"
            ap.error(f"--{opt.replace('_', '-')} is required (or set {env})")
    try:
        return args.func(args)
    except OAuthError as exc:
        _warn(str(exc))
        return 2
    except KeyboardInterrupt:
        _warn("aborted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
