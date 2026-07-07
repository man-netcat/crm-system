"""Unit tests for schema.py — SchemaDef, dependency ordering, YAML parsing."""

import tempfile
from pathlib import Path

from email_parser.schema import SchemaDef, ColumnDef, TableDef, ForeignKeyRef


def test_minimal_schema():
    s = SchemaDef.model_validate({
        "database": "test.db",
        "tables": [{"name": "widgets", "columns": [{"name": "name", "type": "TEXT"}]}],
    })
    assert s.database == "test.db"
    assert len(s.tables) == 1
    assert s.tables[0].name == "widgets"
    assert s.tables[0].columns[0].name == "name"


def test_sql_type_mapping():
    cases = [
        ("text", "TEXT"), ("TEXT", "TEXT"), ("Text", "TEXT"),
        ("integer", "INTEGER"), ("int", "INTEGER"),
        ("real", "REAL"), ("float", "REAL"), ("number", "REAL"),
        ("boolean", "INTEGER"), ("bool", "INTEGER"),
        ("date", "TEXT"), ("datetime", "TEXT"),
        ("unknown", "TEXT"),
    ]
    for input_type, expected in cases:
        col = ColumnDef(name="x", type=input_type)
        assert col.sql_type() == expected, f"{input_type} -> {col.sql_type()}, expected {expected}"


def test_dependency_order_single_table():
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [{"name": "a", "columns": [{"name": "x", "type": "TEXT"}]}],
    })
    assert s.dependency_order() == ["a"]


def test_dependency_order_simple():
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {"name": "parents", "columns": [{"name": "name", "type": "TEXT"}]},
            {
                "name": "children",
                "columns": [
                    {"name": "parent_id", "type": "INTEGER",
                     "foreign_key": {"table": "parents", "column": "id"}},
                ],
            },
        ],
    })
    assert s.dependency_order() == ["parents", "children"]


def test_dependency_order_reverse_input():
    """Dependency order should put parents first even if listed second."""
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {
                "name": "children",
                "columns": [
                    {"name": "parent_id", "type": "INTEGER",
                     "foreign_key": {"table": "parents", "column": "id"}},
                ],
            },
            {"name": "parents", "columns": [{"name": "name", "type": "TEXT"}]},
        ],
    })
    assert s.dependency_order() == ["parents", "children"]


def test_dependency_order_chain():
    """Three-level chain: grandparent → parent → child."""
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {"name": "grandparent", "columns": [{"name": "x", "type": "TEXT"}]},
            {
                "name": "parent",
                "columns": [{"name": "gp_id", "type": "INTEGER",
                             "foreign_key": {"table": "grandparent", "column": "id"}}],
            },
            {
                "name": "child",
                "columns": [{"name": "p_id", "type": "INTEGER",
                             "foreign_key": {"table": "parent", "column": "id"}}],
            },
        ],
    })
    assert s.dependency_order() == ["grandparent", "parent", "child"]


def test_dependency_order_circular():
    """Circular deps should not hang — batch fallback breaks the cycle."""
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {
                "name": "a",
                "columns": [{"name": "b_id", "type": "INTEGER",
                             "foreign_key": {"table": "b", "column": "id"}}],
            },
            {
                "name": "b",
                "columns": [{"name": "a_id", "type": "INTEGER",
                             "foreign_key": {"table": "a", "column": "id"}}],
            },
        ],
    })
    order = s.dependency_order()
    assert set(order) == {"a", "b"}  # both present
    assert len(order) == 2


def test_table_map():
    s = SchemaDef.model_validate({
        "database": "t.db",
        "tables": [
            {"name": "foo", "columns": [{"name": "x", "type": "TEXT"}]},
            {"name": "bar", "columns": [{"name": "y", "type": "INTEGER"}]},
        ],
    })
    m = s.table_map()
    assert set(m.keys()) == {"foo", "bar"}
    assert m["foo"].name == "foo"
    assert m["bar"].name == "bar"


def test_from_yaml():
    yaml_content = """
database: test.db
tables:
  - name: users
    columns:
      - name: email
        type: TEXT
        required: true
  - name: orders
    columns:
      - name: user_id
        type: INTEGER
        foreign_key:
          table: users
          column: id
        required: true
      - name: amount
        type: REAL
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        path = f.name
    try:
        s = SchemaDef.from_yaml(path)
        assert s.database == "test.db"
        assert len(s.tables) == 2
        assert s.tables[0].name == "users"
        assert s.tables[0].columns[0].required is True
        assert s.tables[1].columns[0].foreign_key.table == "users"
    finally:
        Path(path).unlink()


def test_foreign_key_default_column():
    """FK without explicit column defaults to 'id'."""
    ref = ForeignKeyRef.model_validate({"table": "users"})
    assert ref.column == "id"


def test_column_defaults():
    col = ColumnDef(name="test")
    assert col.type == "TEXT"
    assert col.description == ""
    assert col.required is False
    assert col.foreign_key is None


def test_required_column_sql():
    """sql_type() returns the SQL type only; NOT NULL is added by create_database."""
    col = ColumnDef(name="name", type="TEXT", required=True)
    assert col.sql_type() == "TEXT"
    assert col.required is True


def test_foreign_key_column():
    col = ColumnDef(
        name="user_id", type="INTEGER",
        foreign_key={"table": "users", "column": "id"},
    )
    assert col.foreign_key is not None
    assert col.foreign_key.table == "users"
    assert col.foreign_key.column == "id"
