from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import MeasurementKind, MeasurementStatus

if TYPE_CHECKING:
    from app.models.feature import Feature


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    feature_id: Mapped[int] = mapped_column(
        ForeignKey("features.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MeasurementKind] = mapped_column(
        Enum(MeasurementKind, native_enum=False, length=20)
    )
    value: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(String(10))
    projected_crs: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[MeasurementStatus] = mapped_column(
        Enum(MeasurementStatus, native_enum=False, length=20), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    geodesic_value: Mapped[float | None]
    deviation_pct: Mapped[float | None]

    feature: Mapped["Feature"] = relationship(back_populates="measurements")
