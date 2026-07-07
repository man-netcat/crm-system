"""Unit tests for extractor.py — prompt building, relationship guide, response wrapping."""

from email_parser.schema import SchemaDef
from email_parser.extractor import _build_relationship_guide, EXTRACTION_PROMPT


def _schema():
    return SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {"name": "companies", "columns": [{"name": "name", "type": "TEXT"}]},
            {
                "name": "contacts",
                "columns": [
                    {"name": "email", "type": "TEXT"},
                    {
                        "name": "company_id",
                        "type": "INTEGER",
                        "foreign_key": {"table": "companies", "column": "id"},
                    },
                ],
            },
            {
                "name": "orders",
                "columns": [
                    {"name": "total", "type": "REAL"},
                    {
                        "name": "contact_id",
                        "type": "INTEGER",
                        "foreign_key": {"table": "contacts", "column": "id"},
                    },
                ],
            },
        ],
    })


def test_build_relationship_guide():
    guide = _build_relationship_guide(_schema())
    assert 'contacts"."company_id"' in guide
    assert '"companies"."id"' in guide
    assert '@last:companies' in guide
    assert 'orders"."contact_id"' in guide
    assert '"contacts"."id"' in guide
    assert '@last:contacts' in guide
    assert "FOREIGN KEY RELATIONSHIPS:" in guide


def test_build_relationship_guide_no_fks():
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [{"name": "a", "columns": [{"name": "x", "type": "TEXT"}]}],
    })
    assert _build_relationship_guide(s) == ""


def test_extraction_prompt_contains_sections():
    s = _schema()
    guide = _build_relationship_guide(s)
    prompt = EXTRACTION_PROMPT.format(
        schema_json=s.model_dump_json(indent=2),
        relationship_guide=guide,
        email_content="Test email body",
    )
    assert "DATABASE SCHEMA:" in prompt
    assert "FOREIGN KEY RELATIONSHIPS:" in prompt
    assert "EMAIL CONTENT:" in prompt
    assert "INSTRUCTIONS:" in prompt
    assert "certainty" in prompt
    assert "spam" in prompt
    assert "extracted" in prompt
    assert "Test email body" in prompt


def test_extraction_prompt_requires_json_structure():
    """Verify the prompt instructs the AI to return the correct JSON shape."""
    prompt = EXTRACTION_PROMPT.format(
        schema_json="{}",
        relationship_guide="",
        email_content="test",
    )
    # Should mention the three required fields
    assert '"extracted"' in prompt
    assert '"certainty"' in prompt
    assert '"spam"' in prompt


def test_extraction_prompt_fk_instruction():
    """Prompt should tell AI to use @last: for FK columns."""
    s = _schema()
    guide = _build_relationship_guide(s)
    prompt = EXTRACTION_PROMPT.format(
        schema_json=s.model_dump_json(indent=2),
        relationship_guide=guide,
        email_content="test",
    )
    assert "@last:" in prompt
    assert "foreign key" in prompt.lower() or "FOREIGN KEY" in prompt


def test_extraction_prompt_no_hallucination():
    """Prompt should tell AI to skip irrelevant content."""
    prompt = EXTRACTION_PROMPT.format(
        schema_json="{}",
        relationship_guide="",
        email_content="recipe content",
    )
    assert "irrelevant" in prompt.lower() or "nothing to do" in prompt.lower()
