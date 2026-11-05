"""Create named simplified templates and preserve each client's existing configuration.

Revision ID: f7e6a1c42d90
Revises: b74f62a9c381
"""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "f7e6a1c42d90"
down_revision = "b74f62a9c381"
branch_labels = None
depends_on = None


def upgrade():
    _provider_constraint(include_manual=True)
    op.create_table(
        "simplified_issuance_templates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("client_id", sa.UUID(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_simplified_templates_org_client", "simplified_issuance_templates", ["organization_id", "client_id"])
    connection = op.get_bind()
    clients_table = sa.table("clients", sa.column("id", sa.UUID()), sa.column("organization_id", sa.UUID()), sa.column("simplified_issuance_config", sa.JSON()), sa.column("simplified_issuance_updated_at", sa.DateTime()))
    clients = connection.execute(sa.select(clients_table).where(clients_table.c.simplified_issuance_config.is_not(None)))
    table = sa.table("simplified_issuance_templates", sa.column("id", sa.UUID()), sa.column("organization_id", sa.UUID()), sa.column("client_id", sa.UUID()), sa.column("name", sa.String()), sa.column("description", sa.String()), sa.column("config", sa.JSON()), sa.column("active", sa.Boolean()), sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()))
    from datetime import datetime
    for client in clients:
        connection.execute(table.insert().values(id=uuid.uuid4(), organization_id=client.organization_id, client_id=client.id, name="Configuração existente", description="Modelo preservado do cadastro anterior.", config=client.simplified_issuance_config, active=True, created_at=client.simplified_issuance_updated_at or datetime.utcnow(), updated_at=client.simplified_issuance_updated_at or datetime.utcnow()))


def downgrade():
    # Preserve the original client column and any existing processes on downgrade.
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM duimp_snapshots WHERE source_provider = 'manual_spreadsheet'")).scalar_one()
    if count:
        raise RuntimeError("Há capturas manuais da planilha; não é possível reverter o provedor.")
    op.drop_index("ix_simplified_templates_org_client", table_name="simplified_issuance_templates")
    op.drop_table("simplified_issuance_templates")
    _provider_constraint(include_manual=False)


def _provider_constraint(*, include_manual):
    values = ("portal_unico", "serpro", "gett", "sefaz", "private_fiscal_api")
    if include_manual:
        values += ("manual_spreadsheet",)
    condition = "source_provider IN ({})".format(", ".join(f"'{value}'" for value in values))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("duimp_snapshots", recreate="always") as batch:
            batch.drop_constraint("duimp_snapshot_source_provider", type_="check")
            batch.create_check_constraint("duimp_snapshot_source_provider", condition)
    else:
        op.drop_constraint("duimp_snapshot_source_provider", "duimp_snapshots", type_="check")
        op.create_check_constraint("duimp_snapshot_source_provider", "duimp_snapshots", condition)
