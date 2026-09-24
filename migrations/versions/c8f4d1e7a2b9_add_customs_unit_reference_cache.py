"""add customs unit reference cache

Revision ID: c8f4d1e7a2b9
Revises: a41c7e2d9f60
Create Date: 2026-09-24 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c8f4d1e7a2b9"
down_revision = "a41c7e2d9f60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fiscal_customs_units",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("municipality_code", sa.String(length=7), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    with op.batch_alter_table("fiscal_customs_units", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_fiscal_customs_units_active"), ["active"])
        batch_op.create_index(batch_op.f("ix_fiscal_customs_units_description"), ["description"])
        batch_op.create_index(batch_op.f("ix_fiscal_customs_units_fetched_at"), ["fetched_at"])
        batch_op.create_index(batch_op.f("ix_fiscal_customs_units_municipality_code"), ["municipality_code"])
        batch_op.create_index(batch_op.f("ix_fiscal_customs_units_state"), ["state"])
        batch_op.create_index(
            "ix_fiscal_customs_units_state_description",
            ["state", "description"],
        )


def downgrade():
    with op.batch_alter_table("fiscal_customs_units", schema=None) as batch_op:
        batch_op.drop_index("ix_fiscal_customs_units_state_description")
        batch_op.drop_index(batch_op.f("ix_fiscal_customs_units_state"))
        batch_op.drop_index(batch_op.f("ix_fiscal_customs_units_municipality_code"))
        batch_op.drop_index(batch_op.f("ix_fiscal_customs_units_fetched_at"))
        batch_op.drop_index(batch_op.f("ix_fiscal_customs_units_description"))
        batch_op.drop_index(batch_op.f("ix_fiscal_customs_units_active"))
    op.drop_table("fiscal_customs_units")
