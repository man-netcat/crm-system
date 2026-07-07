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
2. For each table defined in the schema, extract any matching information.
3. Return ONLY valid JSON with this exact structure:
{{"table_name": [{{"column_name": value}}, ...]}}
4. Use null for missing optional fields. Skip tables with no matches.
5. Extract ALL instances — if multiple records exist, include each as a separate row.
6. Dates should be in YYYY-MM-DD format where possible.
7. Do not include any text outside the JSON object.
8. Every foreign key column listed in FOREIGN KEY RELATIONSHIPS MUST use @last:<tablename> as its value — never null, never a real value. NON-FK columns must never use @last: — extract real data from the email instead."""


SCHEMA_PROMPT = """You are a database schema designer. Given a user's description of what they want to extract from emails, design a relational SQLite schema.

User's requirements: "{prompt}"

Generate a YAML schema in this EXACT format (this is a valid example, follow the structure):

database: emails.db
tables:
  - name: suppliers
    description: "Supplier companies"
    columns:
      - name: company_name
        type: TEXT
        description: "Company name"
        required: true
      - name: contact_person
        type: TEXT
        description: "Contact person"
        required: false
  - name: orders
    description: "Orders placed"
    columns:
      - name: customer_id
        type: INTEGER
        description: "FK to customers.id"
        foreign_key:
          table: customers
          column: id
        required: true
      - name: delivery_date
        type: DATE
        description: "Delivery date"
        required: false
  - name: order_items
    description: "Line items"
    columns:
      - name: order_id
        type: INTEGER
        description: "FK to orders"
        foreign_key:
          table: orders
          column: id
        required: true
      - name: product_name
        type: TEXT
        description: "Product name"
        required: true
      - name: quantity
        type: INTEGER
        description: "Quantity"
        required: false

Rules:
- 2 to 4 tables max, no join/lookup tables
- Each table gets auto-generated id (do NOT include it)
- All foreign_key references must use column: id (never reference non-id columns)
- Follow the YAML structure above exactly — including the nesting and quoting style
- Output ONLY valid YAML — no markdown fences, no explanations"""


def generate_schema_from_prompt(
    prompt: str,
    model: str = "llama3.2",
    ollama_host: str = "http://localhost:11434",
) -> str:
    import ollama

    client = ollama.Client(host=ollama_host)
    response = client.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a database designer. Output only valid YAML, no explanations.",
            },
            {
                "role": "user",
                "content": SCHEMA_PROMPT.format(prompt=prompt),
            },
        ],
        options={"temperature": 0.3},
    )
    content = response["message"]["content"]

    yaml_match = re.search(r"```(?:yaml|yml)?\s*\n(.*?)\n```", content, re.DOTALL)
    if yaml_match:
        content = yaml_match.group(1)

    content = content.strip()
    if not content.startswith("database:"):
        raise ValueError(
            "AI did not return a valid schema. Response:\n" + content[:500]
        )
    import yaml
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(
            f"AI generated invalid YAML:\n---\n{content[:1000]}\n---\nParser error: {e}"
        )
    for table in data.get("tables", []):
        table["columns"] = [c for c in table.get("columns", []) if c.get("name") != "id"]
    return yaml.dump(data, default_flow_style=False, sort_keys=False).strip() + "\n"


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
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
