import json
import re

from .schema import SchemaDef
from .prompt_logger import log


def _build_relationship_guide(schema: SchemaDef) -> str:
    lines = []
    for table in schema.tables:
        fks = [c for c in table.columns if c.foreign_key]
        if not fks:
            continue
        for c in fks:
            ref = c.foreign_key
            lines.append(
                f'  - "{table.name}"."{c.name}" → "{ref.table}"."{ref.column}"  '
                f'→ @last:{ref.table} or @pos:{ref.table}:<N>'
            )
    if not lines:
        return ""
    return "FOREIGN KEYS:\n" + "\n".join(lines)


EXTRACTION_PROMPT = """Extract data from the email into the schema below.

SCHEMA:
{schema_text}

{relationship_guide}

EMAIL:
{email_content}

RULES:
1. Return JSON: {{"extracted": {{"table": [{{"col": value}}]}}, "certainty": 0-1, "spam": 0-1}}
2. FK columns → use @last:<table> (all rows share parent) or @pos:<table>:<N> (specific row, 1-based). NEVER use real IDs or null for FKs.
3. Non-FK columns → extract real values from email. NEVER use @last: or @pos:. Use null only if missing.
4. PK columns (<table>_id) → extract value from email if present (e.g. "Order ID: 5002" → order_id: 5002). Use null if absent.
5. Output EVERY table from the schema as a key in "extracted". Include every entity mentioned — even if some optional fields are missing, include them as null. If no matching data for a table, use an empty array [].
6. Output ONLY valid JSON — no other text."""


SCHEMA_PROMPT = """Design a relational SQLite schema for extracting data from emails.

Requirements: "{prompt}"

Output YAML in this structure (replace the example content):
database: mydata.db
tables:
  - name: customers
    description: "Customer information"
    columns:
      - name: customers_id
        type: INTEGER
        required: true
        description: "PK — auto-generated"
      - name: company_name
        type: TEXT
        required: false
        description: "Company name"
  - name: orders
    description: "Customer orders"
    columns:
      - name: orders_id
        type: INTEGER
        required: true
        description: "PK — auto-generated"
      - name: customer_id
        type: INTEGER
        foreign_key:
          table: customers
          column: customers_id
        required: true
        description: "FK to customers"
      - name: order_date
        type: TEXT
        required: false
        description: "Order date"

RULES:
- Cover the main entities the user describes. Keep it focused — aim for 2-5 tables.
- PK: each table needs `<table>_id` as first column. TEXT for alphanumeric IDs, INTEGER for numeric IDs.
- FK: column name ends with `_id`. Reference `<table>_id` only — never `id`.
- Required: only PK and FK columns. All other columns: `required: false`.
- Column placement: each column in its most natural table. No duplicated data across tables.
- Output: valid YAML only, no markdown fences, no explanations."""


def generate_schema_from_prompt(
    prompt: str,
    model: str = "qwen2.5:7b",
    ollama_host: str = "http://localhost:11434",
    max_retries: int = 2,
) -> str:
    import ollama
    import yaml

    client = ollama.Client(host=ollama_host)

    system_msg = (
        "You are a database designer. Output only valid YAML. "
        "Every foreign_key column MUST reference <table>_id — never 'id'. "
        "Only PK and FK columns should have required: true."
    )

    for attempt in range(1 + max_retries):
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_msg,
                },
                {
                    "role": "user",
                    "content": SCHEMA_PROMPT.format(prompt=prompt),
                },
            ],
            options={"temperature": 0.1, "num_predict": 4096},
        )
        content = response["message"]["content"]

        # Strip markdown fences and YAML document separators
        content = re.sub(r'^---\s*\n?', '', content, count=1)
        content = re.sub(r'^```(?:yaml|yml)?\s*\n?', '', content, count=1)
        content = re.sub(r'\n?```\s*$', '', content, count=1)

        content = content.strip()
        if not content.startswith("database:"):
            if attempt < max_retries:
                continue
            raise ValueError(
                "AI did not return a valid schema. Response:\n" + content[:500]
            )

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            if attempt < max_retries:
                continue
            raise ValueError(
                f"AI generated invalid YAML after {1 + max_retries} attempts:\n"
                f"---\n{content[:1000]}\n---\nParser error: {e}"
            )

        if not isinstance(data, dict) or "tables" not in data:
            if attempt < max_retries:
                continue
            raise ValueError(
                f"AI generated YAML without tables:\n---\n{content[:500]}\n---"
            )

        # 1. Deduplicate tables by name
        seen_names = set()
        tables = []
        for table in data.get("tables", []):
            name = table.get("name", "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            tables.append(table)

        # 1b. Hard limit: keep ≤5 tables (the most detailed ones)
        if len(tables) > 5:
            tables.sort(key=lambda t: len(t.get("columns", [])), reverse=True)
            tables = tables[:5]
            data["tables"] = tables

        # 2. Fix FKs: strip self-referencing FKs and references to non-existent tables, fix column refs
        table_names = {t["name"] for t in tables}
        table_cols = {t["name"]: {c["name"] for c in t.get("columns", [])} for t in tables}
        for table in tables:
            for col in table.get("columns", []):
                fk = col.get("foreign_key")
                if fk:
                    if fk.get("table") == table["name"]:
                        # Self-referencing FK — always wrong
                        del col["foreign_key"]
                    elif fk.get("table") not in table_names:
                        del col["foreign_key"]
                    elif fk.get("column") not in table_cols.get(fk["table"], set()):
                        # FK references a non-existent column — find the actual PK
                        target_table = next(t for t in tables if t["name"] == fk["table"])
                        pk_cols = [c["name"] for c in target_table.get("columns", [])
                                   if c["name"].endswith("_id")]
                        if pk_cols:
                            fk["column"] = pk_cols[0]
                        else:
                            del col["foreign_key"]

        # 2b. Normalize FK column names: ensure they end with _id
        for table in tables:
            for col in table.get("columns", []):
                fk = col.get("foreign_key")
                if fk and not col["name"].endswith("_id"):
                    col["name"] = f"{fk['table']}_id"

        # 3. Filter columns — remove bare "id" (conflicts with auto PK), keep everything else
        for table in tables:
            filtered = []
            for c in table.get("columns", []):
                name_col = c.get("name", "")
                if not name_col or name_col == "id":
                    continue
                filtered.append(c)
            table["columns"] = filtered

        # 4. Only PK and FK columns keep required: true — all data columns become optional
        for table in tables:
            pk_col_name = None
            for c in table.get("columns", []):
                cname = c.get("name", "")
                if cname.endswith("_id") and c.get("type", "").upper() == "INTEGER" and not c.get("foreign_key"):
                    pk_col_name = cname
                    break
            for c in table.get("columns", []):
                cname = c.get("name", "")
                is_pk = (cname == pk_col_name)
                if not is_pk:
                    c["required"] = False

        # 5. Schema self-consistency check: ensure every table has a PK, no duplicate columns
        for table in tables:
            cols = table.setdefault("columns", [])
            col_names = [c["name"] for c in cols]
            # Deduplicate columns (keep first occurrence)
            seen = set()
            deduped = []
            for c in cols:
                if c["name"] not in seen:
                    seen.add(c["name"])
                    deduped.append(c)
            if len(deduped) != len(cols):
                table["columns"] = deduped
            # Ensure PK column exists
            pk_col_name = f"{table['name']}_id"
            if pk_col_name not in seen:
                cols.insert(0, {
                    "name": pk_col_name,
                    "type": "INTEGER",
                    "required": True,
                    "description": "PK — auto-generated",
                })
            # After ensuring PK, mark all other *_id columns (that aren't FKs) as optional
            for c in table.get("columns", []):
                cname = c.get("name", "")
                if cname == pk_col_name:
                    continue
                if cname.endswith("_id") and not c.get("foreign_key"):
                    c["required"] = False

        # 5b. Fix FK references: if step 5 added a new PK, update any FK that references
        # a non-PK *_id column in that table to point to the actual PK.
        pk_map = {t["name"]: f"{t['name']}_id" for t in tables}
        for table in tables:
            for col in table.get("columns", []):
                fk = col.get("foreign_key")
                if fk:
                    expected_pk = pk_map.get(fk["table"])
                    if expected_pk and fk["column"] != expected_pk:
                        # Only update if the FK's target column exists in the referenced table
                        ref_table = next((t for t in tables if t["name"] == fk["table"]), None)
                        if ref_table and fk["column"] in {c["name"] for c in ref_table.get("columns", [])}:
                            fk["column"] = expected_pk

        # 6. Remove tables left empty
        data["tables"] = [t for t in tables if t.get("columns")]
        final = yaml.dump(data, default_flow_style=False, sort_keys=False).strip() + "\n"
        log("schema_generation", prompt, final)
        return final

    # Should not be reached, but satisfy the return type
    raise RuntimeError("Unexpected error in schema generation")


def extract_from_email(
    schema,
    email_content: str,
    model: str = "qwen2.5:7b",
    ollama_host: str = "http://localhost:11434",
) -> dict:
    import ollama

    client = ollama.Client(host=ollama_host)
    # Build a clean text schema representation (not JSON — avoids "name" key confusion)
    schema_lines = []
    for t in schema.tables:
        pk_name = f"{t.name}_id"
        cols = []
        for c in t.columns:
            frag = c.name
            if c.name == pk_name:
                frag += " (PK)"
            if c.foreign_key:
                frag += f" (FK→{c.foreign_key.table})"
            cols.append(frag)
        schema_lines.append(f"  {t.name}: {', '.join(cols)}")
    schema_text = "Tables:\n" + "\n".join(schema_lines)
    relationship_guide = _build_relationship_guide(schema)

    response = client.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise data extraction engine. Return valid JSON only. FK columns use @last: or @pos:. Non-FK columns use real values.",
            },
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(
                    schema_text=schema_text,
                    relationship_guide=relationship_guide,
                    email_content=email_content,
                ),
            },
        ],
        options={"temperature": 0.1},
        format="json",
    )

    content = response["message"]["content"]

    log("extraction", email_content[:500], content[:500])

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise

    if "extracted" in result and "certainty" in result and "spam" in result:
        extracted = result["extracted"]
    else:
        extracted = result if isinstance(result, dict) and "extracted" not in result else result.get("extracted", {})

    # Validate extraction: check FK columns have @last:/@pos: markers
    if not _validate_extraction(schema, extracted):
        # Retry once with slightly higher temperature for diversity
        log("extraction_retry", "Retrying extraction due to validation failure", "")
        retry_resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise data extraction engine. Return valid JSON only. FK columns use @last: or @pos:. Non-FK columns use real values."},
                {"role": "user", "content": EXTRACTION_PROMPT.format(
                    schema_text=schema_text,
                    relationship_guide=relationship_guide,
                    email_content=email_content,
                )},
            ],
            options={"temperature": 0.3},  # slightly higher for variation
            format="json",
        )
        retry_content = retry_resp["message"]["content"]
        log("extraction_retry_result", email_content[:200], retry_content[:500])
        try:
            retry_result = json.loads(retry_content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", retry_content, re.DOTALL)
            if match:
                retry_result = json.loads(match.group())
            else:
                retry_result = result  # fall back to original
        if "extracted" in retry_result and "certainty" in retry_result and "spam" in retry_result:
            extracted = retry_result["extracted"]
        else:
            extracted = retry_result if isinstance(retry_result, dict) and "extracted" not in retry_result else retry_result.get("extracted", {})

    wrapped = {
        "certainty": result.get("certainty", 0.5),
        "spam": result.get("spam", 0.5),
        "extracted": extracted,
    }
    return wrapped


def _validate_extraction(schema, extracted: dict) -> bool:
    """Check that the extraction result is consistent with the schema.
    Returns True if valid, False if retry is likely to help."""
    if not extracted or not isinstance(extracted, dict):
        return False

    schema_table_names = {t.name for t in schema.tables}
    for table_name, rows in extracted.items():
        if table_name not in schema_table_names:
            return False  # unknown table — schema may have changed
        if not isinstance(rows, list):
            return False
        table = next(t for t in schema.tables if t.name == table_name)
        schema_col_names = {c.name for c in table.columns}
        fk_col_names = {c.name for c in table.columns if c.foreign_key}
        for row in rows:
            if not isinstance(row, dict):
                return False
            # Check for unknown columns
            for key in row:
                if key not in schema_col_names:
                    return False
            # Check FK columns have @last: or @pos: markers
            for fk_name in fk_col_names:
                val = row.get(fk_name)
                if val is not None and not isinstance(val, str):
                    continue  # already resolved (edge case)
                if val is None:
                    continue  # _auto_fill_fks will handle null FKs
                if isinstance(val, str) and not val.startswith("@last:") and not val.startswith("@pos:"):
                    return False  # FK has a raw value — likely wrong
    return True
