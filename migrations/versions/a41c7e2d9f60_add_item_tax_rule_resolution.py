"""add item-level tax rule resolution and snapshots

Revision ID: a41c7e2d9f60
Revises: 5d2c4a19e7b1
Create Date: 2026-09-19 13:30:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa


revision = "a41c7e2d9f60"
down_revision = "5d2c4a19e7b1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("client_import_tax_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ncm_scope_type",
                sa.String(length=10),
                server_default="all",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "ncm_patterns",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "revision",
                sa.Integer(),
                server_default="1",
                nullable=False,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_client_import_tax_rules_ncm_scope_type"),
            ["ncm_scope_type"],
            unique=False,
        )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, ncm_pattern FROM client_import_tax_rules")
    ).mappings()
    for row in rows:
        pattern = "".join(
            character
            for character in str(row["ncm_pattern"] or "")
            if character.isdigit()
        )
        scope_type = (
            "all" if not pattern else "exact" if len(pattern) == 8 else "prefix"
        )
        connection.execute(
            sa.text(
                "UPDATE client_import_tax_rules "
                "SET ncm_scope_type = :scope_type, ncm_patterns = :patterns "
                "WHERE id = :rule_id"
            ),
            {
                "scope_type": scope_type,
                "patterns": json.dumps([pattern] if pattern else []),
                "rule_id": row["id"],
            },
        )

    with op.batch_alter_table("nfe_item_classifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tax_rule_snapshot", sa.JSON(), nullable=True))

    with op.batch_alter_table("nfe_draft_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tax_rule_snapshot", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("nfe_draft_items", schema=None) as batch_op:
        batch_op.drop_column("tax_rule_snapshot")

    with op.batch_alter_table("nfe_item_classifications", schema=None) as batch_op:
        batch_op.drop_column("tax_rule_snapshot")

    with op.batch_alter_table("client_import_tax_rules", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_client_import_tax_rules_ncm_scope_type")
        )
        batch_op.drop_column("revision")
        batch_op.drop_column("ncm_patterns")
        batch_op.drop_column("ncm_scope_type")
