"""add postal code cache

Revision ID: 37aeb9a1552f
Revises: 8c964dc2a0e2
Create Date: 2026-09-18 16:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "37aeb9a1552f"
down_revision = "8c964dc2a0e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "postal_code_cache",
        sa.Column("zip_code", sa.String(length=8), nullable=False),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("complement", sa.String(length=255), nullable=True),
        sa.Column("district", sa.String(length=120), nullable=True),
        sa.Column("city_code", sa.String(length=7), nullable=False),
        sa.Column("city_name", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("country_code", sa.String(length=4), nullable=False),
        sa.Column("country_name", sa.String(length=60), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("zip_code"),
    )
    with op.batch_alter_table("postal_code_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_postal_code_cache_city_code"),
            ["city_code"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_postal_code_cache_fetched_at"),
            ["fetched_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_postal_code_cache_state"),
            ["state"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("postal_code_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_postal_code_cache_state"))
        batch_op.drop_index(batch_op.f("ix_postal_code_cache_fetched_at"))
        batch_op.drop_index(batch_op.f("ix_postal_code_cache_city_code"))
    op.drop_table("postal_code_cache")
