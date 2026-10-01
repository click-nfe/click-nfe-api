"""Create and seed production NF-e authorizers for all 27 states.

Revision ID: e62a7b9c104d
Revises: d3c9f7a21e84
"""

import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from alembic import op
import sqlalchemy as sa


revision = "e62a7b9c104d"
down_revision = "d3c9f7a21e84"
branch_labels = None
depends_on = None

CATALOG = Path(__file__).resolve().parents[1] / "data" / "nfe_sefaz_endpoints_production_2026-10-01.csv"
EXPECTED_CUFS = {
    "11", "12", "13", "14", "15", "16", "17", "21", "22", "23", "24", "25", "26", "27", "28",
    "29", "31", "32", "33", "35", "41", "42", "43", "50", "51", "52", "53",
}


def _load_catalog():
    with CATALOG.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    if len(records) != 27 or {r["cuf"] for r in records} != EXPECTED_CUFS:
        raise ValueError("Catálogo SEFAZ exige os 27 cUFs, sem duplicatas.")
    if len({r["uf"] for r in records}) != 27:
        raise ValueError("Siglas UF duplicadas no catálogo SEFAZ.")
    for row in records:
        if row["authorizer"] not in {"SVRS", "SVAN", row["uf"]}:
            raise ValueError(f"Autorizador inválido para {row['uf']}.")
        for field in ("authorization_url", "receipt_url", "protocol_url"):
            url = urlsplit(row[field])
            if url.scheme != "https" or not url.netloc or url.username or url.password or url.fragment:
                raise ValueError(f"URL de produção inválida para {row['uf']}: {field}.")
    return records


def upgrade():
    records = _load_catalog()  # Validate the bundled snapshot before writing to the DB.
    table = op.create_table(
        "sefaz_endpoints",
        sa.Column("cuf", sa.String(2), primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False, unique=True),
        sa.Column("authorizer", sa.String(8), nullable=False),
        sa.Column("authorization_url", sa.String(500), nullable=False),
        sa.Column("receipt_url", sa.String(500), nullable=False),
        sa.Column("protocol_url", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    now = datetime.utcnow()
    op.bulk_insert(table, [dict(row, active=True, updated_at=now) for row in records])


def downgrade():
    op.drop_table("sefaz_endpoints")
