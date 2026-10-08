import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, utcnow
from app.models.enums import FileStatus

if TYPE_CHECKING:
    from app.models.layer import Layer


class GeoFile(Base):
    __tablename__ = "geo_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(20))
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, native_enum=False, length=20), default=FileStatus.PENDING, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[Any] = mapped_column(JSONType, nullable=True)
    # The source_crs form field, used for layers that carry no CRS of their own.
    requested_crs: Mapped[str | None] = mapped_column(Text)
    source_crs: Mapped[str | None] = mapped_column(String(255))
    feature_count: Mapped[int] = mapped_column(default=0)
    layer_count: Mapped[int] = mapped_column(default=0)
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(255))
    processing_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    layers: Mapped[list["Layer"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Layer.id",
    )
