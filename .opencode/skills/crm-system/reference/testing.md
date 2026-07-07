# Testing Reference

## Test Layout

```
tests/
├── test_schema.py              # 13 unit tests — schema parsing, typing, dependency order
├── test_db.py                  # 14 unit tests — DB creation, insertion, FK resolution, query
├── test_email_input.py         # 11 unit tests — body extraction, .eml parsing, unicode
├── test_extractor.py           # 6 unit tests — prompt building, relationship guide
├── test_pipeline.py            # 7 pipeline tests — .eml → extraction → DB (mocked AI)
├── test_accept_reject.py       # 10 tests — CLI threshold filtering (CliRunner + mocked AI)
├── test_multi_schema.py        # Real AI end-to-end across 10 domains (standalone, ~4-5 min)
├── test_edge_cases.py          # 8 real-AI scenario extraction tests (standalone, ~4-5 min)
├── test_imap_server.py         # Test IMAP server (not a test suite, run directly)
├── edge_cases/                 # .eml files for edge case testing
│   ├── empty.eml
│   ├── recipe.eml
│   ├── multi_lead.eml
│   ├── company_only.eml
│   ├── unicode.eml
│   ├── minimal.eml
│   ├── no_match.eml
│   ├── garbage.eml
│   └── html_only.eml
├── fixtures/
│   ├── schema/sales.yaml       # Pytest schema fixture for pipeline tests
│   └── expected/*.json         # Mocked AI responses for pipeline tests
└── inbox/                      # .eml files served by test IMAP server
    ├── 001_sarah.eml
    └── 002_james.eml
```

## Running Tests

```bash
# All unit + pipeline tests (mocked, fast)
python3 -m pytest tests/ -v                     # 61 tests total

# Specific file
python3 -m pytest tests/test_db.py -v

# Real AI end-to-end (requires Ollama + llama3.1:8b, ~4-5 min)
python3 tests/test_multi_schema.py

# Real AI edge cases (requires Ollama, ~4-5 min for 8 tests)
python3 tests/test_edge_cases.py

# Start test IMAP server (for end-to-end testing)
python3 tests/test_imap_server.py
```

## Testing Philosophy

**No hardcoded expectations.** The project is built on true AI inference — schema generation and extraction are both non-deterministic. Tests verify the pipeline runs cleanly without crashing, not specific table/column names:

- **Pytest suite (61 tests)** — Fast, deterministic, uses mocked AI responses for all Ollama calls
- **Standalone scripts** — Real AI calls for confidence testing; success = no crashes + data lands in DB

## Mocking Strategy

### Pipeline tests (`test_pipeline.py`)
- Mock `ollama.Client.chat` via `unittest.mock.patch`
- Fixture JSON files in `tests/fixtures/expected/` hold the exact AI response
- `_mock_ollama(fixture_name)` returns a function that returns the fixed response

```python
with patch("ollama.Client.chat", _mock_ollama("company_only")):
    result = extract_from_email(schema, body)
```

### Accept/reject tests (`test_accept_reject.py`)
- Mock `extract_from_email` to return controlled certainty/spam values
- Mock `SchemaDef.from_yaml` to avoid file I/O
- Mock `insert_extracted` to avoid DB writes
- Use Click's `CliRunner` for full CLI invocation

### What is mocked vs real

| Component | Unit tests | Pipeline tests | Edge case tests | Multi-schema tests |
|-----------|-----------|----------------|-----------------|-------------------|
| Ollama AI  | ✗ (not needed) | Mocked via JSON fixtures | Real calls | Real calls |
| SQLite    | Real temp DB | Real temp DB | Real DB file | Temp DB file |
| File I/O  | Real files | Real .eml files | Real .eml files | Real schema files |
| Click CLI | CliRunner | ✗ (function calls) | ✗ | ✗ |

## Multi-Schema Testing (`test_multi_schema.py`)

**Pure end-to-end:** Each domain runs prompt → AI schema → AI extraction → DB insert with no fixed schema expectations.

Each domain has: `name`, `prompt` (natural language description), `email` (text body).

Success = pipeline doesn't crash and at least one row exists in the database.

### Domain Scenarios (10 total)

| # | Domain |
|---|--------|
| 1 | Support tickets |
| 2 | Job applications |
| 3 | Event registrations |
| 4 | Inventory / purchase orders |
| 5 | Real estate inquiries |
| 6 | Project tasks |
| 7 | Expense reports |
| 8 | Medical appointments |
| 9 | Restaurant reservations |
| 10 | Freelance invoices |

### Pipeline flow per domain

1. `generate_schema_from_prompt(prompt)` → AI returns YAML
2. `SchemaDef.from_yaml_str(yaml)` → parse into models
3. `create_database(schema)` → SQLite tables with FK constraints
4. `extract_from_email(schema, email)` → AI returns `{certainty, spam, extracted}`
5. `insert_extracted(schema, extracted)` → rows into DB
6. Verify `COUNT(*) > 0` in at least one table

### Schema generation failure modes handled
- Truncated YAML → retry (up to 3 attempts)
- AI mirrors example prompt → post-processing strips bad FK refs
- AI uses `column: name` instead of `column: id` → forced to `id`
- AI creates columns without `name` field → filtered by `if c.get("name")`
- AI creates extra relationship tables → bad FK refs stripped (unused tables harmless)
- AI duplicates table names → deduplicated

## Accept/Reject Tests (`test_accept_reject.py`)

10 tests verifying `--min-certainty` / `--max-spam` CLI filtering:

- **8 CliRunner tests** — invoke `parse` and `watch` commands with mocked `extract_from_email`, `SchemaDef.from_yaml`, `insert_extracted`
- **2 pure logic tests** — test `_passes_threshold()` directly with boundary values
- Mock returns controlled `{"certainty": X, "spam": Y, "extracted": {...}}` values
- Verifies: correct output messages, no extraction calls when threshold fails, boundary behavior (exact match passes)

## Edge Case Scenarios (`test_edge_cases.py`)

8 scenarios run against real AI (standalone script, ~4-5 min). Each calls `run_test()` with `expect_scores` dict.

| # | Case | Expected Behavior | expect_scores assertions |
|---|------|------------------|--------------------------|
| 1 | Empty body | Skip silently (no extraction call) | — |
| 2 | Recipe (irrelevant) | Low certainty, high spam, empty extraction | `max_certainty: 0.4`, `min_spam: 0.5` |
| 3 | Multiple records | Multiple rows with FK resolution | `min_certainty: 0.5`, `max_spam: 0.5` |
| 4 | Company only (partial data) | Company + lead, FK resolved | `min_certainty: 0.3`, `max_spam: 0.5` |
| 5 | Unicode (é, ü, €) | Characters preserved end-to-end | `min_certainty: 0.4`, `max_spam: 0.5` |
| 6 | Minimal (name only) | Company inserted, lead nulls | `min_certainty: 0.2`, `max_spam: 0.6` |
| 7 | Newsletter (no match) | Empty extraction | `max_certainty: 0.4`, `min_spam: 0.3` |
| 8 | Garbage/binary body | Very low certainty, very high spam | `max_certainty: 0.2`, `min_spam: 0.7` |

## Key Fixtures

- `tests/fixtures/schema/sales.yaml` — 3-table sales schema (companies, leads, products) used by all pipeline tests
- `tests/fixtures/expected/*.json` — mocked AI responses for pipeline tests (company_only, unicode, recipe, etc.)
