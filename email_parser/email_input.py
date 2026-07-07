import email
import email.policy
import imaplib
import time
from pathlib import Path
from typing import Callable

from .auth import AuthProvider


def from_text(text: str) -> str:
    return text.strip()


def from_eml(filepath: str | Path) -> str:
    with open(filepath, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    return _get_body(msg)


def from_stdin() -> str:
    import sys

    return sys.stdin.read().strip()


def _get_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_content()
                if payload:
                    return payload
        return ""
    payload = msg.get_content()
    return payload or ""


def fetch_imap_emails(
    server: str,
    user: str,
    password: str,
    folder: str = "INBOX",
    unseen_only: bool = True,
) -> list[dict]:
    mail = imaplib.IMAP4_SSL(server)
    mail.login(user, password)
    mail.select(folder)

    search_criteria = "UNSEEN" if unseen_only else "ALL"
    status, ids = mail.search(None, search_criteria)
    email_ids = ids[0].split() if ids[0] else []

    results = []
    for eid in email_ids:
        status, data = mail.fetch(eid, "(RFC822)")
        if status != "OK":
            continue
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email, policy=email.policy.default)

        results.append(
            {
                "id": eid.decode(),
                "subject": msg.get("Subject", ""),
                "from": msg.get("From", ""),
                "date": msg.get("Date", ""),
                "body": _get_body(msg),
            }
        )

    mail.logout()
    return results


class IMAPWatcher:
    def __init__(
        self,
        server: str,
        user: str,
        password: str,
        folder: str = "INBOX",
        port: int = 993,
        use_ssl: bool = True,
        interval: int = 60,
        on_email: Callable[[dict], None] | None = None,
        auth_provider: AuthProvider | None = None,
    ):
        self.server = server
        self.user = user
        self.password = password
        self.folder = folder
        self.port = port
        self.use_ssl = use_ssl
        self.interval = interval
        self.on_email = on_email
        self.auth_provider = auth_provider
        self.seen_ids: set[bytes] = set()

    def _connect(self):
        if self.use_ssl:
            return imaplib.IMAP4_SSL(self.server, self.port)
        return imaplib.IMAP4(self.server, self.port)

    def run(self):
        proto = "IMAPS" if self.use_ssl else "IMAP"
        print(f"Watching {self.server}:{self.port}/{self.folder} via {proto} every {self.interval}s...")
        while True:
            try:
                self._check()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(self.interval)

    def _check(self):
        mail = self._connect()
        authenticator = self.auth_provider.get_authenticator() if self.auth_provider else None
        if authenticator:
            mail.authenticate("XOAUTH2", authenticator)
        else:
            mail.login(self.user, self.password)
        mail.select(self.folder)

        status, ids = mail.search(None, "ALL")
        all_ids = set(ids[0].split()) if ids[0] else set()

        new_ids = all_ids - self.seen_ids
        for eid in new_ids:
            status, data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)

            result = {
                "id": eid.decode(),
                "subject": msg.get("Subject", ""),
                "from": msg.get("From", ""),
                "date": msg.get("Date", ""),
                "body": _get_body(msg),
            }

            if self.on_email:
                self.on_email(result)

        self.seen_ids = all_ids
        mail.logout()
