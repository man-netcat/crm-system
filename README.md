# CRM System — AI-Powered Email-to-Database Pipeline

Turn natural language descriptions into relational databases. Feed in emails, get structured data extracted by AI and inserted into SQLite with foreign key relationships resolved.

```bash
# Design a schema from a plain English prompt
python -m email_parser init --prompt "Track customer support tickets with customer name, priority, and assigned agent"

# Extract data from an email and store it
python -m email_parser parse inferred_schema.yaml --text "Login page returns 502. Priority: High. Agent: Bob."

# Browse results in the web UI
python web/app.py
```

---

## How It Works

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐     ┌─────────┐
│  "Describe   │────>│  Ollama (qwen)   │────>│  Relational  │────>│ SQLite  │
│   your data" │     │  generates YAML  │     │  Schema      │     │   DB    │
└──────────────┘     └──────────────────┘     └──────────────┘     └─────────┘
                                                      │
┌──────────────┐     ┌──────────────────┐            │
│  Email text  │────>│  Ollama extracts  │───────────>│
│  or .eml     │     │  structured JSON  │  FK markers│
└──────────────┘     └──────────────────┘  resolved   │
                                                      ▼
                                              ┌──────────────┐
                                              │  Flask + HTMX│
                                              │  Web Dashboard│
                                              └──────────────┘
```

Two AI stages, both running locally via Ollama:

1. **Schema inference** — describe your domain in English; the AI designs a normalized relational schema with tables, columns, types, primary keys, and foreign keys.
2. **Data extraction** — feed in an email; the AI extracts structured rows and emits `@last:<table>` / `@pos:<table>:<N>` FK markers that the pipeline resolves into real foreign key IDs.

A multi-stage post-processor ensures schema quality: deduplication, FK normalization, PK enforcement, table limits, and self-consistency checks.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [Ollama](https://ollama.ai) running locally with a model pulled:
  ```bash
  ollama pull qwen2.5:7b
  ```

### Install

```bash
git clone <repo> && cd crm_system
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
# Let AI design a schema from a description
python -m email_parser init \
  --prompt "Track job applications. Applicants have name, email, phone. Positions have title, department. Applications link applicants to positions."

# Extract data from an email
python -m email_parser parse inferred_schema.yaml \
  --text "Applying for Senior Python Dev in Engineering. Name: Jane. jane@email.com"

# View stored data
python -m email_parser list inferred_schema.yaml

# Or use the web dashboard
python web/app.py
# → http://localhost:5000
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `init [FILE]` | Create a database from a YAML schema file, or use `--prompt` to have AI generate one |
| `parse FILE` | Extract structured data from an email and insert into the database |
| `watch FILE` | Poll an IMAP inbox continuously, extracting every new message |
| `list FILE` | Query and display stored data, optionally filtered by table |

### Global Options

- `--model` — Ollama model to use (default: `qwen2.5:7b`)
- `--ollama-host` — Ollama server URL (default: `http://localhost:11434`)
- `--min-certainty` / `--max-spam` — threshold filters on extraction scores

### Examples

```bash
# Parse an .eml file
python -m email_parser parse orders.yaml --file complex_order.eml

# Watch an IMAP inbox
python -m email_parser watch orders.yaml \
  --server imap.example.com --user me@example.com --interval 30

# List data as JSON
python -m email_parser list orders.yaml --json

# Accept only high-confidence extractions
python -m email_parser parse schema.yaml --text "..." --min-certainty 0.7 --max-spam 0.2
```

---

## Web UI

The `web/` directory contains a Flask + HTMX dashboard:

```bash
python web/app.py
```

- **Dashboard** — auto-discovers schema YAML files, shows table counts and row counts
- **New Schema** — describe your data in a textarea; AI generates the schema and creates the database in one click
- **Schema Detail** — browse table data with lazy-loaded HTMX fragments
- **Import Email** — paste email text, see extraction results with certainty/spam scores, and insert into the database

---

## Testing

### Unit Tests (no Ollama needed, fast)

```bash
pytest tests/test_schema.py -v      # Schema model, YAML parsing, dependency ordering
pytest tests/test_db.py -v          # Database creation, insertion, FK resolution
pytest tests/test_extractor.py -v   # Prompt templates, relationship guide
pytest tests/test_email_input.py -v # Email parsing (text, .eml, multipart)
pytest tests/test_pipeline.py -v    # End-to-end with mocked AI responses
pytest tests/test_accept_reject.py -v # CLI threshold filtering
```

### Integration Tests (require Ollama)

```bash
# 8 edge-case scenarios (empty body, Unicode, garbage, etc.)
python tests/test_edge_cases.py

# 10 multi-domain pipelines (support tickets, event registrations, invoices, etc.)
python tests/test_multi_schema.py
```

---

## Project Structure

```
crm_system/
├── email_parser/          # Core package
│   ├── cli.py             # Click CLI (init, parse, watch, list)
│   ├── schema.py          # Pydantic schema models
│   ├── db.py              # SQLite create/insert/query
│   ├── extractor.py       # AI integration + schema post-processing
│   ├── email_input.py     # Email parsing + IMAP watcher
│   └── prompt_logger.py   # AI prompt/response audit log
├── web/                   # Flask + HTMX web UI
│   ├── app.py
│   └── templates/
├── tests/                 # Test suite
│   ├── test_schema.py     # 13 unit tests
│   ├── test_db.py         # 14 unit tests
│   ├── test_extractor.py  # 6 unit tests
│   ├── test_email_input.py# 9 unit tests
│   ├── test_pipeline.py   # 5 integration tests (mocked)
│   ├── test_accept_reject.py # 8 CLI tests
│   ├── test_edge_cases.py # 8 live-Ollama scenarios
│   ├── test_multi_schema.py # 10 live-Ollama domains
│   └── test_imap_server.py  # Local IMAP server for testing
├── requirements.txt
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI Inference | [Ollama](https://ollama.ai) + [Qwen 2.5 7B](https://qwenlm.github.io/) |
| Database | SQLite 3 |
| CLI | Click |
| Web UI | Flask + HTMX |
| Schema | Pydantic |
| Testing | pytest |

---

## Why This Exists

Extracting structured data from unstructured emails is a common business need — support tickets, job applications, invoices, event registrations, restaurant reservations, medical appointments. Traditional approaches require hand-crafted parsers or rigid form templates. This system lets you describe what you need in plain English, and the AI handles schema design and extraction on the fly.

---

## License

MIT
