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


def run_test(name: str, eml_path: str | Path,
             expect_tables: list[str] | None = None,
             expect_scores: dict | None = None):
    """Run an edge case extraction test.

    Args:
        name: test label
        eml_path: path to .eml file
        expect_tables: if set, require these tables to have non-null data
        expect_scores: dict with optional keys:
            min_certainty (float), max_spam (float) for legitimate content
            or max_certainty (float), min_spam (float) for garbage/no-match
    """
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

    if expect_scores:
        min_cert = expect_scores.get("min_certainty")
        max_cert = expect_scores.get("max_certainty")
        min_sp = expect_scores.get("min_spam")
        max_sp = expect_scores.get("max_spam")

        if min_cert is not None and certainty < min_cert:
            print(f"  FAIL: certainty {certainty:.2f} < min {min_cert}", flush=True)
            failed += 1
            results.append((name, "FAIL", f"certainty {certainty:.2f} < {min_cert}"))
            return
        if max_cert is not None and certainty > max_cert:
            print(f"  FAIL: certainty {certainty:.2f} > max {max_cert}", flush=True)
            failed += 1
            results.append((name, "FAIL", f"certainty {certainty:.2f} > {max_cert}"))
            return
        if min_sp is not None and spam < min_sp:
            print(f"  FAIL: spam {spam:.2f} < min {min_sp}", flush=True)
            failed += 1
            results.append((name, "FAIL", f"spam {spam:.2f} < {min_sp}"))
            return
        if max_sp is not None and spam > max_sp:
            print(f"  FAIL: spam {spam:.2f} > max {max_sp}", flush=True)
            failed += 1
            results.append((name, "FAIL", f"spam {spam:.2f} > {max_sp}"))
            return

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
    run_test("empty body", EML_DIR / "empty.eml")

    # 2. Irrelevant content (recipe) → low certainty, high spam
    run_test("irrelevant content", EML_DIR / "recipe.eml",
             expect_tables=[],
             expect_scores={"max_certainty": 0.4, "min_spam": 0.5})

    # 3. Multiple records → legitimate leads, high certainty, low spam
    run_test("multiple records", EML_DIR / "multi_lead.eml",
             expect_scores={"min_certainty": 0.5, "max_spam": 0.5})

    # 4. Partial data — company only
    run_test("company only", EML_DIR / "company_only.eml",
             expect_tables=["companies"],
             expect_scores={"min_certainty": 0.3, "max_spam": 0.5})

    # 5. Unicode / special chars
    run_test("unicode", EML_DIR / "unicode.eml",
             expect_tables=["companies", "leads"],
             expect_scores={"min_certainty": 0.4, "max_spam": 0.5})

    # 6. Minimal data
    run_test("minimal data", EML_DIR / "minimal.eml",
             expect_tables=["companies"],
             expect_scores={"min_certainty": 0.2, "max_spam": 0.6})

    # 7. No matching data at all → low certainty
    run_test("no match", EML_DIR / "no_match.eml",
             expect_tables=[],
             expect_scores={"max_certainty": 0.4, "min_spam": 0.3})

    # 8. Garbage / binary body → very low certainty, very high spam
    run_test("garbage body", EML_DIR / "garbage.eml",
             expect_tables=[],
             expect_scores={"max_certainty": 0.2, "min_spam": 0.7})

    # Summary
    print(f"\n{'='*40}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed+failed}")
    for name, status, msg in results:
        print(f"  {status:4s}  {name}" + (f"  ({msg})" if msg else ""))
