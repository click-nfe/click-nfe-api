"""Configure CIF presentation for new document plans, retaining old plans' format.

Revision ID: 91c45e62a83f
Revises: f7e6a1c42d90
"""
from alembic import op
import sqlalchemy as sa

revision = "91c45e62a83f"
down_revision = "f7e6a1c42d90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "nfe_document_plans",
        sa.Column(
            "include_international_costs_in_product",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "nfe_document_plans",
        "include_international_costs_in_product",
        server_default=sa.true(),
    )


def downgrade():
    op.drop_column("nfe_document_plans", "include_international_costs_in_product")
