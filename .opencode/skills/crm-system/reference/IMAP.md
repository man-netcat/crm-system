# IMAP Integration Reference

## IMAPWatcher Class (`email_input.py:81-146`)

- Connects via `imaplib.IMAP4_SSL` (port 993) or `imaplib.IMAP4` (port 143) depending on `use_ssl`
- Polls `INBOX` on configurable interval (default 60s)
- Tracks `seen_ids` set to avoid re-processing
- Calls `on_email` callback for each new email
- Callback receives dict: `{id, subject, from, date, body}`

```python
watcher = IMAPWatcher(
    server="127.0.0.1", user="test", password="test",
    port=143, use_ssl=False, interval=3,
    on_email=process_email,
)
watcher.run()
```

## Test IMAP Server (`tests/test_imap_server.py`)

- Minimal async IMAP server on `127.0.0.1:11437`
- Serves `.eml` files from `tests/inbox/` directory
- Supports: `CAPABILITY`, `LOGIN`, `SELECT`, `SEARCH`, `FETCH`, `LOGOUT`
- Clean shutdown via SIGINT/SIGTERM signal handler
- Uses asyncio (`asyncio.start_server`)

### IMAP FETCH Format (Critical!)

**Python 3.14's `imaplib`** requires the closing `)` to follow immediately after the literal bytes — NOT on its own line after `\r\n`.

✅ Correct:
```python
self.writer.write(raw + b")\r\n")
```

❌ Wrong (breaks imaplib parsing):
```python
self.writer.write(raw + b"\r\n")
# later...
self.writer.write(b")\r\n")
```

### Usage

```bash
# Terminal 1: start server
python3 tests/test_imap_server.py

# Terminal 2: watch for new emails
python3 -m email_parser.cli watch schema.yaml \
  --server 127.0.0.1 --user test --password test \
  --port 11437 --no-ssl --interval 3
```

## .eml File Format

Plain RFC822 text files (no MIME boundaries needed). Example:

```
From: sender@example.com
Subject: Test Email
Date: Mon, 1 Jan 2024 12:00:00 +0000
Content-Type: text/plain

Body text here.
```

## `_get_body()` (`email_input.py:25-41`)

- Handles multipart and single-part messages
- Prefers `text/plain` part in multipart emails
- Uses `get_payload(decode=True)` + explicit UTF-8 decode to handle missing charset headers
- Returns empty string if no viable text body found

## fetch_imap_emails() (`email_input.py:44-78`)

- One-shot IMAP fetch for `UNSEEN` or `ALL` messages
- Returns list of `{id, subject, from, date, body}` dicts
- Uses `IMAP4_SSL` only (no `use_ssl` parameter)
