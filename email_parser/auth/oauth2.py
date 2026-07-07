import base64
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
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

    if not cid and provider.name == "gmail":
        print(
            "No Google OAuth2 client ID configured.\n"
            "Set these env vars, or create a project at https://console.cloud.google.com/apis/credentials\n"
            "  EMAIL_PARSER_GMAIL_CLIENT_ID=xxx.apps.googleusercontent.com\n"
            "  EMAIL_PARSER_GMAIL_CLIENT_SECRET=GOCSPX-xxx\n"
        )
    elif not cid:
        print(f"No OAuth2 client config found for '{provider.name}'.\n"
              f"Set EMAIL_PARSER_{env_prefix}_CLIENT_ID and EMAIL_PARSER_{env_prefix}_CLIENT_SECRET.")

    return cid, csec


def _device_code_flow(provider: OAuth2Provider, client_id: str, client_secret: str) -> dict:
    auth_url = provider.device_auth_url
    if "{tenant}" in auth_url:
        auth_url = auth_url.replace("{tenant}", provider.tenant or "common")

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": " ".join(provider.scopes),
    }).encode()

    req = urllib.request.Request(auth_url, data=data, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        device_info = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Device auth request failed ({e.code}): {body}")

    print(f"\n  Visit: {device_info.get('verification_url', device_info.get('verification_uri', ''))}")
    print(f"  Enter code: {device_info['user_code']}")
    print(f"\nWaiting for authorization... (check your browser)")

    device_code = device_info["device_code"]
    interval = device_info.get("interval", 5)
    expires_in = device_info.get("expires_in", 600)

    token_url = provider.token_url
    if "{tenant}" in token_url:
        token_url = token_url.replace("{tenant}", provider.tenant or "common")

    start = time.time()
    while time.time() - start < expires_in:
        time.sleep(interval)

        poll_data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()

        req = urllib.request.Request(token_url, data=poll_data, method="POST")
        try:
            resp = urllib.request.urlopen(req)
            token_info = json.loads(resp.read().decode())
            print("  Authorized!")
            return token_info
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode())
            error = body.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "expired_token":
                raise RuntimeError("Device code expired. Restart the authorization flow.")
            elif error == "access_denied":
                raise RuntimeError("Authorization denied by user.")
            elif error == "invalid_client":
                raise RuntimeError(f"Invalid OAuth2 client credentials for '{provider.name}'.")
            else:
                raise RuntimeError(f"Token request failed: {body}")

    raise RuntimeError("Authorization timed out.")


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

        token_info = _device_code_flow(self.provider, cid, csec)
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

        token_url = self.provider.token_url
        if "{tenant}" in token_url:
            token_url = token_url.replace("{tenant}", self.provider.tenant or "common")

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
