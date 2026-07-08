import json
import re
import sqlite3

from .schema import SchemaDef


LAST_REF_RE = re.compile(r"^@last:(.+)$")
POS_REF_RE = re.compile(r"^@pos:(\w+):(\d+)$")


def _find_pk_col(table) -> str:
    for col in table.columns:
        if col.foreign_key:
            continue
        if col.type.upper() == "INTEGER" and col.name.endswith("_id"):
            return col.name
    return "id"


def create_database(schema: SchemaDef) -> str:
    db_path = schema.database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    order = schema.dependency_order()
    name_map = schema.table_map()

    for table_name in order:
        table = name_map[table_name]
        pk_col = None
        for col in table.columns:
            if col.foreign_key:
                continue
            if col.type.upper() == "INTEGER" and col.name.endswith("_id"):
                pk_col = col.name
                break
        if pk_col:
            cols = [f'"{pk_col}" INTEGER PRIMARY KEY AUTOINCREMENT']
        else:
            cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        fk_clauses = []
        for col in table.columns:
            if col.name == pk_col:
                continue
            nullable = " NOT NULL" if col.required else ""
            cols.append(f'"{col.name}" {col.sql_type()}{nullable}')
            if col.foreign_key:
                ref = col.foreign_key
                fk_clauses.append(
                    f'FOREIGN KEY ("{col.name}") REFERENCES "{ref.table}" ("{ref.column}")'
                )
        all_parts = cols + fk_clauses
        stmt = f'CREATE TABLE IF NOT EXISTS "{table.name}" ({", ".join(all_parts)})'
        cursor.execute(stmt)

    conn.commit()
    conn.close()
    return db_path


def _auto_fill_fks(schema: SchemaDef, extracted: dict[str, list[dict]]):
    """Fill null FK values:
    - @last:<table> when the referenced table has exactly one row
    - @pos:<table>:<N> when child rows match parent rows 1-to-1 in order
    """
    name_map = schema.table_map()
    for table in schema.tables:
        fk_cols = [c for c in table.columns if c.foreign_key]
        if not fk_cols:
            continue
        rows = extracted.get(table.name, [])
        if not rows:
            continue
        for col in fk_cols:
            ref_table = col.foreign_key.table
            ref_rows = extracted.get(ref_table, [])
            if not ref_rows:
                continue
            if len(ref_rows) == 1:
                marker = f"@last:{ref_table}"
                for row in rows:
                    if row.get(col.name) is None:
                        row[col.name] = marker
            elif len(ref_rows) == len(rows):
                for i, row in enumerate(rows):
                    if row.get(col.name) is None:
                        row[col.name] = f"@pos:{ref_table}:{i + 1}"


def insert_extracted(schema: SchemaDef, extracted: dict[str, list[dict]]) -> dict[str, int]:
    _auto_fill_fks(schema, extracted)

    db_path = schema.database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    name_map = schema.table_map()
    order = schema.dependency_order()
    ref_registry: dict[str, int] = {}
    pos_registry: dict[str, list[int]] = {}
    counts: dict[str, int] = {}

    for table_name in order:
        rows = extracted.get(table_name)
        if not rows:
            counts[table_name] = 0
            continue

        table = name_map[table_name]
        col_names = [c.name for c in table.columns]
        fk_cols = {c.name: c.foreign_key for c in table.columns if c.foreign_key}

        placeholders = ", ".join("?" for _ in col_names)
        cols_fmt = ", ".join(f'"{c}"' for c in col_names)
        stmt = f'INSERT INTO "{table_name}" ({cols_fmt}) VALUES ({placeholders})'

        count = 0
        for row in rows:
            values = []
            has_data = False
            for col_name in col_names:
                val = row.get(col_name)
                fk = fk_cols.get(col_name)
                # Coerce string → numeric when column expects INTEGER/REAL
                is_pk = _find_pk_col(table) == col_name
                if isinstance(val, str) and fk is None:
                    col_def = next((c for c in table.columns if c.name == col_name), None)
                    if col_def and col_def.type.upper() in ("INTEGER", "REAL", "NUMERIC"):
                        try:
                            val = float(val) if "." in val else int(val)
                        except (ValueError, TypeError):
                            if is_pk:
                                # PK value can't coerce to INTEGER — let AUTOINCREMENT handle it
                                val = None
                if isinstance(val, str) and LAST_REF_RE.match(val):
                    if not fk:
                        print(
                            f"Warning: @last: reference on non-FK column \"{table_name}\".\"{col_name}\" — treating as null",
                            flush=True,
                        )
                        val = None
                    else:
                        m = LAST_REF_RE.match(val)
                        ref_table = m.group(1)
                        resolved = ref_registry.get(ref_table)
                        if resolved is None:
                            # Fallback: look up the referenced table's PK value in extracted data and search DB
                            ref_table_def = name_map.get(ref_table)
                            pk_col = _find_pk_col(ref_table_def) if ref_table_def else "id"
                            ref_rows_data = extracted.get(ref_table, [])
                            ref_pk_val = None
                            for rr in ref_rows_data:
                                rv = rr.get(pk_col)
                                if rv is not None:
                                    ref_pk_val = rv
                                    break
                            if ref_pk_val is not None:
                                cursor.execute(f'SELECT rowid FROM "{ref_table}" WHERE "{pk_col}" = ?', (ref_pk_val,))
                                match = cursor.fetchone()
                                if match:
                                    resolved = match[0]
                            if resolved is None:
                                print(
                                    f"Warning: @last:{ref_table} referenced but no rows inserted and no DB match for {pk_col}={ref_pk_val} — treating as null",
                                    flush=True,
                                )
                                val = None
                            else:
                                val = resolved
                        else:
                            val = resolved
                elif isinstance(val, str) and POS_REF_RE.match(val):
                    if not fk:
                        print(
                            f"Warning: @pos: reference on non-FK column \"{table_name}\".\"{col_name}\" — treating as null",
                            flush=True,
                        )
                        val = None
                    else:
                        m = POS_REF_RE.match(val)
                        ref_table = m.group(1)
                        idx = int(m.group(2)) - 1
                        if idx < 0:
                            print(f"Warning: @pos:{ref_table}:{m.group(2)} has invalid index — treating as null", flush=True)
                            val = None
                        else:
                            all_ids = pos_registry.get(ref_table, [])
                            if idx < len(all_ids):
                                val = all_ids[idx]
                            else:
                                print(
                                    f"Warning: @pos:{ref_table}:{m.group(2)} out of range (only {len(all_ids)} rows inserted in {ref_table}) — treating as null",
                                    flush=True,
                                )
                                val = None
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                if val is not None:
                    has_data = True
                values.append(val)

            if not has_data:
                continue

            try:
                cursor.execute(stmt, values)
                count += 1
                ref_registry[table_name] = cursor.lastrowid
                pos_registry.setdefault(table_name, []).append(cursor.lastrowid)
            except sqlite3.Error as e:
                vals_display = {col_names[i]: values[i] for i in range(len(col_names))}
                print(
                    f"Warning: Skipping row in \"{table_name}\" — {e}\n  Data: {vals_display}",
                    flush=True,
                )

        counts[table_name] = count

    conn.commit()
    conn.close()
    return counts


def query_data(
    schema: SchemaDef, table_name: str | None = None, limit: int = 50
) -> dict[str, list[dict]]:
    db_path = schema.database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    tables = [t.name for t in schema.tables]
    if table_name:
        tables = [t for t in tables if t == table_name]

    results = {}
    for t in tables:
        cursor.execute(f'SELECT * FROM "{t}" LIMIT ?', (limit,))
        rows = [dict(row) for row in cursor.fetchall()]
        results[t] = rows

    conn.close()
    return results
