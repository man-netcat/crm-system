import json
import re

from .schema import SchemaDef


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
                f'→ use @last:{ref.table}'
            )
    if not lines:
        return ""
    return "FOREIGN KEY RELATIONSHIPS:\n" + "\n".join(lines)


EXTRACTION_PROMPT = """You are an email data extraction engine. Extract structured data from emails based on the provided database schema.

DATABASE SCHEMA:
{schema_json}

{relationship_guide}

EMAIL CONTENT:
{email_content}

INSTRUCTIONS:
1. Read the email carefully.
2. For each table defined in the schema, extract ONLY matching information.
3. Return a JSON object with three fields:
   - "extracted": {{"table_name": [{{"column_name": value}}, ...]}} — the extracted data. Empty object {{}} if no data matches.
   - "certainty": a float from 0.0 to 1.0 — how confident you are that the extracted data is correct
   - "spam": a float from 0.0 to 1.0 — how likely this email is irrelevant, spam, or nonsense (0 = genuine sales lead, 1 = total garbage)
4. Use null for missing optional fields. Skip tables with zero matches.
5. Extract ALL instances — if multiple records exist, include each as a separate row.
6. Dates should be in YYYY-MM-DD format where possible.
7. Do not include any text outside the JSON object.
8. Every foreign key column listed in FOREIGN KEY RELATIONSHIPS MUST use @last:<tablename> as its value — never null, never a real value. NON-FK columns must NEVER use @last: — extract real data from the email instead. If you cannot find a value for a non-FK column, use null, not @last:."""


SCHEMA_PROMPT = """You are a database schema designer. Given a user's description of what they want to extract from emails, design a relational SQLite schema.

User's requirements: "{prompt}"

Generate a YAML schema in this EXACT format (this is a valid example, follow the structure):

database: emails.db
tables:
  - name: projects
    description: "Projects"
    columns:
      - name: project_name
        type: TEXT
        description: "Project name"
        required: true
      - name: lead_name
        type: TEXT
        description: "Project lead"
        required: false
  - name: milestones
    description: "Milestones within a project"
    columns:
      - name: project_id
        type: INTEGER
        description: "FK to projects.id"
        foreign_key:
          table: projects
          column: id
        required: true
      - name: milestone_name
        type: TEXT
        description: "Milestone name"
        required: true
      - name: due_date
        type: DATE
        description: "Due date"
        required: false
  - name: tasks
    description: "Individual tasks"
    columns:
      - name: milestone_id
        type: INTEGER
        description: "FK to milestones"
        foreign_key:
          table: milestones
          column: id
        required: true
      - name: task_name
        type: TEXT
        description: "Task name"
        required: true
      - name: assignee
        type: TEXT
        description: "Assigned person"
        required: false

Rules:
- 2 to 3 tables max — each table must be a distinct real-world entity (e.g. customers, orders, products)
- Each table gets auto-generated id (do NOT include it in columns)
- Every foreign_key must reference another table's id column:
    foreign_key:
      table: <table_name>
      column: id
  column: id is MANDATORY — never reference name, email, or any non-id column
- Foreign key column names should end with _id (e.g. project_id, client_id)
- Only create tables that the user explicitly mentions in their requirements
- NEVER create join/lookup tables, relationship tables, status-history tables, or extra tables that are not explicitly described
- Follow the YAML structure above exactly — including the nesting and quoting style
- Output ONLY valid YAML — no markdown fences, no explanations"""


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
        "You are a database designer. Output only valid YAML, no explanations. "
        "Every foreign_key MUST reference column: id — never use any other column."
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
            options={"temperature": 0.1},
        )
        content = response["message"]["content"]

        # Strip markdown fences
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

        # 2. Fix FKs: force column=id, strip references to non-existent tables
        table_names = {t["name"] for t in tables}
        bad_fk_cols = set()
        for table in tables:
            for col in table.get("columns", []):
                fk = col.get("foreign_key")
                if fk:
                    fk["column"] = "id"
                    if fk.get("table") not in table_names:
                        del col["foreign_key"]
                        bad_fk_cols.add((table["name"], col["name"]))

        # 4. Filter columns (remove id, auto-ID heuristic)
        for table in tables:
            filtered = []
            for c in table.get("columns", []):
                name_col = c.get("name", "")
                if not name_col or name_col == "id":
                    continue
                # INTEGER *_id without FK and not a stripped-bad-FK column → auto-ID
                if (name_col.endswith("_id") and not c.get("foreign_key")
                        and c.get("type", "").upper() == "INTEGER"
                        and (table["name"], name_col) not in bad_fk_cols):
                    continue
                filtered.append(c)
            table["columns"] = filtered

        # 5. Remove tables left empty
        data["tables"] = [t for t in tables if t.get("columns")]
        return yaml.dump(data, default_flow_style=False, sort_keys=False).strip() + "\n"

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
    schema_json = schema.model_dump_json(indent=2, exclude={"database"})
    relationship_guide = _build_relationship_guide(schema)

    response = client.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise data extraction engine. Always return valid JSON matching the requested schema.",
            },
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(
                    schema_json=schema_json,
                    relationship_guide=relationship_guide,
                    email_content=email_content,
                ),
            },
        ],
        options={"temperature": 0.1},
        format="json",
    )

    content = response["message"]["content"]

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
