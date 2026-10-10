"""File processing: read, resolve CRS, measure, persist.

process_file is pure (a path in, plain data out), so it could move to a Celery or arq worker.
run_file_job is the persistence wrapper that BackgroundTasks runs today. It needs only the file
id and opens its own session; it never reuses the request's session.
"""

import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely
from fastapi.encoders import jsonable_encoder
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from sqlalchemy import insert, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.core.logging import request_context
from app.models import Feature, FileStatus, GeoFile, Layer, Measurement
from app.models.base import utcnow
from app.services.crs import crs_label, crs_wkt, parse_crs, resolve_layer_crs, summarize_crs
from app.services.measurement import MeasuredFeature, measure_layer
from app.services.readers import ReadLimits, reader_for
from app.storage import Storage

logger = logging.getLogger(__name__)

GENERIC_FAILURE = "Processing failed because of an internal error."
INTERRUPTED = "Processing was interrupted by a restart."
UNFINISHED = (FileStatus.PENDING, FileStatus.PROCESSING)


@dataclass(frozen=True, slots=True)
class ProcessedLayer:
    name: str
    crs: CRS
    features: list[MeasuredFeature]


@dataclass(frozen=True, slots=True)
class ProcessedFile:
    format: str
    layers: list[ProcessedLayer]
    source_crs: str | None

    @property
    def feature_count(self) -> int:
        return sum(len(layer.features) for layer in self.layers)


def process_file(
    path: Path, filename: str, limits: ReadLimits, requested_crs: str | None
) -> ProcessedFile:
    """Raises AppError for anything wrong with the file itself."""
    fallback = parse_crs(requested_crs) if requested_crs else None
    result = reader_for(filename, limits).read(path)
    layers = []
    for layer in result.layers:
        crs = resolve_layer_crs(layer.name, layer.crs, fallback)
        layers.append(ProcessedLayer(layer.name, crs, measure_layer(layer, crs)))
    return ProcessedFile(
        format=result.format,
        layers=layers,
        source_crs=summarize_crs(layer.crs for layer in layers),
    )


def run_file_job(
    file_id: uuid.UUID,
    session_factory: sessionmaker[Session],
    storage: Storage,
    limits: ReadLimits,
    request_id: str | None = None,
) -> None:
    """Process one stored file. It always ends COMPLETED or FAILED, never PROCESSING.

    Safe to run more than once for the same file, as an at-least-once job queue would: only the
    run that moves the file out of PENDING does any work. request_id is the upload's, so the
    upload and its processing share one ID in the logs.
    """
    with request_context(request_id):
        _run_file_job(file_id, session_factory, storage, limits)


def _run_file_job(
    file_id: uuid.UUID,
    session_factory: sessionmaker[Session],
    storage: Storage,
    limits: ReadLimits,
) -> None:
    started = time.perf_counter()
    try:
        with session_factory() as session:
            if not claim(session, file_id):
                logger.info("File %s is not PENDING, so this run does nothing", file_id)
                return
            geo_file = session.get(GeoFile, file_id)
            assert geo_file is not None
            path = storage.local_path(geo_file.storage_key)
            filename, requested_crs = geo_file.filename, geo_file.requested_crs

        try:
            processed = process_file(path, filename, limits, requested_crs)
        except AppError as exc:
            logger.info("File %s could not be processed: %s", file_id, exc.message)
            mark_failed(session_factory, file_id, exc.code, exc.message, exc.details, started)
            return

        with session_factory() as session:
            saved = save_result(session, file_id, processed, elapsed_ms(started))
            session.commit()
        if saved:
            logger.info("Processed file %s: %d features", file_id, processed.feature_count)
        else:
            logger.info("File %s was deleted while processing; results discarded", file_id)
    except Exception:
        logger.exception("Unexpected error while processing file %s", file_id)
        mark_failed(session_factory, file_id, "internal_error", GENERIC_FAILURE, None, started)


def claim(session: Session, file_id: uuid.UUID) -> bool:
    """Atomically move a PENDING file to PROCESSING. False if it was missing or not PENDING."""
    result = session.execute(
        update(GeoFile)
        .where(GeoFile.id == file_id, GeoFile.status == FileStatus.PENDING)
        .values(status=FileStatus.PROCESSING)
    )
    session.commit()
    return rowcount(result) == 1


def save_result(
    session: Session, file_id: uuid.UUID, processed: ProcessedFile, processing_ms: int
) -> bool:
    """Write the results. False, writing nothing, if the file was deleted meanwhile."""
    geo_file = session.get(GeoFile, file_id)
    if geo_file is None:
        return False

    for processed_layer in processed.layers:
        layer = Layer(
            file_id=file_id,
            name=processed_layer.name,
            crs_label=crs_label(processed_layer.crs),
            crs_wkt=crs_wkt(processed_layer.crs),
            feature_count=len(processed_layer.features),
        )
        session.add(layer)
        session.flush()
        insert_features(session, file_id, layer.id, processed_layer.features)

    geo_file.format = processed.format
    geo_file.source_crs = processed.source_crs
    geo_file.layer_count = len(processed.layers)
    geo_file.feature_count = processed.feature_count
    geo_file.status = FileStatus.COMPLETED
    geo_file.error_code = None
    geo_file.error_message = None
    geo_file.error_details = None
    geo_file.processing_ms = processing_ms
    geo_file.processed_at = utcnow()
    return True


def insert_features(
    session: Session, file_id: uuid.UUID, layer_id: int, features: list[MeasuredFeature]
) -> None:
    if not features:
        return
    feature_rows = [
        {
            "file_id": file_id,
            "layer_id": layer_id,
            "feature_index": measured.feature.feature_index,
            "geometry_type": measured.feature.geometry_type,
            "geometry": to_geojson(measured.geometry_wgs84),
            "properties": measured.feature.properties,
            "is_valid": measured.feature.is_valid,
        }
        for measured in features
    ]
    feature_ids = session.scalars(
        insert(Feature).returning(Feature.id, sort_by_parameter_order=True), feature_rows
    ).all()
    measurement_rows = [
        {"feature_id": feature_id, **dataclasses.asdict(measurement)}
        for feature_id, measured in zip(feature_ids, features, strict=True)
        for measurement in measured.measurements
    ]
    if measurement_rows:
        session.execute(insert(Measurement), measurement_rows)


def mark_failed(
    session_factory: sessionmaker[Session],
    file_id: uuid.UUID,
    code: str,
    message: str,
    details: Any,
    started: float,
) -> None:
    try:
        with session_factory() as session:
            # Only the run that claimed the file may fail it.
            session.execute(
                update(GeoFile)
                .where(GeoFile.id == file_id, GeoFile.status == FileStatus.PROCESSING)
                .values(
                    status=FileStatus.FAILED,
                    error_code=code,
                    error_message=message,
                    error_details=jsonable_encoder(details),
                    processing_ms=elapsed_ms(started),
                    processed_at=utcnow(),
                )
            )
            session.commit()
    except Exception:
        # Startup recovery marks it FAILED if this write is lost too.
        logger.exception("Could not mark file %s as failed", file_id)


def fail_interrupted(session: Session) -> int:
    """Mark files a previous process left unfinished. BackgroundTasks don't survive a restart."""
    result = session.execute(
        update(GeoFile)
        .where(GeoFile.status.in_(UNFINISHED))
        .values(
            status=FileStatus.FAILED,
            error_code="interrupted",
            error_message=INTERRUPTED,
            processed_at=utcnow(),
        )
    )
    session.commit()
    return rowcount(result)


def rowcount(result: object) -> int:
    # Session.execute is typed as returning Result, but UPDATE gives a CursorResult.
    return int(getattr(result, "rowcount", 0) or 0)


def to_geojson(geometry: BaseGeometry | None) -> dict[str, Any] | None:
    if geometry is None:
        return None
    parsed: dict[str, Any] = json.loads(shapely.to_geojson(geometry))
    return parsed


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
