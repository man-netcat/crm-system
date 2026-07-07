---
name: crm-system
description: Email-to-database extraction pipeline. Python + Click + SQLite + Ollama. Use when working on email_parser/ (CLI, schema inference, AI extraction, DB insertion, IMAP watching), tests/, schema YAML files, or the test IMAP server.
---

## Core Principles & Patterns

- **Schema inferred from prompt** — `init --prompt` replaces manual YAML authorship. AI generates 2–3 relational tables with FK constraints. Temperature 0.1, retry logic (3 attempts), post-processing (strip id columns, fix bad FK refs).
- **FK resolution via `@last:<table>`** — AI outputs `@last:companies` as FK value. Code resolves to the last-inserted row's ID. Auto-fill fallback when AI omits the marker and parent table has exactly 1 row.
- **Certainty/spam scoring** — AI returns `{certainty: 0–1, spam: 0–1, extracted: {...}}`. CLI filters with `--min-certainty` / `--max-spam`.
- **Graceful degradation** — Rows violating NOT NULL / FK constraints are skipped with a warning, not crashed. `@last:` on non-FK columns → null. Empty-body emails → skipped silently. Bulk-skip rows with unresolvable `@last:` references.
- **Model choice matters** — `llama3.2` (3B) hallucinates `@last:` placeholders on non-FK columns. Minimum viable: `llama3.1:8b`.
- **No OAuth2 / Gmail** — All auth is plain IMAP via `--server/--user/--password` + `--no-ssl`/`--port`. Test server uses plain auth.
- **Test IMAP server** — `tests/test_imap_server.py` serves `.eml` files from `tests/inbox/` on port 11437, plain IMAP only, signal-handled clean shutdown.
- **No hardcoded schema fixtures** — Everything must be inferred. Schema generation and extraction both use real AI calls. Tests verify the pipeline runs cleanly (any valid schema + any valid extraction = success).
- **10 multi-domain scenarios tested end-to-end** — support tickets, job apps, events, inventory, real estate, projects, expenses, medical, restaurants, freelance invoices. Each runs: prompt → AI schema → AI extraction → DB insert. No fixed expectations on table/column names.
- **71 tests total** — 61 fast pytest (mocked AI) + 2 standalone scripts for real AI runs (`test_multi_schema.py`, `test_edge_cases.py`).

## Reference Files

| File | Content | When to load |
|------|---------|-------------|
| `reference/architecture.md` | Package layout, module responsibilities, CLI commands | Onboarding, adding features |
| `reference/database.md` | Schema inference, FK resolution, auto-fill, DB creation/insertion | Working on schema.py or db.py |
| `reference/extraction.md` | AI prompts, extraction flow, certainty/spam, model gotchas | Working on extractor.py |
| `reference/IMAP.md` | IMAP watcher, test server protocol, `.eml` format, IMAP FETCH format fix | Working on email_input.py or test_imap_server.py |
| `reference/testing.md` | Test structure, fixture layout, edge cases, mock patterns, end-to-end philosophy | Writing or running tests |

## Key Workflows

### 1. Infer schema + create DB
```bash
python3 -m email_parser.cli init --prompt "Track sales leads with company, contact, product interest" -o schema.yaml
```

### 2. Parse a single email
```bash
python3 -m email_parser.cli parse schema.yaml --text "Email content here" --model llama3.1:8b
python3 -m email_parser.cli parse schema.yaml --file email.eml
echo "body" | python3 -m email_parser.cli parse schema.yaml --stdin
```

### 3. Watch an IMAP inbox
```bash
# Against test server:
python3 tests/test_imap_server.py   # terminal 1
python3 -m email_parser.cli watch schema.yaml --server 127.0.0.1 --user test --password test --port 11437 --no-ssl
```

### 4. Run tests
```bash
python3 -m pytest tests/ -v                    # unit + pipeline (mocked) — 61 tests
python3 tests/test_multi_schema.py             # real AI end-to-end across 10 domains (~4-5 min)
python3 tests/test_edge_cases.py               # real AI edge case extraction (~4-5 min)
python3 tests/test_imap_server.py              # starts test IMAP server
```

### 5. Drop a new .eml file for testing
```bash
cp /path/to/email.eml tests/inbox/
# Test IMAP server picks it up on next poll
```
