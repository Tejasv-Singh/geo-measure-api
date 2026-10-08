"""Create file, layer, feature and measurement tables

Revision ID: 0001
Revises:
Create Date: 2026-10-09 00:04:20.374627
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geo_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                name="filestatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_crs", sa.Text(), nullable=True),
        sa.Column("source_crs", sa.String(length=255), nullable=True),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("layer_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geo_files")),
    )
    with op.batch_alter_table("geo_files", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_geo_files_sha256"), ["sha256"], unique=False)
        batch_op.create_index(batch_op.f("ix_geo_files_status"), ["status"], unique=False)

    op.create_table(
        "layers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("crs_label", sa.String(length=255), nullable=False),
        sa.Column("crs_wkt", sa.Text(), nullable=False),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["geo_files.id"],
            name=op.f("fk_layers_file_id_geo_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_layers")),
    )
    with op.batch_alter_table("layers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_layers_file_id"), ["file_id"], unique=False)

    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("layer_id", sa.Integer(), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=False),
        sa.Column("geometry_type", sa.String(length=30), nullable=True),
        sa.Column(
            "geometry",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "properties",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["geo_files.id"],
            name=op.f("fk_features_file_id_geo_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["layer_id"],
            ["layers.id"],
            name=op.f("fk_features_layer_id_layers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_features")),
        sa.UniqueConstraint(
            "file_id", "feature_index", name=op.f("uq_features_file_id_feature_index")
        ),
    )
    with op.batch_alter_table("features", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_features_layer_id"), ["layer_id"], unique=False)

    op.create_table(
        "measurements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("AREA", "LENGTH", "NONE", name="measurementkind", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(length=10), nullable=True),
        sa.Column("projected_crs", sa.String(length=255), nullable=True),
        sa.Column("method", sa.String(length=20), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OK",
                "NOT_APPLICABLE",
                "UNSUPPORTED",
                "INVALID_GEOMETRY",
                "EMPTY",
                name="measurementstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("geodesic_value", sa.Double(), nullable=True),
        sa.Column("deviation_pct", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["feature_id"],
            ["features.id"],
            name=op.f("fk_measurements_feature_id_features"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurements")),
    )
    with op.batch_alter_table("measurements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_measurements_feature_id"), ["feature_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_measurements_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("measurements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_measurements_status"))
        batch_op.drop_index(batch_op.f("ix_measurements_feature_id"))

    op.drop_table("measurements")
    with op.batch_alter_table("features", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_features_layer_id"))

    op.drop_table("features")
    with op.batch_alter_table("layers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_layers_file_id"))

    op.drop_table("layers")
    with op.batch_alter_table("geo_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_geo_files_status"))
        batch_op.drop_index(batch_op.f("ix_geo_files_sha256"))

    op.drop_table("geo_files")
