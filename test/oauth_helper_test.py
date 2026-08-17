#!/usr/bin/env python3
"""Unit tests for bin/mcp_oauth_login.py against a stub OIDC issuer.

Runnable directly:

    python3 test/oauth_helper_test.py

No third-party packages. The stub issuer implements the two endpoints the
helper discovers (openid-configuration, token) and verifies PKCE the way a
real authorization server does: the challenge captured from the authorize URL
must hash to the verifier presented at the token endpoint. A fake ``open_app``
plays the browser — it reads the authorize URL the helper built and drives
the loopback redirect a signed-in user would land on.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

_TMP = Path(tempfile.mkdtemp(prefix="mcp-oauth-test-"))
os.environ["MCP_OAUTH_STORE"] = str(_TMP / "oauth-tokens.json")

import display_targets  # noqa: E402
import mcp_oauth_login as helper  # noqa: E402

_RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail and not ok else ''}")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _fake_jwt(claims: dict) -> str:
    return f"{_b64url(b'{}')}.{_b64url(json.dumps(claims).encode())}.sig"


class StubIssuer:
    """A minimal OIDC issuer: discovery + token endpoint with PKCE checks.

    Also stubs IBM Cloud IAM's passcode grant: HTTP Basic ``bx:bx`` (the
    public CLI client) is REQUIRED, the one-time passcode must match, and
    passcode-issued refresh tokens must be refreshed without a client_id
    form field — the shapes the real IAM endpoint enforces (verified live:
    bogus passcode -> 400 invalid-grant-class, not invalid_client).
    """

    VALID_PASSCODE = "valid-passcode"

    def __init__(self) -> None:
        self.expected_challenge: str | None = None
        self.codes_issued: list[str] = []
        self.refresh_tokens: list[str] = []
        self.access_tokens: list[str] = []
        self.token_requests: list[dict] = []
        self.passcode_refresh_tokens: set[str] = set()
        # When set, issued access tokens carry this IBM Cloud account claim.
        self.account: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # OIDC Discovery appends the well-known path to the issuer,
                # path component included (as App ID itself serves it).
                if self.path.endswith("/.well-known/openid-configuration"):
                    self._json(
                        {
                            "issuer": outer.issuer,
                            "authorization_endpoint": outer.issuer + "/authorization",
                            "token_endpoint": outer.issuer + "/token",
                            "passcode_endpoint": outer.issuer + "/passcode",
                        }
                    )
                else:
                    self.send_error(404)

            def _basic_ok(self) -> bool:
                expected = "Basic " + base64.b64encode(b"bx:bx").decode()
                return self.headers.get("Authorization", "") == expected

            def _issue_tokens(self, passcode: bool):
                claims = {"sub": "user-123", "email": "u@example.com",
                          "seq": len(outer.access_tokens),
                          "exp": int(time.time()) + 3600}
                if outer.account:
                    claims["account"] = {"bss": outer.account}
                outer.access_tokens.append(_fake_jwt(claims))
                outer.refresh_tokens.append(f"refresh-{len(outer.refresh_tokens)}")
                if passcode:
                    outer.passcode_refresh_tokens.add(outer.refresh_tokens[-1])
                self._json({
                    "access_token": outer.access_tokens[-1],
                    "refresh_token": outer.refresh_tokens[-1],
                    "token_type": "Bearer",
                    "expires_in": 3600,
                })

            def do_POST(self):
                if not self.path.endswith("/token"):
                    self.send_error(404)
                    return
                form = urllib.parse.parse_qs(
                    self.rfile.read(int(self.headers["Content-Length"])).decode()
                )
                data = {k: v[0] for k, v in form.items()}
                outer.token_requests.append(data)
                grant = data.get("grant_type")
                if grant == "authorization_code":
                    if data.get("code") not in outer.codes_issued:
                        return self._json({"error": "invalid_grant"}, 400)
                    digest = hashlib.sha256(
                        data.get("code_verifier", "").encode("ascii")
                    ).digest()
                    if _b64url(digest) != outer.expected_challenge:
                        return self._json(
                            {"error": "invalid_grant",
                             "error_description": "PKCE verification failed"}, 400)
                    outer.access_tokens.append(
                        _fake_jwt({"sub": "user-123", "email": "u@example.com",
                                   "exp": int(time.time()) + 3600}))
                    outer.refresh_tokens.append(f"refresh-{len(outer.refresh_tokens)}")
                    self._json({
                        "access_token": outer.access_tokens[-1],
                        "refresh_token": outer.refresh_tokens[-1],
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "id_token": _fake_jwt({"sub": "user-123",
                                               "email": "u@example.com"}),
                    })
                elif grant == "urn:ibm:params:oauth:grant-type:passcode":
                    if not self._basic_ok():
                        return self._json(
                            {"error": "invalid_client",
                             "error_description": "No authorization header found."}, 401)
                    if data.get("passcode") != outer.VALID_PASSCODE:
                        return self._json(
                            {"error": "invalid_grant",
                             "error_description": "Provided passcode is invalid."}, 400)
                    self._issue_tokens(passcode=True)
                elif grant == "refresh_token":
                    if data.get("refresh_token") not in outer.refresh_tokens:
                        return self._json(
                            {"error": "invalid_grant",
                             "error_description": "unknown refresh token"}, 400)
                    if data["refresh_token"] in outer.passcode_refresh_tokens:
                        # Passcode-issued tokens refresh as the public bx
                        # client: Basic bx:bx, and no client_id form field.
                        if not self._basic_ok() or "client_id" in data:
                            return self._json(
                                {"error": "invalid_client",
                                 "error_description": "bad passcode refresh shape"}, 401)
                        self._issue_tokens(passcode=True)
                        return
                    outer.access_tokens.append(
                        _fake_jwt({"sub": "user-123", "exp": int(time.time()) + 3600}))
                    outer.refresh_tokens.append(f"refresh-{len(outer.refresh_tokens)}")
                    self._json({
                        "access_token": outer.access_tokens[-1],
                        "refresh_token": outer.refresh_tokens[-1],
                        "expires_in": 3600,
                    })
                else:
                    self._json({"error": "unsupported_grant_type"}, 400)

            def _json(self, payload: dict, status: int = 200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # quiet
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.issuer = f"http://127.0.0.1:{self.port}/oauth/v4/tenant"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _args(**kw) -> object:
    from types import SimpleNamespace

    payload = {
        "issuer": kw.pop("issuer", STUB.issuer),
        "client_id": kw.pop("client_id", "client-abc"),
        "client_secret": kw.pop("client_secret", None),
        "scope": "openid",
        "flow": kw.pop("flow", "auto"),
        "manual": False,
        "browser": "none",
        "timeout": 30,
        "account": kw.pop("account", None),
        "profile": kw.pop("profile", None),
    }
    payload.update(kw)
    return SimpleNamespace(**payload)


def _play_browser(url: str, *rest, **kw) -> str:
    """Fake display_targets.open_app: drive the redirect a login would cause."""
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    STUB.expected_challenge = params["code_challenge"][0]
    redirect_uri = params["redirect_uri"][0]
    state = params["state"][0]
    code = f"code-{len(STUB.codes_issued)}"
    STUB.codes_issued.append(code)
    cb = f"{redirect_uri}?{urllib.parse.urlencode({'code': code, 'state': state})}"
    threading.Thread(target=lambda: urllib.request.urlopen(cb, timeout=10),
                     daemon=True).start()
    return "stub-browser"


STUB = StubIssuer()
_ORIG_OPEN_APP = display_targets.open_app


def test_pkce_pair() -> None:
    verifier, challenge = helper._pkce_pair()
    ok = (
        43 <= len(verifier) <= 128
        and challenge == _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    )
    check("pkce: verifier length and S256 challenge", ok)


def test_store_permissions() -> None:
    helper._save_store({"k": {"access_token": "x"}})
    mode = oct(os.stat(helper.token_store()).st_mode & 0o777)
    check("store: file mode 0600", mode == "0o600", f"mode={mode}")
    check("store: round-trips", helper._load_store() == {"k": {"access_token": "x"}})
    helper.token_store().unlink()


def test_profile_scoped_store_path() -> None:
    old_store = os.environ.pop("MCP_OAUTH_STORE", None)
    old_profile = os.environ.get("MCP_OAUTH_PROFILE")
    old_cfg = os.environ.get("MCP_APP_CONFIG_DIR")
    profile_root = _TMP / "profile-config"
    os.environ["MCP_APP_CONFIG_DIR"] = str(profile_root)
    os.environ["MCP_OAUTH_PROFILE"] = "hermes-work"
    try:
        expected = profile_root / "profiles" / "hermes-work" / "oauth-tokens.json"
        check("profile store: derived from MCP_APP_CONFIG_DIR + profile",
              helper.token_store() == expected,
              f"got={helper.token_store()} expected={expected}")
    finally:
        if old_store is not None:
            os.environ["MCP_OAUTH_STORE"] = old_store
        if old_profile is None:
            os.environ.pop("MCP_OAUTH_PROFILE", None)
        else:
            os.environ["MCP_OAUTH_PROFILE"] = old_profile
        if old_cfg is None:
            os.environ.pop("MCP_APP_CONFIG_DIR", None)
        else:
            os.environ["MCP_APP_CONFIG_DIR"] = old_cfg


def test_login_status_token_logout() -> None:
    display_targets.open_app = _play_browser
    try:
        rc = helper.cmd_login(_args())
    finally:
        display_targets.open_app = _ORIG_OPEN_APP
    check("login: completes against stub issuer", rc == 0)

    store = helper._load_store()
    entry = store.get(helper._store_key(STUB.issuer, "client-abc"), {})
    check("login: stores access + refresh token",
          bool(entry.get("access_token")) and bool(entry.get("refresh_token")))
    check("login: subject from claims", entry.get("subject") == "user-123")

    rc = helper.cmd_status(_args())
    check("status: runs", rc == 0)

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args())
    got = buf.getvalue().strip()
    check("token: prints the stored access token",
          rc == 0 and got == STUB.access_tokens[-1])

    rc = helper.cmd_logout(_args())
    check("logout: removes the entry",
          rc == 0 and helper._store_key(STUB.issuer, "client-abc") not in helper._load_store())


def test_refresh_when_expired() -> None:
    # Seed a login, then age the access token past expiry.
    display_targets.open_app = _play_browser
    try:
        helper.cmd_login(_args())
    finally:
        display_targets.open_app = _ORIG_OPEN_APP
    before_access = STUB.access_tokens[-1]
    before_refresh = STUB.refresh_tokens[-1]

    store = helper._load_store()
    key = helper._store_key(STUB.issuer, "client-abc")
    store[key]["expires_at"] = int(time.time()) - 10
    helper._save_store(store)

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args())
    got = buf.getvalue().strip()
    check("refresh: expired token triggers refresh grant",
          rc == 0 and got != before_access and got == STUB.access_tokens[-1])
    grants = [r["grant_type"] for r in STUB.token_requests]
    check("refresh: used the refresh_token grant", "refresh_token" in grants)
    entry = helper._load_store()[key]
    check("refresh: rotated refresh token persisted",
          entry["refresh_token"] != before_refresh)
    check("refresh: future expiry restored",
          entry["expires_at"] > time.time() + 3000)
    helper.cmd_logout(_args())


def test_state_mismatch_rejected() -> None:
    def bad_browser(url: str, *a, **kw) -> str:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        redirect_uri = params["redirect_uri"][0]
        STUB.expected_challenge = params["code_challenge"][0]
        cb = f"{redirect_uri}?code=evil&state=wrong-state"
        threading.Thread(target=lambda: urllib.request.urlopen(cb, timeout=10),
                         daemon=True).start()
        return "stub-browser"

    display_targets.open_app = bad_browser
    try:
        try:
            helper.cmd_login(_args())
            rc, err = 0, None
        except helper.OAuthError as exc:
            rc, err = None, str(exc)
    finally:
        display_targets.open_app = _ORIG_OPEN_APP
    check("state mismatch: login aborted", err is not None and "state mismatch" in err)


def test_manual_flow_paste() -> None:
    import builtins
    import contextlib, io

    buf = io.StringIO()

    def fake_input(prompt=""):
        # The manual-mode URL was printed to stdout; parse it as the headless
        # user's browser would, then paste back the redirect it lands on.
        url = next(l.strip() for l in buf.getvalue().splitlines()
                   if l.strip().startswith(STUB.issuer + "/authorization"))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        STUB.expected_challenge = params["code_challenge"][0]
        state = params["state"][0]
        code = f"code-{len(STUB.codes_issued)}"
        STUB.codes_issued.append(code)
        return f"http://127.0.0.1{helper.CALLBACK_PATH}?code={code}&state={state}"

    import getpass

    orig_getpass = getpass.getpass
    getpass.getpass = fake_input
    try:
        with contextlib.redirect_stdout(buf):
            rc = helper.cmd_login(_args(manual=True))
    finally:
        getpass.getpass = orig_getpass
    check("manual: pasted redirect URL completes login", rc == 0,
          buf.getvalue()[-300:] if rc != 0 else "")
    check("manual: never touched a browser opener",
          "displayed on" not in buf.getvalue())
    helper.cmd_logout(_args())


def test_flow_auto_resolution() -> None:
    check(
        "flow auto: iam.cloud.ibm.com resolves to passcode",
        helper._resolve_flow("https://iam.cloud.ibm.com/identity", "auto") == "passcode",
    )
    check(
        "flow auto: other issuers resolve to authcode",
        helper._resolve_flow(STUB.issuer, "auto") == "authcode",
    )
    check(
        "flow: explicit choice wins over auto",
        helper._resolve_flow("https://iam.cloud.ibm.com/identity", "authcode") == "authcode",
    )


def _passcode_login(passcode: str = StubIssuer.VALID_PASSCODE) -> int:
    """Drive cmd_login in passcode mode: stub the browser, feed the code."""
    import builtins
    import contextlib, io

    import getpass

    orig_getpass = getpass.getpass
    getpass.getpass = lambda prompt="": passcode
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return helper.cmd_login(_args(flow="passcode", manual=True))
    finally:
        getpass.getpass = orig_getpass


def test_passcode_login() -> None:
    before = len(STUB.token_requests)
    rc = _passcode_login()
    check("passcode: login completes against stub IAM", rc == 0)

    req = STUB.token_requests[-1]
    check("passcode: sent the passcode grant",
          req.get("grant_type") == "urn:ibm:params:oauth:grant-type:passcode")
    check("passcode: no PKCE / client_id / secret in the exchange",
          len(STUB.token_requests) == before + 1
          and "code_verifier" not in req and "client_id" not in req)

    key = helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT)
    entry = helper._load_store().get(key, {})
    check("passcode: stored under the issuer|iam-passcode slot",
          bool(entry.get("access_token")) and bool(entry.get("refresh_token")))
    check("passcode: entry marked flow=passcode", entry.get("flow") == "passcode")
    check("passcode: subject from claims", entry.get("subject") == "user-123")

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode"))
    check("passcode: token prints the stored IAM token",
          rc == 0 and buf.getvalue().strip() == STUB.access_tokens[-1])

    rc = helper.cmd_logout(_args(flow="passcode"))
    check("passcode: logout removes the entry",
          rc == 0 and key not in helper._load_store())


def test_passcode_wrong_code_rejected() -> None:
    try:
        _passcode_login("not-the-passcode")
        check("passcode: invalid code is an error, not a stored login", False)
    except helper.OAuthError as exc:
        check("passcode: invalid code is an error, not a stored login",
              "passcode" in str(exc).lower())
    key = helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT)
    check("passcode: failed login stored nothing", key not in helper._load_store())


def test_passcode_refresh() -> None:
    _passcode_login()
    before_access = STUB.access_tokens[-1]

    store = helper._load_store()
    key = helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT)
    store[key]["expires_at"] = int(time.time()) - 10
    helper._save_store(store)

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode"))
    got = buf.getvalue().strip()
    # The stub 401s a passcode refresh carrying client_id or missing bx:bx
    # Basic — reaching a new token proves the IAM refresh shape.
    check("passcode refresh: expired token triggers refresh grant",
          rc == 0 and got != before_access and got == STUB.access_tokens[-1])
    req = STUB.token_requests[-1]
    check("passcode refresh: refresh_token grant, no client_id",
          req.get("grant_type") == "refresh_token" and "client_id" not in req)
    helper.cmd_logout(_args(flow="passcode"))


def test_token_without_login_fails() -> None:
    try:
        helper.cmd_token(_args(client_id="never-logged-in"))
        check("token: no stored login is an error, not a crash", False)
    except helper.OAuthError:
        check("token: no stored login is an error, not a crash", True)


def _passcode_login_account(account: str) -> int:
    """Passcode login whose issued tokens carry account.bss = account."""
    STUB.account = account
    try:
        return _passcode_login()
    finally:
        STUB.account = None


def _account_key(account: str) -> str:
    return helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT, account)


def test_multi_account_store_and_status() -> None:
    import contextlib, io

    helper._save_store({})
    _passcode_login_account("acct-aaa")
    token_a = STUB.access_tokens[-1]
    _passcode_login_account("acct-bbb")
    token_b = STUB.access_tokens[-1]

    store = helper._load_store()
    key_a, key_b = _account_key("acct-aaa"), _account_key("acct-bbb")
    check("multi-account: second account login does not overwrite the first",
          key_a in store and key_b in store
          and store[key_a]["access_token"] == token_a
          and store[key_b]["access_token"] == token_b)
    check("multi-account: entry records the account bss",
          store[key_a].get("account") == "acct-aaa"
          and store[key_b].get("account") == "acct-bbb")
    check("multi-account: no legacy key left behind",
          helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT) not in store)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_status(_args(flow="passcode"))
    out = buf.getvalue()
    check("multi-account: status lists every stored account",
          rc == 0 and "acct-aaa" in out and "acct-bbb" in out)
    check("multi-account: status never prints token values",
          all(tok not in out for tok in STUB.access_tokens))
    # Leaves both accounts stored for the selection/ambiguity tests.


def test_ambiguous_accounts_exit_2() -> None:
    import contextlib, io

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = helper.main(["token", "--issuer", STUB.issuer, "--flow", "passcode"])
    err = err_buf.getvalue()
    check("ambiguity: token without --account exits 2", rc == 2)
    check("ambiguity: choices printed with instructions",
          "--account acct-aaa" in err and "--account acct-bbb" in err)
    check("ambiguity: no token value printed",
          out_buf.getvalue() == ""
          and all(tok not in err for tok in STUB.access_tokens))

    err_buf = io.StringIO()
    with contextlib.redirect_stderr(err_buf):
        rc = helper.main(["logout", "--issuer", STUB.issuer, "--flow", "passcode"])
    check("ambiguity: logout without --account also exits 2", rc == 2)
    store = helper._load_store()
    check("ambiguity: nothing was deleted",
          _account_key("acct-aaa") in store and _account_key("acct-bbb") in store)


def test_account_selection() -> None:
    import contextlib, io

    key_a, key_b = _account_key("acct-aaa"), _account_key("acct-bbb")
    token_a = helper._load_store()[key_a]["access_token"]

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode", account="acct-aaa"))
    check("account selection: --account selects that account's token",
          rc == 0 and buf.getvalue().strip() == token_a)

    rc = helper.cmd_logout(_args(flow="passcode", account="acct-aaa"))
    store = helper._load_store()
    check("account selection: logout --account removes only that entry",
          rc == 0 and key_a not in store and key_b in store)
    helper.cmd_logout(_args(flow="passcode", account="acct-bbb"))


def test_migration_from_legacy_keys() -> None:
    import contextlib, io

    legacy_key = helper._store_key(STUB.issuer, helper.PASSCODE_STORE_SLOT)
    new_key = f"{legacy_key}|acct-legacy"
    legacy_jwt = _fake_jwt({"sub": "user-legacy",
                            "account": {"bss": "acct-legacy"},
                            "exp": int(time.time()) + 3600})
    helper._save_store({legacy_key: {
        "access_token": legacy_jwt, "refresh_token": "rt-legacy",
        "expires_at": int(time.time()) + 3600, "flow": "passcode"}})

    store = helper._load_store()
    check("migration: legacy key re-keyed to issuer|slot|account on load",
          new_key in store and legacy_key not in store)
    helper._save_store(store)
    on_disk = json.loads(helper.token_store().read_text("utf-8"))
    check("migration: save writes the new key format",
          new_key in on_disk and legacy_key not in on_disk)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode"))
    check("migration: reads without --account keep working",
          rc == 0 and buf.getvalue().strip() == legacy_jwt)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode", account="acct-legacy"))
    check("migration: reads with --account find the migrated entry",
          rc == 0 and buf.getvalue().strip() == legacy_jwt)

    plain_key = helper._store_key(STUB.issuer, "client-abc")
    helper._save_store({plain_key: {"access_token": _fake_jwt({"sub": "x"}),
                                    "expires_at": int(time.time()) + 3600}})
    check("migration: entries without a bss claim keep the legacy key",
          plain_key in helper._load_store())
    helper._save_store({})


def test_refresh_preserves_account() -> None:
    import contextlib, io

    helper._save_store({})
    _passcode_login_account("acct-refresh")
    key = _account_key("acct-refresh")
    before_access = STUB.access_tokens[-1]

    store = helper._load_store()
    store[key]["expires_at"] = int(time.time()) - 10
    helper._save_store(store)

    # STUB.account is None here: the refresh response carries no account
    # claim — the binding must survive that.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_token(_args(flow="passcode", account="acct-refresh"))
    check("refresh: expired account-bound token refreshes",
          rc == 0 and buf.getvalue().strip() != before_access)
    store = helper._load_store()
    check("refresh: entry stays under the account key", key in store)
    check("refresh: account binding preserved",
          store[key].get("account") == "acct-refresh")
    helper.cmd_logout(_args(flow="passcode", account="acct-refresh"))


def test_watch_multi_account() -> None:
    import contextlib, io

    helper._save_store({})
    _passcode_login_account("acct-w1")
    token_w1 = STUB.access_tokens[-1]
    _passcode_login_account("acct-w2")
    token_w2 = STUB.access_tokens[-1]
    key_w1, key_w2 = _account_key("acct-w1"), _account_key("acct-w2")

    store = helper._load_store()
    for k in (key_w1, key_w2):
        store[k]["expires_at"] = int(time.time()) + 300  # inside the margin
    helper._save_store(store)

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=True, margin=600))
    store = helper._load_store()
    check("watch: every account entry refreshed in place",
          rc == 0 and key_w1 in store and key_w2 in store
          and store[key_w1]["access_token"] != token_w1
          and store[key_w2]["access_token"] != token_w2)
    check("watch: account bindings preserved across refresh",
          store[key_w1].get("account") == "acct-w1"
          and store[key_w2].get("account") == "acct-w2")
    check("watch: refresh log names the account, never token values",
          "acct-w1" in buf.getvalue()
          and all(tok not in buf.getvalue() for tok in STUB.access_tokens))

    # --account filters the pass to one account.
    store = helper._load_store()
    for k in (key_w1, key_w2):
        store[k]["expires_at"] = int(time.time()) + 300
    helper._save_store(store)
    before_w1 = store[key_w1]["access_token"]
    before_w2 = store[key_w2]["access_token"]
    with contextlib.redirect_stderr(io.StringIO()):
        rc = helper.cmd_watch(_args(once=True, margin=600, account="acct-w1"))
    store = helper._load_store()
    check("watch --account: only the named account is refreshed",
          rc == 0 and store[key_w1]["access_token"] != before_w1
          and store[key_w2]["access_token"] == before_w2)

    helper.cmd_logout(_args(flow="passcode", account="acct-w1"))
    helper.cmd_logout(_args(flow="passcode", account="acct-w2"))


def _seed_authcode_login() -> str:
    """Log in via the stub browser and return the entry's store key."""
    display_targets.open_app = _play_browser
    try:
        helper.cmd_login(_args())
    finally:
        display_targets.open_app = _ORIG_OPEN_APP
    return helper._store_key(STUB.issuer, "client-abc")


def test_watch_once_skips_fresh_entries() -> None:
    _seed_authcode_login()
    before = list(STUB.access_tokens)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=True, margin=600))
    check("watch --once: fresh entry left alone (no refresh grant)",
          rc == 0 and STUB.access_tokens == before)
    check("watch --once: no refresh event logged for a fresh entry",
          "refreshed" not in buf.getvalue())
    helper.cmd_logout(_args())


def test_watch_once_refreshes_near_expiry() -> None:
    key = _seed_authcode_login()
    store = helper._load_store()
    before_access = store[key]["access_token"]
    store[key]["expires_at"] = int(time.time()) + 300  # inside the 600s margin
    helper._save_store(store)

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=True, margin=600))
    entry = helper._load_store()[key]
    check("watch --once: near-expiry entry refreshed",
          rc == 0 and entry["access_token"] != before_access)
    check("watch --once: new expiry is beyond the margin",
          entry["expires_at"] > time.time() + 600)
    check("watch --once: refresh event logged (issuer, no token value)",
          f"refreshed {STUB.issuer}" in buf.getvalue()
          and entry["access_token"] not in buf.getvalue()
          and entry["refresh_token"] not in buf.getvalue())
    helper.cmd_logout(_args())


def test_watch_once_reports_refresh_failure_without_crashing() -> None:
    key = _seed_authcode_login()
    store = helper._load_store()
    store[key]["expires_at"] = int(time.time()) - 10
    store[key]["refresh_token"] = "revoked-refresh-token"
    helper._save_store(store)

    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=True, margin=600))
    out = buf.getvalue()
    check("watch --once: failed refresh reported, exit still clean",
          rc == 0 and f"refresh failed for {STUB.issuer}" in out)
    check("watch --once: names the issuer needing re-login",
          "needs a fresh login" in out and STUB.issuer in out)
    check("watch --once: failed entry left in the store",
          key in helper._load_store())
    check("watch --once: failure output carries no token values",
          all(tok not in out for tok in STUB.access_tokens))
    helper.cmd_logout(_args())


def test_watch_loop_exits_on_sigterm() -> None:
    _seed_authcode_login()
    import contextlib, io
    import signal as _signal

    def kill_soon() -> None:
        time.sleep(0.3)
        os.kill(os.getpid(), _signal.SIGTERM)

    threading.Thread(target=kill_soon, daemon=True).start()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=False, margin=600))
    check("watch loop: SIGTERM exits cleanly", rc == 0 and "stopped" in buf.getvalue())
    helper.cmd_logout(_args())


def test_watch_empty_store_is_a_warning_not_a_crash() -> None:
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = helper.cmd_watch(_args(once=True, margin=600))
    check("watch --once: empty store warns and exits 0",
          rc == 0 and "no stored logins" in buf.getvalue())


class StubMcpServer:
    """A minimal Streamable-HTTP MCP endpoint for the setup self-check.

    Requires the exact bearer the stub issuer last issued, answers
    initialize (with a session id) and tools/list; ``always_401`` models a
    server that rejects the token outright.
    """

    def __init__(self, tools: int = 3, always_401: bool = False) -> None:
        self.tools = tools
        self.always_401 = always_401
        self.saw_session_id: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, payload: dict | None, status: int = 200,
                       session: bool = False):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                if session:
                    self.send_header("mcp-session-id", "stub-session-1")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_POST(self):
                if not self.path.endswith("/mcp"):
                    self.send_error(404)
                    return
                expected = f"Bearer {STUB.access_tokens[-1]}" if STUB.access_tokens else ""
                if outer.always_401 or self.headers.get("Authorization") != expected:
                    return self._reply(None, status=401)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = body.get("method")
                if method == "initialize":
                    self._reply({"jsonrpc": "2.0", "id": body["id"], "result": {
                        "protocolVersion": helper.MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "serverInfo": {"name": "stub-mcp", "version": "0"},
                    }}, session=True)
                elif method == "notifications/initialized":
                    outer.saw_session_id = self.headers.get("mcp-session-id")
                    self._reply(None, status=202)
                elif method == "tools/list":
                    if outer.saw_session_id:
                        assert self.headers.get("mcp-session-id") == outer.saw_session_id
                    self._reply({"jsonrpc": "2.0", "id": body["id"], "result": {
                        "tools": [{"name": f"tool-{i}"} for i in range(outer.tools)]}})
                else:
                    self._reply({"jsonrpc": "2.0", "id": body.get("id"),
                                 "error": {"code": -32601, "message": "no such method"}})

            def log_message(self, *a):  # quiet
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _setup_args(mcp: StubMcpServer, **kw) -> object:
    return _args(flow="passcode", server=kw.pop("server", mcp.base),
                 client=kw.pop("client", "generic"),
                 workspace=kw.pop("workspace", "."), **kw)


def _run_setup(args: object) -> tuple[int, str]:
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = helper.cmd_setup(args)
    return rc, buf.getvalue()


def _ensure_passcode_logout() -> None:
    helper.cmd_logout(_args(flow="passcode"))


def test_setup_not_logged_in_exit_2() -> None:
    mcp = StubMcpServer()
    _ensure_passcode_logout()
    rc, out = _run_setup(_setup_args(mcp))
    check("setup: no stored login exits 2", rc == 2)
    check("setup: prints the exact human login step",
          "mcp-app.sh oauth login --flow passcode" in out)
    check("setup: hand-off stops before any config/self-check output",
          "self-check" not in out and "mcpServers" not in out)
    mcp.stop()


def test_setup_bob_writes_and_merges() -> None:
    mcp = StubMcpServer(tools=4)
    _passcode_login()
    workspace = Path(tempfile.mkdtemp(prefix="mcp-setup-bob-"))
    bob_dir = workspace / ".bob"
    bob_dir.mkdir()
    preexisting = {
        "mcpServers": {"other": {"url": "http://localhost:9999/mcp", "transportType": "http"}},
        "unrelatedKey": {"keep": True},
    }
    (bob_dir / "mcp.json").write_text(json.dumps(preexisting), encoding="utf-8")

    rc, out = _run_setup(_setup_args(mcp, client="bob", workspace=str(workspace)))
    check("setup bob: exits 0 on self-check PASS", rc == 0, out[-400:])
    check("setup bob: PASS reports the tool count",
          "PASS" in out and "4 tools" in out)

    data = json.loads((bob_dir / "mcp.json").read_text("utf-8"))
    check("setup bob: pre-existing server preserved",
          data["mcpServers"].get("other") == preexisting["mcpServers"]["other"])
    check("setup bob: unrelated top-level key preserved",
          data.get("unrelatedKey") == {"keep": True})
    entry = data["mcpServers"].get("ibmcloud", {})
    check("setup bob: entry has url/transportType/headers shape",
          entry.get("url") == mcp.base + "/mcp"
          and entry.get("transportType") == "http"
          and entry.get("disabled") is False
          and entry.get("headers", {}).get("Authorization") == f"Bearer {STUB.access_tokens[-1]}")
    check("setup bob: token never printed to stdout",
          all(tok not in out for tok in STUB.access_tokens))
    check("setup bob: session id honoured across the handshake",
          mcp.saw_session_id == "stub-session-1")
    _ensure_passcode_logout()
    mcp.stop()


def test_setup_generic_self_check_and_snippet() -> None:
    mcp = StubMcpServer(tools=2)
    _passcode_login()
    rc, out = _run_setup(_setup_args(mcp, server=mcp.base + "/mcp"))  # trailing /mcp accepted
    check("setup generic: exits 0, PASS with tool count",
          rc == 0 and "PASS" in out and "2 tools" in out, out[-400:])
    check("setup generic: prints the JSON snippet",
          '"mcpServers"' in out and '"ibmcloud"' in out and mcp.base + "/mcp" in out)
    check("setup generic: prints the curl verification command",
          "curl -sS -X POST" in out and "tools/list" in out)
    check("setup generic: token redacted, never printed",
          "redacted" in out and all(tok not in out for tok in STUB.access_tokens))
    _ensure_passcode_logout()
    mcp.stop()


def test_setup_self_check_401_prints_relogin_hint() -> None:
    mcp = StubMcpServer(always_401=True)
    _passcode_login()
    rc, out = _run_setup(_setup_args(mcp))
    check("setup: self-check 401 exits 1 with FAIL", rc == 1 and "FAIL" in out)
    check("setup: 401 prints the re-login hint",
          "401" in out and "oauth login" in out)
    _ensure_passcode_logout()
    mcp.stop()


def main() -> int:
    test_pkce_pair()
    test_store_permissions()
    test_login_status_token_logout()
    test_refresh_when_expired()
    test_state_mismatch_rejected()
    test_manual_flow_paste()
    test_flow_auto_resolution()
    test_passcode_login()
    test_passcode_wrong_code_rejected()
    test_passcode_refresh()
    test_token_without_login_fails()
    test_multi_account_store_and_status()
    test_ambiguous_accounts_exit_2()
    test_account_selection()
    test_migration_from_legacy_keys()
    test_refresh_preserves_account()
    test_watch_multi_account()
    test_watch_once_skips_fresh_entries()
    test_watch_once_refreshes_near_expiry()
    test_watch_once_reports_refresh_failure_without_crashing()
    test_watch_loop_exits_on_sigterm()
    test_watch_empty_store_is_a_warning_not_a_crash()
    test_setup_not_logged_in_exit_2()
    test_setup_bob_writes_and_merges()
    test_setup_generic_self_check_and_snippet()
    test_setup_self_check_401_prints_relogin_hint()
    STUB.stop()
    failed = [n for n, ok, _ in _RESULTS if not ok]
    print(f"\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
