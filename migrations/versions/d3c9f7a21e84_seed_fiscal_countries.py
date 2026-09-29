"""Seed the versioned NF-e country catalog.

Revision ID: d3c9f7a21e84
Revises: c8f4d1e7a2b9
"""

import csv
from datetime import datetime
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "d3c9f7a21e84"
down_revision = "c8f4d1e7a2b9"
branch_labels = None
depends_on = None

CATALOG = Path(__file__).resolve().parents[1] / "data" / "fiscal_countries_2026-09-29.csv"
EXPECTED_ROWS = 253


def _load_catalog():
    with CATALOG.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"Catálogo fiscal incompleto: {len(rows)} países (esperados {EXPECTED_ROWS}).")
    countries = {}
    for row in rows:
        code = (row["bacen_code"] or "").strip()
        name = (row["name"] or "").strip()
        if (
            len(code) != 4 or not code.isdigit() or code == "0000"
            or not name or len(name) > 120 or code in countries
        ):
            raise ValueError(f"Código/nome BACEN inválido ou duplicado: {code!r}.")
        countries[code] = name
    if countries.get("1058") != "BRASIL" or countries.get("1600") != "CHINA, REPUBLICA POPULAR":
        raise ValueError("O snapshot não contém as referências fiscais esperadas.")
    return countries


def upgrade():
    # Load and validate the full file before touching the database. The migration
    # runs without network access inside the existing Compose migrate service.
    countries = _load_catalog()
    bind = op.get_bind()
    table = sa.table(
        "fiscal_countries",
        sa.column("bacen_code", sa.String(4)),
        sa.column("name", sa.String(120)),
        sa.column("iso_alpha_2", sa.String(2)),
        sa.column("iso_alpha_3", sa.String(3)),
        sa.column("valid_from", sa.Date()),
        sa.column("valid_until", sa.Date()),
        sa.column("active", sa.Boolean()),
        sa.column("updated_at", sa.DateTime()),
    )
    existing = {
        row.bacen_code: (row.name, row.active)
        for row in bind.execute(sa.select(table.c.bacen_code, table.c.name, table.c.active))
    }
    now = datetime.utcnow()
    for code, name in countries.items():
        current = existing.get(code)
        if current is None:
            bind.execute(table.insert().values(
                bacen_code=code, name=name, iso_alpha_2=None,
                iso_alpha_3=None, valid_from=None, valid_until=None,
                active=True, updated_at=now,
            ))
        elif current != (name, True):
            # Preserve ISO codes and validity windows already obtained from TABX.
            bind.execute(
                table.update().where(table.c.bacen_code == code).values(
                    name=name, active=True, updated_at=now,
                )
            )


def downgrade():
    # Country records may have existed before this revision or be referenced by
    # invoices/drafts. A downgrade must not delete business reference data.
    pass
