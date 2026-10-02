"""Add module access tags and preserve existing member access.

Revision ID: f4b8c2d901ae
Revises: e62a7b9c104d
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b8c2d901ae"
down_revision = "e62a7b9c104d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("access_tags", sa.JSON(), nullable=False, server_default="[]"))
    op.execute("UPDATE users SET access_tags = '[\"clientes\",\"processos\",\"emissao\",\"configuracoes\"]'")


def downgrade():
    op.drop_column("users", "access_tags")
