# AI Extraction Reference

## Extraction Prompt (`extractor.py:24-45`)

Prompt instructs AI to:

1. Read email carefully
2. Match to schema tables
3. Return JSON with three fields:
   - `extracted`: `{"table_name": [{"column": value}, ...]}`
   - `certainty`: float 0–1 (confidence signal)
   - `spam`: float 0–1 (irrelevance signal)
4. Use `null` for missing optional fields
5. Extract ALL instances (multiple rows)
6. Dates in YYYY-MM-DD format
7. Every FK column MUST use `@last:<tablename>` (never null, never a real value)
8. Non-FK columns must NEVER use `@last:` — extract real data or `null`

**Critical rule for the AI:** The prompt explicitly says FK columns use `@last:` and non-FK columns must never use it. Small models (llama3.2) routinely violate this.

## Model Recommendations

| Model | Quality | Notes |
|-------|---------|-------|
| `llama3.2` (3B) | Poor | Consistently uses `@last:` as placeholder on non-FK columns. Not suitable for production. |
| `llama3.1:8b` | Good | Reliable FK handling, correct real values on non-FK columns. Minimum viable. |

## Certainty & Spam Scoring

- Both are AI self-evaluated, not computed separately
- Legitimate business emails: `certainty ~0.9, spam ~0.01`
- Recipes / newsletters: `certainty ~0.8, spam ~0.2` (overconfident)
- Garbage / spam: `certainty ~0.5, spam ~1.00`
- CLI flags: `--min-certainty 0.5 --max-spam 0.8`

## Schema Generation Prompt (`extractor.py:48-116`)

- Temperature: 0.1 (very deterministic)
- System message reinforces `column: id` rule for all FK references
- User describes needs in natural language
- AI generates valid YAML schema
- Example schema in prompt (projects→milestones→tasks) — AI tends to template-match against this. Explicit rules added: "Only create tables the user mentions", "Do NOT create join/lookup tables". Post-processing strips bad FKs that reference non-existent tables.

### Retry logic (`generate_schema_from_prompt`, line 119-193)
- Up to 3 attempts (2 retries) if output is invalid
- On each retry: same prompt re-sent to the model
- Failures that trigger retry:
  1. Output doesn't start with `database:` (truncation)
  2. YAML parsing error
  3. Missing `tables` key in parsed YAML
- After exhausting retries: `ValueError` with truncated response for debugging

### Post-processing
- Markdown fences stripped via regex: ` ```yaml`, ` ```yml`
- AI-inferred `id` columns stripped from YAML
- FK `column` forced to `id`
- FK references to non-existent tables removed — critical when AI hallucinates tables
- Columns without a `name` key filtered out

## Extraction Prompt Parameters

- Temperature: 0.1 (very deterministic)
- `format="json"` passed to Ollama for structured output
- Uses `ollama.Client` from the `ollama` Python library

## Known AI Failure Modes

### Extraction
1. **@last: on non-FK columns** — llama3.2 puts `@last:companies` in contact_name or email fields. Code handles this by converting to null.
2. **Missing FK references** — AI forgets `@last:` entirely. Auto-fill catches when parent has 1 row.
3. **Over-normalization** — AI creates too many tables or extra id columns. Prompt restricts to 2-4 tables.
4. **Null FK on multi-row parent** — When auto-fill can't help (ambiguous), rows with null FKs and NOT NULL constraints are skipped.
5. **Overconfident certainty** — Recipes/newsletters still get ~0.80 certainty. Spam score is more reliable than certainty.

### Schema Generation (`init --prompt`)
1. **Truncation** — Model cuts off mid-YAML. Caught by retry logic: output must start with `database:`.
2. **Template-matching** — AI mirrors the example prompt's structure (projects→milestones→tasks) instead of the user's actual requirements. Mitigated by explicit anti-template rules in prompt + post-processing that removes FKs to non-existent tables.
3. **Bad FK references** — AI references `name` column instead of `id`, or references a table that doesn't exist. Post-processing fixes column→id and removes bad FKs.
4. **Over-inference** — AI adds id columns to table definitions or creates join tables. Post-processing strips id columns; prompt forbids join tables.
5. **Markdown fences** — AI wraps YAML in ` ```yaml ` / ` ``` ` blocks. Stripped by regex.
6. **YAML syntax errors** — AI produces malformed YAML. Retry on parse failure.
7. **Columns without `name` field** — AI occasionally omits the `name` key in column definitions. Filtered by `if c.get("name")` in post-processing.
8. **Extra relationship tables** — AI creates join/link tables. Prompt explicitly forbids these; bad FKs to non-existent tables are stripped by post-processing (the unused table itself survives but is harmless).
9. **Duplicate table names** — AI occasionally repeats tables. Deduplicated in post-processing.
10. **Non-deterministic output** — AI returns different schemas each invocation. This is expected and acceptable; the project philosophy is pure inference with no hardcoded expectations.
