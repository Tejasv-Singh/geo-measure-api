import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.layer import Layer
    from app.models.measurement import Measurement


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("file_id", "feature_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Duplicates layer.file_id so per-file queries need no join. The unique constraint indexes it.
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("geo_files.id", ondelete="CASCADE"))
    layer_id: Mapped[int] = mapped_column(ForeignKey("layers.id", ondelete="CASCADE"), index=True)
    feature_index: Mapped[int]
    geometry_type: Mapped[str | None] = mapped_column(String(30))
    # GeoJSON geometry in EPSG:4326.
    geometry: Mapped[dict[str, Any] | None]
    properties: Mapped[dict[str, Any]]
    is_valid: Mapped[bool | None]

    layer: Mapped["Layer"] = relationship(back_populates="features")
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="feature",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Measurement.id",
    )
