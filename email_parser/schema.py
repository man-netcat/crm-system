from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


TYPE_MAP = {
    "text": "TEXT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "real": "REAL",
    "float": "REAL",
    "number": "REAL",
    "boolean": "INTEGER",
    "bool": "INTEGER",
    "date": "TEXT",
    "datetime": "TEXT",
}


class ForeignKeyRef(BaseModel):
    table: str
    column: str = "id"


class ColumnDef(BaseModel):
    name: str
    type: str = "TEXT"
    description: str = ""
    required: bool = False
    foreign_key: Optional[ForeignKeyRef] = None

    def sql_type(self) -> str:
        return TYPE_MAP.get(self.type.lower(), "TEXT")


class TableDef(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnDef] = Field(default_factory=list)


class SchemaDef(BaseModel):
    database: str = "extracted_data.db"
    tables: list[TableDef] = Field(default_factory=list)

    def table_map(self) -> dict[str, TableDef]:
        return {t.name: t for t in self.tables}

    def dependency_order(self) -> list[str]:
        names = [t.name for t in self.tables]
        deps: dict[str, set[str]] = {}
        for t in self.tables:
            deps[t.name] = set()
            for c in t.columns:
                if c.foreign_key:
                    deps[t.name].add(c.foreign_key.table)
        ordered = []
        remaining = set(names)
        while remaining:
            batch = {n for n in remaining if deps[n].issubset(set(ordered))}
            if not batch:
                batch = remaining
            ordered.extend(sorted(batch))
            remaining -= batch
        return ordered

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SchemaDef":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
