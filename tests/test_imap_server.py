#!/usr/bin/env python3
"""Minimal IMAP server for testing — serves .eml files from a local directory."""

import asyncio
import os
import re
from pathlib import Path

INBOX_DIR = Path(__file__).parent / "inbox"
HOST = "127.0.0.1"
PORT = 11437


class IMAPSession:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.authenticated = False
        self.selected = None

    async def send(self, msg):
        self.writer.write((msg + "\r\n").encode())
        await self.writer.drain()

    async def tag_reply(self, tag, status, msg):
        await self.send(f"{tag} {status} {msg}")

    @property
    def messages(self):
        files = sorted(INBOX_DIR.glob("*.eml"))
        return [(i + 1, f) for i, f in enumerate(files)]

    async def handle(self):
        await self.send("* OK [CAPABILITY IMAP4rev1 LOGIN] Test IMAP server ready")
        while True:
            line = await self.reader.readline()
            if not line:
                break
            line = line.decode().strip()
            if not line:
                continue

            tag, cmd, *args = line.split()
            cmd = cmd.upper()

            if cmd == "CAPABILITY":
                await self.tag_reply(tag, "OK", "CAPABILITY completed")
            elif cmd == "LOGIN":
                self.authenticated = True
                await self.tag_reply(tag, "OK", "LOGIN completed")
            elif cmd == "SELECT":
                mbox = args[0].strip('"') if args else "INBOX"
                self.selected = mbox
                total = len(self.messages)
                await self.send(f"* {total} EXISTS")
                await self.send(f"* 0 RECENT")
                await self.tag_reply(tag, "OK", "[READ-WRITE] SELECT completed")
            elif cmd == "SEARCH":
                ids = [str(m[0]) for m in self.messages]
                await self.send(f"* SEARCH {' '.join(ids)}")
                await self.tag_reply(tag, "OK", "SEARCH completed")
            elif cmd == "FETCH":
                msg_id = int(args[0])
                parts = " ".join(args[1:])
                match = re.match(r"\(?RFC822(?:\.(?:HEADER|TEXT|PEEK))?\)?", parts, re.IGNORECASE)
                if not match:
                    await self.tag_reply(tag, "OK", "FETCH completed (unsupported section)")
                    continue
                for mid, path in self.messages:
                    if mid == msg_id:
                        raw = path.read_bytes()
                        await self.send(f"* {msg_id} FETCH (RFC822 {{{len(raw)}}}")
                        self.writer.write(raw + b")\r\n")
                        await self.writer.drain()
                        break
                await self.tag_reply(tag, "OK", "FETCH completed")
            elif cmd == "LOGOUT":
                await self.send("* BYE IMAP server closing")
                await self.tag_reply(tag, "OK", "LOGOUT completed")
                break
            else:
                await self.tag_reply(tag, "BAD", f"Unknown command: {cmd}")
        self.writer.close()


async def main():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    server = await asyncio.start_server(
        lambda r, w: asyncio.create_task(IMAPSession(r, w).handle()),
        HOST, PORT,
    )
    print(f"Test IMAP server on {HOST}:{PORT}")
    print(f"Inbox dir: {INBOX_DIR}/")
    print(f"Drop .eml files into {INBOX_DIR}/ then run the watch command.")
    print()

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
