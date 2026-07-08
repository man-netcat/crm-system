"""Unit tests for db.py — database creation, insertion, querying."""

import os
import sqlite3

from email_parser.schema import SchemaDef
from email_parser.db import create_database, insert_extracted, query_data, _auto_fill_fks

SCHEMA_YAML = """
database: ":memory:"
tables:
  - name: companies
    columns:
      - name: name
        type: TEXT
        required: true
      - name: website
        type: TEXT
        required: false
  - name: contacts
    columns:
      - name: company_id
        type: INTEGER
        foreign_key:
          table: companies
          column: id
        required: true
      - name: email
        type: TEXT
        required: true
      - name: phone
        type: TEXT
        required: false
"""


def _schema():
    import yaml
    data = yaml.safe_load(SCHEMA_YAML)
    return SchemaDef.model_validate(data)


def _write_db(schema):
    """Create the database in a temp file and return the path."""
    path = "/tmp/_test_crm.db"
    if os.path.exists(path):
        os.remove(path)
    s = schema.model_copy(update={"database": path})
    create_database(s)
    return path


def test_create_database():
    schema = _schema()
    path = _write_db(schema)
    try:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in c.fetchall()]
        assert "companies" in tables
        assert "contacts" in tables

        c.execute("PRAGMA table_info(companies)")
        cols = {r[1]: r for r in c.fetchall()}
        assert cols["name"][2] == "TEXT"  # type
        assert cols["name"][3] == 1       # NOT NULL

        c.execute("PRAGMA foreign_key_list(contacts)")
        fks = c.fetchall()
        assert len(fks) == 1
        assert fks[0][2] == "companies"  # referenced table
        conn.close()
    finally:
        os.remove(path)


def test_insert_extracted_basic():
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [{"name": "Acme Corp", "website": "https://acme.com"}],
            "contacts": [{"company_id": "@last:companies", "email": "john@acme.com", "phone": "555-0100"}],
        }
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["companies"] == 1
        assert counts["contacts"] == 1

        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT id, name, website FROM companies").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "Acme Corp"

        rows = conn.execute("SELECT id, company_id, email FROM contacts").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == 1  # FK resolved to company id
        assert rows[0][2] == "john@acme.com"
        conn.close()
    finally:
        os.remove(path)


def test_insert_extracted_multiple_companies():
    """Multiple ref rows with matching counts → auto-fill fills @pos: references."""
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [
                {"name": "Alpha Inc", "website": "http://alpha.com"},
                {"name": "Beta LLC", "website": "http://beta.com"},
            ],
            "contacts": [
                {"company_id": None, "email": "alice@alpha.com", "phone": None},
                {"company_id": None, "email": "bob@beta.com", "phone": None},
            ],
        }
        _auto_fill_fks(schema, extracted)
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["companies"] == 2
        assert counts["contacts"] == 2  # @pos:companies:1 and @pos:companies:2
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT email, company_id FROM contacts ORDER BY email").fetchall()
        assert rows == [("alice@alpha.com", 1), ("bob@beta.com", 2)]
        conn.close()
    finally:
        os.remove(path)


def test_insert_extracted_skips_null_rows():
    """Rows where all non-FK columns are null should be skipped."""
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [{"name": "Test Corp", "website": None}],
            "contacts": [
                {"company_id": "@last:companies", "email": "real@test.com", "phone": None},
                {"company_id": "@last:companies", "email": None, "phone": None},
            ],
        }
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["companies"] == 1
        assert counts["contacts"] == 1

        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT email FROM contacts").fetchall()
        assert rows == [("real@test.com",)]
        conn.close()
    finally:
        os.remove(path)


def test_insert_skips_not_null_violation():
    """Row with NOT NULL violation should be skipped, not crash."""
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [{"name": "C", "website": None}],
            "contacts": [
                {"company_id": "@last:companies", "email": None, "phone": None},
            ],
        }
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["companies"] == 1
        assert counts["contacts"] == 0
    finally:
        os.remove(path)


def test_auto_fill_fk_single_ref():
    """Auto-fill should set @last: when ref table has exactly 1 row."""
    schema = _schema()
    extracted = {
        "companies": [{"name": "Solo Inc", "website": None}],
        "contacts": [
            {"company_id": None, "email": "solo@test.com", "phone": None},
        ],
    }
    _auto_fill_fks(schema, extracted)
    assert extracted["contacts"][0]["company_id"] == "@last:companies"


def test_auto_fill_fk_multiple_refs():
    """Auto-fill should NOT set @last: when ref table has >1 row."""
    schema = _schema()
    extracted = {
        "companies": [
            {"name": "A Inc", "website": None},
            {"name": "B Inc", "website": None},
        ],
        "contacts": [
            {"company_id": None, "email": "a@test.com", "phone": None},
        ],
    }
    _auto_fill_fks(schema, extracted)
    assert extracted["contacts"][0]["company_id"] is None  # unchanged


def test_auto_fill_fk_already_set():
    """Auto-fill should NOT overwrite existing FK values."""
    schema = _schema()
    extracted = {
        "companies": [{"name": "C Inc", "website": None}],
        "contacts": [
            {"company_id": 42, "email": "c@test.com", "phone": None},
        ],
    }
    _auto_fill_fks(schema, extracted)
    assert extracted["contacts"][0]["company_id"] == 42  # preserved


def test_query_data():
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [{"name": "Q Corp", "website": "http://q.com"}],
            "contacts": [{"company_id": "@last:companies", "email": "q@q.com", "phone": None}],
        }
        insert_extracted(schema.model_copy(update={"database": path}), extracted)

        result = query_data(schema.model_copy(update={"database": path}))
        assert "companies" in result
        assert "contacts" in result
        assert len(result["companies"]) == 1
        assert result["companies"][0]["name"] == "Q Corp"

        result = query_data(schema.model_copy(update={"database": path}), table_name="companies")
        assert "companies" in result
        assert "contacts" not in result
    finally:
        os.remove(path)


def test_insert_empty_extracted():
    """Empty extraction dict should result in zero counts."""
    schema = _schema()
    path = _write_db(schema)
    try:
        counts = insert_extracted(schema.model_copy(update={"database": path}), {})
        assert all(v == 0 for v in counts.values())
    finally:
        os.remove(path)


def test_insert_partial_tables():
    """Extraction missing some tables entirely should not crash."""
    schema = _schema()
    path = _write_db(schema)
    try:
        counts = insert_extracted(schema.model_copy(update={"database": path}), {
            "companies": [{"name": "Partial", "website": None}],
        })
        assert counts["companies"] == 1
        assert counts["contacts"] == 0
    finally:
        os.remove(path)


def test_last_ref_on_non_fk_column():
    """@last: on non-FK column should be treated as null, not crash."""
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [{"name": "@last:companies", "website": None}],
        }
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["companies"] == 0  # has_data false since name became null
    finally:
        os.remove(path)


def test_last_ref_unresolvable():
    """@last: referencing table with no rows should warn and set null."""
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "contacts": [{"company_id": "@last:companies", "email": "orphan@test.com", "phone": None}],
        }
        counts = insert_extracted(schema.model_copy(update={"database": path}), extracted)
        assert counts["contacts"] == 0  # FK unresolved → null → NOT NULL fail → skip
    finally:
        os.remove(path)


def test_query_data_limit():
    schema = _schema()
    path = _write_db(schema)
    try:
        extracted = {
            "companies": [
                {"name": "A", "website": None},
                {"name": "B", "website": None},
                {"name": "C", "website": None},
            ],
        }
        insert_extracted(schema.model_copy(update={"database": path}), extracted)

        result = query_data(schema.model_copy(update={"database": path}), table_name="companies", limit=2)
        assert len(result["companies"]) == 2
    finally:
        os.remove(path)
