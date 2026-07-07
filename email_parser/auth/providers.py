"""Provider configurations for OAuth2 IMAP services."""

from dataclasses import dataclass


@dataclass
class OAuth2Provider:
    name: str
    imap_server: str
    imap_port: int
    use_ssl: bool
    device_auth_url: str
    token_url: str
    scopes: list[str]
    client_id: str
    client_secret: str
    tenant: str | None = None


# Built-in defaults.
# Users can override client_id/client_secret via env vars:
#   EMAIL_PARSER_<PROVIDER>_CLIENT_ID
#   EMAIL_PARSER_<PROVIDER>_CLIENT_SECRET
# Or set them in ~/.config/email-parser/config.json

GOOGLE = OAuth2Provider(
    name="gmail",
    imap_server="imap.gmail.com",
    imap_port=993,
    use_ssl=True,
    device_auth_url="https://oauth2.googleapis.com/device/code",
    token_url="https://oauth2.googleapis.com/token",
    scopes=["https://mail.google.com/"],
    client_id="",
    client_secret="",
)

MICROSOFT = OAuth2Provider(
    name="outlook",
    imap_server="outlook.office365.com",
    imap_port=993,
    use_ssl=True,
    device_auth_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode",
    token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
    scopes=[
        "https://outlook.office.com/IMAP.AccessAsUser.All",
        "offline_access",
    ],
    client_id="",
    client_secret="",
    tenant="common",
)

PROVIDERS: dict[str, OAuth2Provider] = {
    "gmail": GOOGLE,
    "outlook": MICROSOFT,
}
