"""End-to-end tests using only AI-inferred schemas — no hardcoded expectations.

Each scenario runs: prompt → AI schema → AI extraction → DB insert.
Success means no crashes and data lands in the database.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from email_parser.schema import SchemaDef
from email_parser.db import create_database, insert_extracted
from email_parser.extractor import generate_schema_from_prompt, extract_from_email

MODEL = "llama3.1:8b"

DOMAINS = [
    {
        "name": "support_tickets",
        "prompt": (
            "Track customer support tickets: each ticket has a customer name, "
            "issue description, priority (high/medium/low), status "
            "(open/in progress/resolved), and an assigned agent name."
        ),
        "email": "Login page returns 502 since this morning. Priority: High. Status: Open. Bob Smith, MegaCorp.",
    },
    {
        "name": "job_applications",
        "prompt": (
            "Track job applications from email. Each applicant has a name, "
            "email, and phone. Positions have a title and department. "
            "Applications link an applicant to a position."
        ),
        "email": "Applying for Senior Python Developer in Engineering. Name: Jane Doe. Phone: 555-123-4567. jane@email.com",
    },
    {
        "name": "event_registrations",
        "prompt": (
            "Extract event registrations from emails. Registrations have "
            "attendee name, email, company, ticket type "
            "(vip/standard/student), and event. Events have name, date, "
            "and location."
        ),
        "email": "Alice Johnson, alice@example.com, TechCorp. VIP ticket for DevCon 2026 on March 15 at Moscone Center.",
    },
    {
        "name": "inventory_orders",
        "prompt": (
            "Track purchase orders sent to suppliers. Suppliers have a "
            "company name, contact, and phone. Purchase orders have PO "
            "number, order date, supplier, and delivery date. Order items "
            "have product name, quantity, and unit price."
        ),
        "email": "PO-2026-0042 from Acme Widgets. Order date 2026-01-15, delivery 2026-02-01. Items: Widget Alpha x100 @ $12.50, Widget Beta x50 @ $24.00",
    },
    {
        "name": "real_estate_inquiries",
        "prompt": (
            "Extract real estate leads from emails. Leads have prospect "
            "name, phone, email, property type (house/condo/land/commercial), "
            "budget range, and neighborhood. Properties have address, "
            "listing price, and agent name."
        ),
        "email": "Tom Smith interested in a house in Pacific Heights. Budget $1.5M-$2M. tom@email.com, 415-555-1234.",
    },
    {
        "name": "project_tasks",
        "prompt": (
            "Track project tasks from email updates. Each task has a title, "
            "description, priority, status (todo/in-progress/done), "
            "due date, and assignee. Projects have a name and lead. "
            "Tasks belong to a project."
        ),
        "email": "Project: Website Redesign. Lead: Sarah Connor. New task: Migrate to PostgreSQL. Priority High. Status In Progress. Due 2026-02-28. Assignee: Mike.",
    },
    {
        "name": "expense_reports",
        "prompt": (
            "Extract expense report data from emails. Employees have name, "
            "department, and employee ID. Expenses have employee, category "
            "(travel/meals/supplies/software), amount, date, and description."
        ),
        "email": "John Doe, Sales, EMP-0042. Travel $1250 on Jan 10 - flight to NYC. Meals $85.50 on Jan 10 - client dinner. Software $299 on Jan 15 - Zoom.",
    },
    {
        "name": "medical_appointments",
        "prompt": (
            "Track medical appointment requests from emails. Patients have "
            "full name, DOB, phone, and email. Appointments have patient, "
            "requested date, reason, doctor name, and status "
            "(pending/confirmed/cancelled/completed)."
        ),
        "email": "Patient: Emily Watson, DOB 1985-04-12, 312-555-7890, emily@email.com. Request for annual checkup on Feb 20 with Dr. House. Status: Pending.",
    },
    {
        "name": "restaurant_reservations",
        "prompt": (
            "Track restaurant reservations from email inquiries. Guests have "
            "name, phone, and email. Reservations have guest, number of "
            "guests, date/time, special requests, and table number."
        ),
        "email": "David Lee, 212-555-3456, david@email.com. Reservation for 4 on Friday Feb 14 at 7:30 PM. Nut allergy.",
    },
    {
        "name": "freelance_invoices",
        "prompt": (
            "Extract freelance invoice data from emails. Clients have "
            "company name, contact person, and email. Invoices have number, "
            "client, issue date, due date, total, and status "
            "(draft/sent/paid/overdue). Line items have invoice, service, "
            "hourly rate, and hours."
        ),
        "email": "Client: ClientCo Inc, Jane Manager, jane@clientco.com. Invoice INV-2026-001, issued Jan 1, due Jan 31, $11,250. Status Sent. Items: Frontend dev 40h @ $150, Backend API 25h @ $150, DB setup 10h @ $150.",
    },
]


def run(name: str, prompt: str, email: str) -> list[str]:
    errors = []

    # Step 1: Generate schema
    try:
        schema_yaml = generate_schema_from_prompt(prompt)
        schema = SchemaDef.from_yaml_str(schema_yaml)
    except Exception as e:
        return [f"schema gen: {e}"]

    # Step 2: Create database
    db = f"/tmp/_test_e2e_{name}.db"
    try:
        schema.database = db
        create_database(schema)
    except Exception as e:
        Path(db).unlink(missing_ok=True)
        return [f"db create: {e}"]

    # Step 3: Extract
    try:
        result = extract_from_email(schema, email, model=MODEL)
        extracted = result.get("extracted", {})
    except Exception as e:
        Path(db).unlink(missing_ok=True)
        return [f"extraction: {e}"]

    # Step 4: Insert
    try:
        counts = insert_extracted(schema, extracted)
    except Exception as e:
        Path(db).unlink(missing_ok=True)
        return [f"insert: {e}"]

    # Step 5: Verify at least one row exists somewhere
    try:
        conn = sqlite3.connect(db)
        c = conn.cursor()
        total = 0
        for t in schema.table_map():
            c.execute(f'SELECT COUNT(*) FROM "{t}"')
            total += c.fetchone()[0]
        conn.close()
        if total == 0:
            errors.append("no rows in any table")
    except Exception as e:
        errors.append(f"verify: {e}")
    finally:
        Path(db).unlink(missing_ok=True)

    return errors


if __name__ == "__main__":
    passed = 0
    failed = 0

    for d in DOMAINS:
        name = d["name"]
        print(f"  {name}...", end=" ", flush=True)
        errors = run(name, d["prompt"], d["email"])
        if errors:
            print("FAIL")
            for e in errors:
                print(f"    {e}")
            failed += 1
        else:
            print("OK")
            passed += 1

    print()
    print(f"  {passed}/{len(DOMAINS)} pipelines passed cleanly")
    print()
