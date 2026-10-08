import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.feature import Feature
    from app.models.geo_file import GeoFile


class Layer(Base):
    __tablename__ = "layers"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("geo_files.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    crs_label: Mapped[str] = mapped_column(String(255))
    crs_wkt: Mapped[str] = mapped_column(Text)
    feature_count: Mapped[int]

    file: Mapped["GeoFile"] = relationship(back_populates="layers")
    features: Mapped[list["Feature"]] = relationship(
        back_populates="layer", cascade="all, delete-orphan", passive_deletes=True
    )
