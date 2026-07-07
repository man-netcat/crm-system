import http.server
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .base import AuthProvider
from .providers import OAuth2Provider, PROVIDERS

CONFIG_DIR = Path(os.environ.get("EMAIL_PARSER_CONFIG_DIR", Path.home() / ".config" / "email-parser"))
TOKENS_FILE = CONFIG_DIR / "tokens.json"


def _load_tokens() -> dict:
    if TOKENS_FILE.exists():
        try:
            return json.loads(TOKENS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_tokens(tokens: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def _get_client_config(provider: OAuth2Provider) -> tuple[str, str]:
    env_prefix = provider.name.upper().replace("-", "_")
    cid = os.environ.get(f"EMAIL_PARSER_{env_prefix}_CLIENT_ID") or provider.client_id
    csec = os.environ.get(f"EMAIL_PARSER_{env_prefix}_CLIENT_SECRET") or provider.client_secret

    if not cid:
        print(
            f"No OAuth2 client ID configured for '{provider.name}'.\n"
            f"Set these env vars:\n"
            f"  EMAIL_PARSER_{env_prefix}_CLIENT_ID=xxx\n"
            f"  EMAIL_PARSER_{env_prefix}_CLIENT_SECRET=xxx\n"
        )
    return cid, csec


def _resolve_url(url: str, provider: OAuth2Provider) -> str:
    if "{tenant}" in url:
        return url.replace("{tenant}", provider.tenant or "common")
    return url


def _start_local_server() -> tuple[int, str, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    state = secrets.token_urlsafe(32)
    code: list[str] = []
    server_ref: list[http.server.HTTPServer | None] = [None]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if "error" in params:
                self._respond(400, f"Authorization denied: {params['error'][0]}")
                threading_shutdown()
                return

            if params.get("state", [None])[0] != state:
                self._respond(403, "State mismatch — aborting.")
                threading_shutdown()
                return

            if "code" in params:
                code.append(params["code"][0])
                self._respond(200, "Authorized! You may close this tab.")
                threading_shutdown()
                return

            self._respond(400, "No authorization code received.")
            threading_shutdown()

        def _respond(self, status: int, body: str):
            self.send_response(status)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, fmt, *args):
            pass

        def threading_shutdown(self):
            import threading
            srv = server_ref[0]
            if srv:
                threading.Thread(target=srv.shutdown, daemon=True).start()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server_ref[0] = server
    return port, state, code, server


def _localhost_redirect_flow(provider: OAuth2Provider, client_id: str, client_secret: str) -> dict:
    port, state, code, server = _start_local_server()
    redirect_uri = f"http://127.0.0.1:{port}"

    auth_url = _resolve_url(provider.auth_url, provider)
    auth_url += "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })

    print(f"\n  Opening browser to authorize access...")
    print(f"  If the browser doesn't open, visit:\n  {auth_url}")
    webbrowser.open(auth_url)

    server.timeout = 300
    while not code:
        try:
            server.handle_request()
        except TimeoutError:
            break

    server.server_close()

    if not code:
        raise RuntimeError("Authorization timed out — no code received within 5 minutes.")

    token_url = _resolve_url(provider.token_url, provider)
    data = urllib.parse.urlencode({
        "code": code[0],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(token_url, data=data, method="POST")
    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Token exchange failed ({e.code}): {body}")

    print("  Authorized!")
    return json.loads(resp.read().decode())


class OAuth2DeviceAuth(AuthProvider):
    def __init__(self, provider_name: str, email: str | None = None):
        self.provider = PROVIDERS.get(provider_name)
        if not self.provider:
            raise ValueError(f"Unknown provider '{provider_name}'. Available: {list(PROVIDERS.keys())}")

        self._email = email or ""
        self._access_token = ""
        self._refresh_token = ""
        self._expires_at = 0.0

        self._load_or_authorize()

    def _load_or_authorize(self):
        tokens = _load_tokens()
        provider_tokens = tokens.get(self.provider.name, {})

        if self._email and self._email in provider_tokens:
            entry = provider_tokens[self._email]
            self._access_token = entry.get("access_token", "")
            self._refresh_token = entry.get("refresh_token", "")
            self._expires_at = entry.get("expires_at", 0.0)
            if self._access_token:
                return

        if not self._email:
            self._email = input("Email address for this account: ").strip()

        cid, csec = _get_client_config(self.provider)
        if not cid:
            raise RuntimeError(
                f"No client ID configured for '{self.provider.name}'. "
                f"Set EMAIL_PARSER_{self.provider.name.upper()}_CLIENT_ID and "
                f"EMAIL_PARSER_{self.provider.name.upper()}_CLIENT_SECRET."
            )

        token_info = _localhost_redirect_flow(self.provider, cid, csec)
        self._access_token = token_info.get("access_token", "")
        self._refresh_token = token_info.get("refresh_token", "")
        expires_in = token_info.get("expires_in", 3600)
        self._expires_at = time.time() + expires_in

        self._persist()

    def _persist(self):
        tokens = _load_tokens()
        provider_tokens = tokens.setdefault(self.provider.name, {})
        provider_tokens[self._email] = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "email": self._email,
        }
        _save_tokens(tokens)

    def _ensure_valid_token(self):
        if time.time() >= self._expires_at - 60:
            if self._refresh_token:
                self._refresh()
            else:
                raise RuntimeError("Token expired and no refresh token available. Re-run 'connect'.")

    def _refresh(self):
        cid, csec = _get_client_config(self.provider)
        if not cid or not csec:
            raise RuntimeError("Cannot refresh token: no client credentials configured.")

        token_url = _resolve_url(self.provider.token_url, self.provider)
        data = urllib.parse.urlencode({
            "client_id": cid,
            "client_secret": csec,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }).encode()

        req = urllib.request.Request(token_url, data=data, method="POST")
        try:
            resp = urllib.request.urlopen(req)
            token_info = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"Token refresh failed ({e.code}): {body}")

        self._access_token = token_info.get("access_token", self._access_token)
        if "refresh_token" in token_info:
            self._refresh_token = token_info["refresh_token"]
        expires_in = token_info.get("expires_in", 3600)
        self._expires_at = time.time() + expires_in
        self._persist()

    def get_username(self) -> str:
        return self._email

    def get_password(self) -> str:
        self._ensure_valid_token()
        return self._access_token

    def get_authenticator(self):
        self._ensure_valid_token()

        def xoauth2(challenge: bytes) -> bytes:
            sasl = f"user={self._email}\x01auth=Bearer {self._access_token}\x01\x01"
            return sasl.encode()

        return xoauth2

    def name(self) -> str:
        return self.provider.name

    @property
    def imap_server(self) -> str:
        return self.provider.imap_server

    @property
    def imap_port(self) -> int:
        return self.provider.imap_port

    @property
    def use_ssl(self) -> bool:
        return self.provider.use_ssl
