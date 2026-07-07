# Project Architecture

## Package Layout

```
email_parser/
├── __init__.py          # Package init
├── __main__.py          # python -m entry
├── schema.py            # Pydantic models + YAML schema parsing
├── db.py                # SQLite creation, insertion, FK resolution, query
├── extractor.py         # Ollama-based AI extraction + schema generation
├── email_input.py       # Input sources: text, .eml, stdin, IMAP watcher
└── cli.py               # Click CLI: init, parse, watch, list
```

## Module Responsibilities

| Module | Role | Key exports |
|--------|------|-------------|
| `schema.py` | Schema definition & parsing | `SchemaDef`, `TableDef`, `ColumnDef`, `ForeignKeyRef`, `TYPE_MAP` |
| `db.py` | Database operations | `create_database()`, `insert_extracted()`, `query_data()`, `_auto_fill_fks()` |
| `extractor.py` | AI interaction | `extract_from_email()`, `generate_schema_from_prompt(prompt, model, ollama_host, max_retries=2)`, prompt templates |
| `email_input.py` | Email reading | `from_text()`, `from_eml()`, `from_stdin()`, `_get_body()`, `IMAPWatcher`, `fetch_imap_emails()` |
| `cli.py` | CLI entry points | `cli()` group, `init`, `parse`, `watch`, `list` commands |

## CLI Commands

| Command | Purpose | Key Flags |
|---------|---------|-----------|
| `init` | Create DB from schema YAML or prompt | `--prompt`, `--model`, `-o` |
| `parse` | Extract from single email | `--text`, `--file`, `--stdin`, `--model`, `--min-certainty`, `--max-spam` |
| `watch` | Poll IMAP inbox continuously | `--server`, `--user`, `--password`, `--port`, `--no-ssl`, `--interval`, `--min-certainty`, `--max-spam` |
| `list` | Query stored data | `--table`, `--limit`, `--json` |

## Test Fixtures Layout

```
tests/fixtures/
├── schema/sales.yaml           # 3-table sales schema for pipeline tests
└── expected/*.json             # Mocked AI responses for pipeline tests
```

## Imports Map

```
cli.py → schema.py, db.py, extractor.py, email_input.py
extractor.py → schema.py
db.py → schema.py
email_input.py → (stdlib only)
schema.py → yaml, pydantic
```
