# Database & Schema Reference

## Schema Inference (`init --prompt`)

AI receives `SCHEMA_PROMPT` in `extractor.py:48-116`. Rules:

- 2–3 tables max, no join/lookup tables
- Each table auto-gets `id INTEGER PRIMARY KEY AUTOINCREMENT` — AI must NOT include `id` in columns
- FK references must use `column: id` only — never `name`, `email`, or other non-id columns
- Output parsed as YAML; markdown fences stripped via regex
- Temperature: 0.1 (deterministic output)

### Post-processing (`generate_schema_from_prompt`, lines 181-189)

1. **Strip AI-inferred `id` columns** — any column named `id` is removed from table columns
2. **Force FK column to `id`** — all FK references are rewritten to target `column: id`
3. **Remove bad FK references** — if FK references a table not in the schema, the foreign_key is removed entirely (otherwise schema validation would fail)

### Retry / Validation pipeline

| Step | Check | Failure → Retry |
|------|-------|-----------------|
| 1 | Output starts with `database:` | Retry (truncation likely) |
| 2 | `yaml.safe_load()` succeeds | Retry |
| 3 | Result is a dict with `tables` key | Retry |
| 4 | Post-processing (id strip, FK fix) | ✅ always succeeds |
| 5 | `SchemaDef.from_yaml_str()` succeeds | 🛑 raises error (max retries exhausted) |

Up to 3 attempts total (2 retries).

## Schema YAML Format

```yaml
database: emails.db
tables:
  - name: suppliers
    description: "Supplier companies"
    columns:
      - name: company_name
        type: TEXT
        required: true
      - name: foreign_table_id
        type: INTEGER
        foreign_key:
          table: parent_table
          column: id
```

## Type Mapping (`schema.py:TYPE_MAP`)

| YAML type | SQLite type |
|-----------|-------------|
| TEXT | TEXT |
| INTEGER, int | INTEGER |
| REAL, float, number | REAL |
| BOOLEAN, bool | INTEGER |
| DATE, datetime | TEXT |

## FK Resolution (`db.py`)

### @last: convention
- AI outputs `@last:<tablename>` as the value for FK columns
- `insert_extracted()` resolves to `ref_registry[tablename]` (the last inserted row's `lastrowid`)
- Tables inserted in dependency order (`schema.py:dependency_order()`)

### Auto-fill fallback (`_auto_fill_fks`)
- If AI leaves an FK column null AND referenced table has exactly 1 row, auto-fills `@last:` marker
- Does NOT overwrite existing FK values
- Does NOT fill when referenced table has >1 row (ambiguous)

### Graceful handling
- `@last:` on non-FK column → warning + set null → row skipped if all non-FK cols null
- `@last:` referencing empty table → warning + set null → row skipped if NOT NULL violation
- Row-level `IntegrityError` caught per-row, not per-batch — one bad row doesn't lose the rest
- Rows where all non-FK columns are null are silently skipped (even if FK is valid)

## Insert Flow (`insert_extracted`)

1. Run `_auto_fill_fks()` to patch null FK values
2. Iterate tables in dependency order
3. For each row: resolve `@last:` refs, check `has_data`, try INSERT
4. On success: set `ref_registry[table] = cursor.lastrowid`
5. On `IntegrityError`: print warning + continue (skip that row)
