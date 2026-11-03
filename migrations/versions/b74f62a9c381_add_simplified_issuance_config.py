"""Persist client simplified issuance configuration and immutable process copy.

Revision ID: b74f62a9c381
Revises: f4b8c2d901ae
"""
from alembic import op
import sqlalchemy as sa

revision = "b74f62a9c381"
down_revision = "f4b8c2d901ae"
branch_labels = None
depends_on = None

SOURCE_CONSTRAINT = "import_process_source"
OLD_SOURCES = ("manual", "portal_unico", "gett_import", "api")
NEW_SOURCES = (*OLD_SOURCES, "simplified")


def _replace_source_constraint(values):
    condition = "source IN ({})".format(", ".join(f"'{value}'" for value in values))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("import_processes", recreate="always") as batch:
            batch.drop_constraint(SOURCE_CONSTRAINT, type_="check")
            batch.create_check_constraint(SOURCE_CONSTRAINT, condition)
    else:
        op.drop_constraint(SOURCE_CONSTRAINT, "import_processes", type_="check")
        op.create_check_constraint(SOURCE_CONSTRAINT, "import_processes", condition)


def upgrade():
    _replace_source_constraint(NEW_SOURCES)
    op.add_column("clients", sa.Column("simplified_issuance_config", sa.JSON(), nullable=True))
    op.add_column("clients", sa.Column("simplified_issuance_updated_at", sa.DateTime(), nullable=True))
    op.add_column("import_processes", sa.Column("simplified_config_snapshot", sa.JSON(), nullable=True))
    op.add_column("import_processes", sa.Column("simplified_item_links", sa.JSON(), nullable=True))


def downgrade():
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM import_processes WHERE source = 'simplified'")).scalar_one()
    if count:
        raise RuntimeError("Remova os processos de emissão simplificada antes de reverter esta migration.")
    op.drop_column("import_processes", "simplified_item_links")
    op.drop_column("import_processes", "simplified_config_snapshot")
    op.drop_column("clients", "simplified_issuance_updated_at")
    op.drop_column("clients", "simplified_issuance_config")
    _replace_source_constraint(OLD_SOURCES)
