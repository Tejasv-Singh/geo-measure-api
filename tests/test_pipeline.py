import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.models import (
    Feature,
    FileStatus,
    GeoFile,
    Layer,
    Measurement,
    MeasurementKind,
    MeasurementStatus,
)
from app.services import pipeline
from app.services.pipeline import GENERIC_FAILURE, INTERRUPTED, run_file_job
from app.services.readers import ReadLimits
from app.storage import LocalStorage

LIMIT = ReadLimits(10**8)


def upload(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixture: Path,
    requested_crs: str | None = None,
) -> uuid.UUID:
    """Store a fixture and create its PENDING row, as the upload route will."""
    with fixture.open("rb") as source:
        stored = storage.save(source, fixture.suffix)
    with session_factory() as session:
        geo_file = GeoFile(
            filename=fixture.name,
            format="unknown",
            requested_crs=requested_crs,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_key=stored.key,
        )
        session.add(geo_file)
        session.commit()
        return geo_file.id


def process(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixture: Path,
    requested_crs: str | None = None,
) -> GeoFile:
    file_id = upload(session_factory, storage, fixture, requested_crs)
    run_file_job(file_id, session_factory, storage, LIMIT)
    with session_factory() as session:
        geo_file = session.get(GeoFile, file_id)
        assert geo_file is not None
        return geo_file


def layers_of(session_factory: sessionmaker[Session], file_id: uuid.UUID) -> list[Layer]:
    with session_factory() as session:
        query = select(Layer).where(Layer.file_id == file_id).order_by(Layer.id)
        return list(session.scalars(query))


def features_of(session_factory: sessionmaker[Session], file_id: uuid.UUID) -> list[Feature]:
    with session_factory() as session:
        query = select(Feature).where(Feature.file_id == file_id).order_by(Feature.id)
        return list(session.scalars(query))


def measurements_of(
    session_factory: sessionmaker[Session], file_id: uuid.UUID
) -> list[Measurement]:
    with session_factory() as session:
        query = (
            select(Measurement)
            .join(Feature)
            .where(Feature.file_id == file_id)
            .order_by(Measurement.id)
        )
        return list(session.scalars(query))


def test_golden_kml_is_stored_with_layer_features_and_measurement(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "golden_square.kml")

    assert geo_file.status is FileStatus.COMPLETED
    assert geo_file.error_message is None
    assert geo_file.format == "kml"
    assert geo_file.source_crs == "EPSG:4326"
    assert (geo_file.layer_count, geo_file.feature_count) == (1, 1)
    assert geo_file.processing_ms is not None and geo_file.processing_ms >= 0
    assert geo_file.processed_at is not None

    (layer,) = layers_of(session_factory, geo_file.id)
    assert (layer.name, layer.crs_label, layer.feature_count) == ("Golden", "EPSG:4326", 1)
    assert layer.crs_wkt.startswith("GEOGCRS[")

    (feature,) = features_of(session_factory, geo_file.id)
    assert feature.layer_id == layer.id
    assert (feature.feature_index, feature.geometry_type, feature.is_valid) == (0, "Polygon", True)
    assert feature.properties == {"Name": "1 km square", "description": None}
    assert feature.geometry is not None and feature.geometry["type"] == "Polygon"
    lon, lat = feature.geometry["coordinates"][0][0]
    assert 76 < lon < 78 and 27 < lat < 29

    (area,) = measurements_of(session_factory, geo_file.id)
    assert area.feature_id == feature.id
    assert (area.kind, area.status, area.unit) == (
        MeasurementKind.AREA,
        MeasurementStatus.OK,
        "m2",
    )
    assert area.value == pytest.approx(1_000_000, rel=1e-5)
    assert area.geodesic_value == pytest.approx(1_000_000, rel=1e-5)
    assert area.projected_crs is not None and area.projected_crs.startswith("+proj=laea")


def test_missing_prj_without_source_crs_fails_with_a_clear_message(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "shapefile_no_prj.zip")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_code == "missing_crs"
    assert geo_file.error_details == {"layer": "parcels"}
    assert geo_file.error_message is not None
    assert "no .prj file" in geo_file.error_message
    assert "source_crs" in geo_file.error_message
    assert geo_file.processing_ms is not None and geo_file.processed_at is not None
    assert layers_of(session_factory, geo_file.id) == []


def test_missing_dbf_keeps_the_missing_sidecars_in_details(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "shapefile_missing_dbf.zip")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_code == "invalid_file"
    assert geo_file.error_details == {"missing": {"parcels.shp": [".dbf"]}}


def test_running_the_job_twice_leaves_one_completed_result(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    file_id = upload(session_factory, storage, fixtures_dir / "golden_square.kml")

    run_file_job(file_id, session_factory, storage, LIMIT)
    run_file_job(file_id, session_factory, storage, LIMIT)

    with session_factory() as session:
        geo_file = session.get(GeoFile, file_id)
        assert geo_file is not None
        assert geo_file.status is FileStatus.COMPLETED
        assert geo_file.error_code is None
    assert len(layers_of(session_factory, file_id)) == 1
    assert len(features_of(session_factory, file_id)) == 1
    assert len(measurements_of(session_factory, file_id)) == 1


def test_job_does_not_touch_a_file_another_run_has_claimed(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    file_id = upload(session_factory, storage, fixtures_dir / "golden_square.kml")
    with session_factory() as session:
        geo_file = session.get(GeoFile, file_id)
        assert geo_file is not None
        geo_file.status = FileStatus.PROCESSING
        session.commit()

    run_file_job(file_id, session_factory, storage, LIMIT)

    with session_factory() as session:
        geo_file = session.get(GeoFile, file_id)
        assert geo_file is not None and geo_file.status is FileStatus.PROCESSING
    assert layers_of(session_factory, file_id) == []


def test_missing_prj_with_source_crs_is_measured(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(
        session_factory, storage, fixtures_dir / "shapefile_no_prj.zip", "EPSG:32643"
    )

    assert geo_file.status is FileStatus.COMPLETED
    assert geo_file.source_crs == "EPSG:32643"
    (layer,) = layers_of(session_factory, geo_file.id)
    assert layer.crs_label == "EPSG:32643"
    measurements = measurements_of(session_factory, geo_file.id)
    assert [m.kind for m in measurements] == [MeasurementKind.AREA, MeasurementKind.AREA]
    assert measurements[0].value == pytest.approx(10_000, rel=1e-3)


def test_invalid_source_crs_fails_the_file(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "shapefile_no_prj.zip", "nope")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_code == "invalid_crs"
    assert geo_file.error_message == "'nope' is not a valid CRS."


def test_collection_feature_gets_two_measurements(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "geometry_collection.kml")

    assert geo_file.status is FileStatus.COMPLETED
    (feature,) = features_of(session_factory, geo_file.id)
    assert feature.geometry_type == "GeometryCollection"
    assert feature.geometry is not None and feature.geometry["type"] == "GeometryCollection"
    measurements = measurements_of(session_factory, geo_file.id)
    assert [(m.feature_id, m.kind) for m in measurements] == [
        (feature.id, MeasurementKind.AREA),
        (feature.id, MeasurementKind.LENGTH),
    ]


def test_multi_layer_shapefile_links_features_to_their_layers(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "shapefile_two_layers.zip")

    layers = layers_of(session_factory, geo_file.id)
    features = features_of(session_factory, geo_file.id)
    assert [(layer.name, layer.feature_count) for layer in layers] == [("parcels", 2), ("roads", 1)]
    assert [f.layer_id for f in features] == [layers[0].id, layers[0].id, layers[1].id]
    assert [f.feature_index for f in features] == [0, 1, 2]
    assert features[1].properties["area_ha"] is None


def test_garbage_file_fails_and_keeps_no_rows(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    geo_file = process(session_factory, storage, fixtures_dir / "not_a_zip.zip")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_message == "The file is not a valid zip archive."
    assert features_of(session_factory, geo_file.id) == []


def test_unexpected_error_fails_with_a_generic_message(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(pipeline, "process_file", explode)

    geo_file = process(session_factory, storage, fixtures_dir / "golden_square.kml")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_code == "internal_error"
    assert geo_file.error_message == GENERIC_FAILURE
    assert geo_file.error_details is None
    assert geo_file.processing_ms is not None and geo_file.processed_at is not None
    assert "secret internal detail" in caplog.text


def test_error_while_saving_rolls_back_and_fails(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "insert_features", explode)

    geo_file = process(session_factory, storage, fixtures_dir / "golden_square.kml")

    assert geo_file.status is FileStatus.FAILED
    assert geo_file.error_message == GENERIC_FAILURE
    assert layers_of(session_factory, geo_file.id) == []


def test_deleted_file_is_skipped(
    session_factory: sessionmaker[Session], storage: LocalStorage
) -> None:
    run_file_job(uuid.uuid4(), session_factory, storage, LIMIT)


def test_startup_fails_files_left_unfinished(
    settings: Settings,
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixtures_dir: Path,
) -> None:
    pending = upload(session_factory, storage, fixtures_dir / "golden_square.kml")
    processing = upload(session_factory, storage, fixtures_dir / "golden_square.kml")
    done = process(session_factory, storage, fixtures_dir / "golden_square.kml").id
    with session_factory() as session:
        geo_file = session.get(GeoFile, processing)
        assert geo_file is not None
        geo_file.status = FileStatus.PROCESSING
        session.commit()

    with TestClient(create_app(settings)):
        pass

    with session_factory() as session:
        for file_id in (pending, processing):
            geo_file = session.get(GeoFile, file_id)
            assert geo_file is not None
            assert geo_file.status is FileStatus.FAILED
            assert geo_file.error_code == "interrupted"
            assert geo_file.error_message == INTERRUPTED
        completed = session.get(GeoFile, done)
        assert completed is not None and completed.status is FileStatus.COMPLETED


def test_startup_prepares_storage_and_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert settings.storage_dir.is_dir()
        assert client.app.state.session_factory is not None  # type: ignore[attr-defined]


def test_file_deleted_while_processing_is_not_logged_as_processed(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    file_id = upload(session_factory, storage, fixtures_dir / "golden_square.kml")
    original = pipeline.process_file

    def process_then_delete(*args: Any) -> pipeline.ProcessedFile:
        processed = original(*args)
        with session_factory() as session:
            geo_file = session.get(GeoFile, file_id)
            assert geo_file is not None
            session.delete(geo_file)
            session.commit()
        return processed

    monkeypatch.setattr(pipeline, "process_file", process_then_delete)

    with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
        run_file_job(file_id, session_factory, storage, LIMIT)

    assert "deleted while processing" in caplog.text
    assert "Processed file" not in caplog.text
    assert layers_of(session_factory, file_id) == []


def test_too_many_features_is_recorded_with_count_and_limit(
    session_factory: sessionmaker[Session], storage: LocalStorage, fixtures_dir: Path
) -> None:
    file_id = upload(session_factory, storage, fixtures_dir / "multi_folder.kml")

    run_file_job(file_id, session_factory, storage, ReadLimits(10**8, max_features=2))

    with session_factory() as session:
        geo_file = session.get(GeoFile, file_id)
        assert geo_file is not None
        assert geo_file.status is FileStatus.FAILED
        assert geo_file.error_code == "too_many_features"
        assert geo_file.error_details == {"feature_count": 3, "limit": 2}
