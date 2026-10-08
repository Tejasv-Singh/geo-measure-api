"""Add error code and details to files

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 00:28:54.612137
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("geo_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error_code", sa.String(length=50), nullable=True))
        batch_op.add_column(
            sa.Column(
                "error_details",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("geo_files", schema=None) as batch_op:
        batch_op.drop_column("error_details")
        batch_op.drop_column("error_code")
