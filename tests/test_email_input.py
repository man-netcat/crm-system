"""Unit tests for email_input.py — body extraction from .eml files."""

import email
import email.policy
from pathlib import Path

from email_parser.email_input import from_text, from_eml, _get_body

HERE = Path(__file__).parent
EDGE = HERE / "edge_cases"


def _make_msg(body: str, content_type: str = "text/plain", charset: str | None = "utf-8") -> email.message.Message:
    msg = email.message.Message()
    if charset:
        msg.set_payload(body.encode(charset), charset=charset)
    else:
        msg.set_payload(body)
    msg["Content-Type"] = f'{content_type}; charset="{charset or "us-ascii"}"'
    return msg


def test_from_text():
    assert from_text("  hello world  ") == "hello world"
    assert from_text("") == ""
    assert from_text("\n\n") == ""


def test_from_eml_simple():
    body = from_eml(EDGE / "company_only.eml")
    assert "MegaCorp Industries" in body
    assert "jane@megacorp.com" in body


def test_from_eml_unicode():
    body = from_eml(EDGE / "unicode.eml")
    assert "café" in body
    assert "José" in body
    assert "€" in body
    assert "Zürich" in body


def test_from_eml_empty_body():
    body = from_eml(EDGE / "empty.eml")
    assert body == ""


def test_get_body_plain_text():
    msg = _make_msg("Hello World")
    assert _get_body(msg) == "Hello World"


def test_get_body_multipart():
    msg = email.message.Message()
    msg["Content-Type"] = "multipart/alternative; boundary=xyz"
    part = email.message.Message()
    part.set_payload("plain text")
    part["Content-Type"] = "text/plain; charset=utf-8"
    msg.attach(part)
    html_part = email.message.Message()
    html_part.set_payload("<html><body>HTML</body></html>")
    html_part["Content-Type"] = "text/html; charset=utf-8"
    msg.attach(html_part)
    assert _get_body(msg) == "plain text"


def test_get_body_multipart_no_text():
    """Should return empty if no text/plain part exists."""
    msg = email.message.Message()
    msg["Content-Type"] = "multipart/alternative; boundary=xyz"
    html_part = email.message.Message()
    html_part.set_payload("<html><body>HTML</body></html>")
    html_part["Content-Type"] = "text/html; charset=utf-8"
    msg.attach(html_part)
    assert _get_body(msg) == ""


def test_get_body_no_charset():
    """Should handle .eml files without charset header."""
    raw = b"From: x@x.com\nTo: y@y.com\nSubject: test\n\ncaf\xc3\xa9 = coffee"
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = _get_body(msg)
    assert "café" in body


def test_from_eml_garbage():
    body = from_eml(EDGE / "garbage.eml")
    assert len(body) > 0


def test_from_eml_recipe():
    body = from_eml(EDGE / "recipe.eml")
    assert "spaghetti" in body
    assert "pancetta" in body


def test_get_body_truncated_return():
    """_get_body should handle the case where get_payload returns str."""
    msg = email.message.Message()
    msg.set_payload("already a string")
    assert _get_body(msg) == "already a string"
