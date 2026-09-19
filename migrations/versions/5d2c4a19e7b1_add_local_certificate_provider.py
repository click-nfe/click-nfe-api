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
FINGERPRINT_CONSTRAINT_NAME = "uq_fiscal_certificate_client_fingerprint"
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
    _clear_duplicate_fingerprints()
    with op.batch_alter_table("fiscal_certificates", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            FINGERPRINT_CONSTRAINT_NAME,
            [
                "organization_id",
                "client_id",
                "environment",
                "certificate_fingerprint_sha256",
            ],
        )


def _clear_duplicate_fingerprints():
    """Mantém um fingerprint por cliente/ambiente antes da nova unicidade."""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, organization_id, client_id, environment, "
            "certificate_fingerprint_sha256, is_active, updated_at "
            "FROM fiscal_certificates "
            "WHERE certificate_fingerprint_sha256 IS NOT NULL "
            "ORDER BY organization_id, client_id, environment, "
            "certificate_fingerprint_sha256, is_active DESC, updated_at DESC"
        )
    ).mappings()
    seen = set()
    for row in rows:
        key = (
            str(row["organization_id"]),
            str(row["client_id"]),
            row["environment"],
            row["certificate_fingerprint_sha256"],
        )
        if key not in seen:
            seen.add(key)
            continue
        connection.execute(
            sa.text(
                "UPDATE fiscal_certificates SET "
                "certificate_fingerprint_sha256 = NULL, "
                "status = 'pending_validation', is_active = false, "
                "validation_error = :message "
                "WHERE id = :certificate_id"
            ),
            {
                "certificate_id": row["id"],
                "message": (
                    "Fingerprint duplicado normalizado durante migration; "
                    "valide o certificado novamente."
                ),
            },
        )


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
    with op.batch_alter_table("fiscal_certificates", schema=None) as batch_op:
        batch_op.drop_constraint(
            FINGERPRINT_CONSTRAINT_NAME,
            type_="unique",
        )
    _replace_constraint(OLD_VALUES)
