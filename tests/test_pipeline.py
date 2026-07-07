"""Pipeline integration tests — .eml → extraction → DB, with mocked Ollama."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from email_parser.email_input import from_eml
from email_parser.schema import SchemaDef
from email_parser.extractor import extract_from_email
from email_parser.db import create_database, insert_extracted, query_data

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
SCHEMA_PATH = FIXTURES / "schema" / "sales.yaml"
EDGE = HERE / "edge_cases"
EXPECTED = FIXTURES / "expected"

schema = SchemaDef.from_yaml(SCHEMA_PATH)


def _mock_ollama(fixture_name: str):
    """Return a mock for ollama.Client.chat that returns a fixed response."""
    path = EXPECTED / f"{fixture_name}.json"
    with open(path) as f:
        fixture = json.load(f)
    content = json.dumps(fixture)

    def mock_chat(self, **kwargs):
        return {"message": {"content": content}}

    return mock_chat


def _run_pipeline(eml_path: str | Path, fixture_name: str, db_path: str = "/tmp/_test_pipeline.db"):
    """Run the full pipeline with mocked AI and return (counts, db_rows)."""
    if os.path.exists(db_path):
        os.remove(db_path)
    s = schema.model_copy(update={"database": db_path})
    create_database(s)

    body = from_eml(eml_path)
    assert body, f"Empty body from {eml_path}"

    with patch("ollama.Client.chat", _mock_ollama(fixture_name)):
        result = extract_from_email(s, body)

    assert "extracted" in result, f"No 'extracted' key in result: {result}"
    assert "certainty" in result
    assert "spam" in result

    counts = insert_extracted(s, result["extracted"])
    rows = query_data(s)
    return result, counts, rows


def test_pipeline_company_only():
    """Company-only .eml → company + lead extracted with correct FK."""
    result, counts, rows = _run_pipeline(EDGE / "company_only.eml", "company_only")

    assert result["certainty"] == 0.9
    assert result["spam"] == 0.01
    assert counts["companies"] == 1
    assert counts["leads"] == 1

    comp = rows["companies"][0]
    assert comp["company_name"] == "MegaCorp Industries"
    assert comp["contact_person"] == "Jane Wilson"
    assert comp["email"] == "jane@megacorp.com"

    lead = rows["leads"][0]
    assert lead["company_id"] == comp["id"]  # FK resolved correctly
    assert lead["email"] == "jane@megacorp.com"


def test_pipeline_unicode():
    """Unicode email → characters preserved end-to-end."""
    result, counts, rows = _run_pipeline(EDGE / "unicode.eml", "unicode")

    assert counts["companies"] == 1
    assert counts["leads"] == 1

    comp = rows["companies"][0]
    assert comp["company_name"] == "Café Zürich AG"
    assert comp["contact_person"] == "José Fernández"
    assert comp["email"] == "josé@café.com"

    lead = rows["leads"][0]
    assert "€80,000" in lead["budget"]
    assert "Recommandation" in lead["lead_source"]


def test_pipeline_recipe_skipped():
    """Recipe email → high spam, low certainty → extracted results empty."""
    result, counts, rows = _run_pipeline(EDGE / "recipe.eml", "recipe")

    assert result["spam"] >= 0.9
    assert result["certainty"] <= 0.2
    # Empty extraction means zero counts
    assert all(v == 0 for v in counts.values())


def test_pipeline_empty_email():
    """Empty email → no body, no extraction needed."""
    s = schema.model_copy(update={"database": "/tmp/_test_empty.db"})
    if os.path.exists("/tmp/_test_empty.db"):
        os.remove("/tmp/_test_empty.db")
    create_database(s)
    body = from_eml(EDGE / "empty.eml")
    assert body == ""


def test_pipeline_multiple_companies_and_leads():
    """Verify FK resolution with multiple rows using explicit @last: markers."""
    db_path = "/tmp/_test_multi.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    s = schema.model_copy(update={"database": db_path})
    create_database(s)

    # Simulate AI output with explicit FK references
    extracted = {
        "companies": [
            {"company_name": "Alpha Inc", "contact_person": "Alice", "email": "a@alpha.com", "phone": None},
            {"company_name": "Beta Corp", "contact_person": "Bob", "email": "b@beta.com", "phone": None},
        ],
        "leads": [
            {"company_id": None, "contact_name": "Alice", "email": "a@alpha.com", "phone": None,
             "product_interest": None, "budget": None, "lead_source": None, "notes": None},
        ],
    }
    counts = insert_extracted(s, extracted)
    assert counts["companies"] == 2
    # Lead with null FK → auto-fill doesn't fire (>1 company) → NOT NULL → skip
    assert counts["leads"] == 0


def test_pipeline_fk_resolution_via_last():
    """Explicit @last:companies FK should resolve to the last inserted company."""
    db_path = "/tmp/_test_last.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    s = schema.model_copy(update={"database": db_path})
    create_database(s)

    extracted = {
        "companies": [
            {"company_name": "First Co", "contact_person": None, "email": None, "phone": None},
            {"company_name": "Second Co", "contact_person": None, "email": None, "phone": None},
        ],
        "leads": [
            {"company_id": "@last:companies", "contact_name": "Bob", "email": "b@second.com",
             "phone": None, "product_interest": None, "budget": None, "lead_source": None, "notes": None},
        ],
    }
    counts = insert_extracted(s, extracted)
    assert counts["companies"] == 2
    assert counts["leads"] == 1

    rows = query_data(s)
    lead = rows["leads"][0]
    second_company_id = rows["companies"][1]["id"]
    assert lead["company_id"] == second_company_id  # points to LAST company


def test_pipeline_certainty_spam_propagated():
    """Verify certainty/spam scores are returned by extract_from_email."""
    db_path = "/tmp/_test_cs.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    s = schema.model_copy(update={"database": db_path})
    create_database(s)

    body = from_eml(EDGE / "company_only.eml")
    with patch("ollama.Client.chat", _mock_ollama("company_only")):
        result = extract_from_email(s, body)

    assert isinstance(result["certainty"], (int, float))
    assert isinstance(result["spam"], (int, float))
    assert 0 <= result["certainty"] <= 1
    assert 0 <= result["spam"] <= 1
