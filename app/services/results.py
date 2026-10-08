"""Read-side queries for a processed file's measurements and features."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlalchemy.orm import Session

from app.core.errors import FileFailedError, FileNotReadyError
from app.models import (
    Feature,
    FileStatus,
    GeoFile,
    Layer,
    Measurement,
    MeasurementKind,
    MeasurementStatus,
)
from app.models.enums import GeometryType

# Rows whose value counts towards the summary totals.
SUMMED_STATUSES = (MeasurementStatus.OK, MeasurementStatus.INVALID_GEOMETRY)


@dataclass(frozen=True, slots=True)
class MeasurementFilters:
    geometry_type: GeometryType | None = None
    status: MeasurementStatus | None = None
    kind: MeasurementKind | None = None


@dataclass(frozen=True, slots=True)
class Summary:
    total_area_m2: float
    total_length_m: float
    count_by_status: dict[MeasurementStatus, int]


@dataclass(frozen=True, slots=True)
class MeasurementResults:
    rows: list[dict[str, Any]]
    total: int
    summary: Summary


@dataclass(frozen=True, slots=True)
class FeatureResults:
    rows: list[dict[str, Any]]
    total: int


def require_completed(geo_file: GeoFile) -> None:
    if geo_file.status in (FileStatus.PENDING, FileStatus.PROCESSING):
        raise FileNotReadyError(
            "The file is still being processed. Try again shortly.",
            {"status": geo_file.status},
        )
    if geo_file.status is FileStatus.FAILED:
        raise FileFailedError(
            "The file could not be processed.",
            {"error_code": geo_file.error_code, "error_message": geo_file.error_message},
        )


def measurement_results(
    session: Session, file_id: uuid.UUID, filters: MeasurementFilters, limit: int, offset: int
) -> MeasurementResults:
    conditions = measurement_conditions(file_id, filters)
    # Explicit columns, so the geometry column is never loaded.
    query = (
        select(
            Feature.feature_index,
            Layer.name.label("layer"),
            Feature.geometry_type,
            Measurement.kind,
            Measurement.value,
            Measurement.unit,
            Measurement.status,
            Measurement.projected_crs,
            Measurement.method,
            Measurement.note,
            Measurement.geodesic_value,
            Measurement.deviation_pct,
        )
        .select_from(Measurement)
        .join(Feature, Measurement.feature_id == Feature.id)
        .join(Layer, Feature.layer_id == Layer.id)
        .where(*conditions)
        .order_by(Feature.feature_index, Measurement.id)
        .limit(limit)
        .offset(offset)
    )
    rows = [dict(row._mapping) for row in session.execute(query)]
    return MeasurementResults(rows, count(session, conditions), summarize(session, conditions))


def measurement_conditions(
    file_id: uuid.UUID, filters: MeasurementFilters
) -> list[ColumnElement[bool]]:
    conditions = [Feature.file_id == file_id]
    if filters.geometry_type is not None:
        conditions.append(Feature.geometry_type == filters.geometry_type.value)
    if filters.status is not None:
        conditions.append(Measurement.status == filters.status)
    if filters.kind is not None:
        conditions.append(Measurement.kind == filters.kind)
    return conditions


def joined[*Ts](query: Select[*Ts]) -> Select[*Ts]:
    return query.select_from(Measurement).join(Feature, Measurement.feature_id == Feature.id)


def count(session: Session, conditions: list[ColumnElement[bool]]) -> int:
    return session.scalar(joined(select(func.count())).where(*conditions)) or 0


def summarize(session: Session, conditions: list[ColumnElement[bool]]) -> Summary:
    summed = Measurement.status.in_(SUMMED_STATUSES)
    area = func.sum(
        case((and_(summed, Measurement.kind == MeasurementKind.AREA), Measurement.value))
    )
    length = func.sum(
        case((and_(summed, Measurement.kind == MeasurementKind.LENGTH), Measurement.value))
    )
    total_area, total_length = session.execute(
        joined(select(func.coalesce(area, 0.0), func.coalesce(length, 0.0))).where(*conditions)
    ).one()

    counts = dict.fromkeys(MeasurementStatus, 0)
    by_status = joined(select(Measurement.status, func.count())).where(*conditions)
    for status, n in session.execute(by_status.group_by(Measurement.status)):
        counts[MeasurementStatus(status)] = n
    return Summary(float(total_area), float(total_length), counts)


def feature_results(
    session: Session, file_id: uuid.UUID, limit: int, offset: int
) -> FeatureResults:
    query = (
        select(
            Feature.feature_index,
            Layer.name.label("layer"),
            Feature.geometry,
            Feature.properties,
        )
        .join(Layer, Feature.layer_id == Layer.id)
        .where(Feature.file_id == file_id)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    )
    rows = [dict(row._mapping) for row in session.execute(query)]
    total = session.scalar(select(func.count()).where(Feature.file_id == file_id)) or 0
    return FeatureResults(rows, total)
