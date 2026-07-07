"""Edge case tests for email extraction pipeline."""

import json
import sqlite3
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from email_parser.email_input import from_eml, _get_body
from email_parser.schema import SchemaDef
from email_parser.extractor import extract_from_email
from email_parser.db import create_database, insert_extracted

SCHEMA = SchemaDef.from_yaml(Path(__file__).parent.parent / "inferred_schema.yaml")
MODEL = "llama3.1:8b"
EML_DIR = Path(__file__).parent / "edge_cases"
DB_PATH = Path(__file__).parent.parent / "sales_leads.db"

passed = 0
failed = 0
results = []


def _new_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    create_database(SCHEMA)


def test(name: str, eml_path: str | Path, expect_tables: list[str] | None = None):
    global passed, failed
    print(f"\n=== {name} ===", flush=True)
    _new_db()
    try:
        body = from_eml(eml_path)
    except Exception as e:
        print(f"  FAIL: could not parse .eml — {e}", flush=True)
        failed += 1
        results.append((name, "FAIL", str(e)))
        return

    if not body:
        print(f"  empty body", flush=True)
        if expect_tables:
            print(f"  FAIL: expected data in {expect_tables} but body was empty", flush=True)
            failed += 1
            results.append((name, "FAIL", "empty body"))
            return
        print(f"  PASS: empty body correctly handled", flush=True)
        passed += 1
        results.append((name, "PASS", "empty body"))
        return

    try:
        result = extract_from_email(SCHEMA, body, model=MODEL)
    except Exception as e:
        print(f"  FAIL: extraction crashed — {e}", flush=True)
        failed += 1
        results.append((name, "FAIL", f"extraction error: {e}"))
        return

    extracted = result["extracted"]
    certainty = result.get("certainty", 0.5)
    spam = result.get("spam", 0.5)
    print(f"  extracted: {json.dumps(extracted, indent=2)}", flush=True)
    print(f"  certainty: {certainty:.2f}, spam: {spam:.2f}", flush=True)

    if expect_tables is not None:
        missing = []
        for t in expect_tables:
            rows = extracted.get(t, [])
            has_data = any(
                v is not None
                for row in rows
                for k, v in row.items()
            )
            if not has_data:
                missing.append(t)
        if missing:
            print(f"  FAIL: expected data in {missing} but got none", flush=True)
            failed += 1
            results.append((name, "FAIL", f"missing tables: {missing}"))
            return

    try:
        counts = insert_extracted(SCHEMA, extracted)
    except Exception as e:
        print(f"  FAIL: insert crashed — {e}", flush=True)
        failed += 1
        results.append((name, "FAIL", f"insert error: {e}"))
        return

    print(f"  inserted: {counts}", flush=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for tname in SCHEMA.table_map():
        c.execute(f'SELECT COUNT(*) FROM "{tname}"')
        cnt = c.fetchone()[0]
        if cnt:
            print(f"    {tname}: {cnt} row(s)", flush=True)
    conn.close()

    print(f"  PASS", flush=True)
    passed += 1
    results.append((name, "PASS", ""))


if __name__ == "__main__":
    # 1. Empty body
    test("empty body", EML_DIR / "empty.eml")

    # 2. Irrelevant content
    test("irrelevant content", EML_DIR / "recipe.eml", expect_tables=[])

    # 3. Multiple records (AI may skip companies, leads should still not crash)
    test("multiple records", EML_DIR / "multi_lead.eml")

    # 4. Partial data — company only
    test("company only", EML_DIR / "company_only.eml",
         expect_tables=["companies"])

    # 5. Unicode / special chars
    test("unicode", EML_DIR / "unicode.eml",
         expect_tables=["companies", "leads"])

    # 6. Minimal data
    test("minimal data", EML_DIR / "minimal.eml",
         expect_tables=["companies"])

    # 7. No matching data at all
    test("no match", EML_DIR / "no_match.eml", expect_tables=[])

    # 8. Garbage / binary body
    test("garbage body", EML_DIR / "garbage.eml", expect_tables=[])

    # Summary
    print(f"\n{'='*40}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed+failed}")
    for name, status, msg in results:
        print(f"  {status:4s}  {name}" + (f"  ({msg})" if msg else ""))
