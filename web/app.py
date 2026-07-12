import json
import os
import sqlite3
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from email_parser.schema import SchemaDef
from email_parser.db import create_database, insert_extracted, query_data
from email_parser.extractor import generate_schema_from_prompt, extract_from_email
from email_parser.prompt_logger import log

app = Flask(__name__)
app.secret_key = os.urandom(16).hex()

MODEL = "llama3.1:8b"
PROJECT_ROOT = Path(__file__).parent.parent


def discover_schemas() -> list[dict]:
    """Find all schema YAML files in the project root."""
    schemas = []
    for p in PROJECT_ROOT.glob("*.yaml"):
        try:
            data = yaml.safe_load(p.read_text())
            if isinstance(data, dict) and "tables" in data and "database" in data:
                db_path = PROJECT_ROOT / data["database"]
                row_count = 0
                if db_path.exists():
                    try:
                        conn = sqlite3.connect(str(db_path))
                        c = conn.cursor()
                        for t in data.get("tables", []):
                            c.execute(f'SELECT COUNT(*) FROM "{t["name"]}"')
                            row_count += c.fetchone()[0]
                        conn.close()
                    except Exception:
                        pass
                schemas.append({
                    "name": p.stem,
                    "path": str(p.relative_to(PROJECT_ROOT)),
                    "db_path": data["database"],
                    "tables": [t["name"] for t in data.get("tables", [])],
                    "exists": db_path.exists(),
                    "row_count": row_count,
                })
        except Exception:
            continue
    schemas.sort(key=lambda s: s["name"])
    return schemas


def get_schema(path: str) -> SchemaDef | None:
    fp = PROJECT_ROOT / path
    if not fp.exists():
        return None
    try:
        return SchemaDef.from_yaml(str(fp))
    except Exception:
        return None


@app.template_filter("slug")
def slug(s):
    return s.lower().replace(" ", "_")


@app.route("/")
def index():
    schemas = discover_schemas()
    return render_template("index.html", schemas=schemas)


@app.route("/schemas/new")
def schema_new():
    return render_template("schema_new.html")


@app.route("/schemas", methods=["POST"])
def schema_create():
    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        flash("Prompt is required", "error")
        return redirect(url_for("schema_new"))

    try:
        schema_yaml = generate_schema_from_prompt(prompt)
        data = yaml.safe_load(schema_yaml)
        name = data.get("database", "schema").replace(".db", "")
    except Exception as e:
        flash(f"Schema generation failed: {e}", "error")
        return redirect(url_for("schema_new"))

    path = PROJECT_ROOT / f"{name}.yaml"
    path.write_text(schema_yaml)

    try:
        schema = SchemaDef.from_yaml(str(path))
        schema.database = str(PROJECT_ROOT / schema.database)
        create_database(schema)
        flash(f"Schema '{name}' created with {len(schema.tables)} table(s)", "success")
    except Exception as e:
        flash(f"Database creation failed: {e}", "error")

    return redirect(url_for("index"))


@app.route("/schemas/<path:path>")
def schema_detail(path):
    schema = get_schema(path)
    if not schema:
        flash("Schema not found", "error")
        return redirect(url_for("index"))

    db_path = PROJECT_ROOT / schema.database
    if not db_path.exists():
        flash("Database file not found — run init first", "error")
        return redirect(url_for("index"))

    tables = []
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    for t in schema.tables:
        try:
            c.execute(f'SELECT COUNT(*) FROM "{t.name}"')
            count = c.fetchone()[0]
        except Exception:
            count = 0
        has_explicit_pk = any(
            col.type.upper() == "INTEGER" and col.name.endswith("_id") and not col.foreign_key
            for col in t.columns
        )
        cols = [col.name for col in t.columns]
        if not has_explicit_pk:
            cols = ["id"] + cols
        tables.append({"name": t.name, "columns": cols, "row_count": count})
    conn.close()

    return render_template("schema_detail.html", schema_path=path, schema=schema, tables=tables)


@app.route("/schemas/<path:path>/tables/<table_name>/data")
def table_data(path, table_name):
    schema = get_schema(path)
    if not schema:
        return {"error": "Schema not found"}, 404

    try:
        schema.database = str(PROJECT_ROOT / schema.database)
        results = query_data(schema, table_name=table_name, limit=100)
    except Exception as e:
        return {"error": str(e)}, 500

    rows = results.get(table_name, [])
    if not rows:
        columns = [col.name for col in next((t for t in schema.tables if t.name == table_name), schema.tables[0]).columns] if schema.tables else []
        return f'<tr><td colspan="{len(columns) + 1}" class="meta">No data yet</td></tr>'
    columns = list(rows[0].keys())
    tds = ""
    for r in rows:
        tds += "<tr>"
        for c in columns:
            val = r.get(c)
            tds += f"<td>{val if val is not None else '<span class=meta>NULL</span>'}</td>"
        tds += "</tr>"
    return tds


@app.route("/schemas/<path:path>/delete", methods=["POST"])
def schema_delete(path):
    schema = get_schema(path)
    if not schema:
        flash("Schema not found", "error")
        return redirect(url_for("index"))

    fp = PROJECT_ROOT / path
    db_path = PROJECT_ROOT / schema.database

    try:
        fp.unlink(missing_ok=True)
    except Exception as e:
        flash(f"Failed to delete schema file: {e}", "error")
        return redirect(url_for("index"))

    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    flash(f"Schema '{path}' deleted", "success")
    return redirect(url_for("index"))


@app.route("/import/<path:path>", methods=["GET", "POST"])
def import_email(path):
    schema = get_schema(path)
    if not schema:
        flash("Schema not found", "error")
        return redirect(url_for("index"))

    result = None
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if text:
            try:
                log("web_import_email", text[:500])
                r = extract_from_email(schema, text, model=MODEL)
                extracted = r.get("extracted", {})
                counts = insert_extracted(schema, extracted)
                # Show only the rows just inserted
                schema.database = str(PROJECT_ROOT / schema.database)
                fresh_data = {}
                try:
                    conn = sqlite3.connect(schema.database)
                    conn.row_factory = sqlite3.Row
                    c = conn.cursor()
                    for t in schema.tables:
                        n = counts.get(t.name, 0)
                        if n:
                            c.execute(f'SELECT * FROM "{t.name}" ORDER BY rowid DESC LIMIT ?', (n,))
                            fresh_data[t.name] = [dict(r) for r in c.fetchall()][::-1]
                    conn.close()
                except Exception as e:
                    print(f"FRESH_DATA ERROR: {e}", flush=True)
                print(f"FRESH_DATA: {fresh_data}", flush=True)
                print(f"COUNTS: {counts}", flush=True)
                result = {
                    "certainty": r.get("certainty"),
                    "spam": r.get("spam"),
                    "extracted": fresh_data,
                    "inserted": {k: v for k, v in counts.items() if v},
                    "skipped": sum(1 for t in schema.tables if t.name in extracted and not counts.get(t.name)),
                }
            except Exception as e:
                flash(f"Extraction failed: {e}", "error")

    tables_info = [
        {
            "name": t.name,
            "columns": [col.name for col in t.columns],
        }
        for t in schema.tables
    ]
    return render_template("import.html", schema_path=path, schema=schema, tables=tables_info, result=result)


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
