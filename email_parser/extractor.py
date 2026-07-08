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
  - name: entity_one
    description: "..."
    columns:
      - name: entity_one_id
        type: INTEGER
        required: true
        description: "PK — auto-generated"
      - name: some_field
        type: TEXT
        required: false
        description: "..."
  - name: entity_two
    description: "..."
    columns:
      - name: entity_two_id
        type: INTEGER
        required: true
        description: "PK — auto-generated"
      - name: entity_one_id
        type: INTEGER
        foreign_key:
          table: entity_one
          column: entity_one_id
        required: true
        description: "FK to entity_one"
      - name: another_field
        type: TEXT
        required: false
        description: "..."

RULES:
- Cover the main entities the user describes. Keep it focused — aim for 2-5 tables.
- PK: each table needs `<table>_id` as first column. TEXT for alphanumeric IDs, INTEGER for numeric IDs.
- FK: column name ends with `_id`. Reference `<table>_id` only — never `id`.
- Required: only PK and FK columns. All other columns: `required: false`.
- Column placement: each column in its most natural table. No duplicated data across tables.
- Output: valid YAML only, no markdown fences, no explanations."""


def generate_schema_from_prompt(
    prompt: str,
    model: str = "llama3.2",
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
                has_fk = bool(c.get("foreign_key"))
                is_self_fk = has_fk and c["foreign_key"].get("table") == table["name"]
                if not is_pk and (not has_fk or is_self_fk):
                    c["required"] = False

        # 5. Remove tables left empty
        data["tables"] = [t for t in tables if t.get("columns")]
        final = yaml.dump(data, default_flow_style=False, sort_keys=False).strip() + "\n"
        log("schema_generation", prompt, final)
        return final

    # Should not be reached, but satisfy the return type
    raise RuntimeError("Unexpected error in schema generation")


def extract_from_email(
    schema: SchemaDef,
    email_content: str,
    model: str = "llama3.2",
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
        return result

    wrapped = {
        "certainty": 0.5,
        "spam": 0.5,
        "extracted": result if isinstance(result, dict) and "extracted" not in result else result.get("extracted", {}),
    }
    return wrapped
