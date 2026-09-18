"""add local encrypted certificate provider

Revision ID: 5d2c4a19e7b1
Revises: 37aeb9a1552f
Create Date: 2026-09-18 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "5d2c4a19e7b1"
down_revision = "37aeb9a1552f"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "fiscal_credential_provider"
OLD_VALUES = ("gcp_secret_manager", "gcp_cloud_storage")
NEW_VALUES = (
    "local_encrypted_file",
    "gcp_secret_manager",
    "gcp_cloud_storage",
)


def _replace_constraint(values):
    condition = "provider IN ({})".format(
        ", ".join(f"'{value}'" for value in values)
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(
            "fiscal_certificates",
            schema=None,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.create_check_constraint(CONSTRAINT_NAME, condition)
        return

    op.drop_constraint(
        CONSTRAINT_NAME,
        "fiscal_certificates",
        type_="check",
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "fiscal_certificates",
        condition,
    )


def upgrade():
    _replace_constraint(NEW_VALUES)


def downgrade():
    connection = op.get_bind()
    local_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM fiscal_certificates "
            "WHERE provider = 'local_encrypted_file'"
        )
    ).scalar_one()
    if local_count:
        raise RuntimeError(
            "Não é possível remover o provider local enquanto existirem "
            "certificados cadastrados nele."
        )
    _replace_constraint(OLD_VALUES)
