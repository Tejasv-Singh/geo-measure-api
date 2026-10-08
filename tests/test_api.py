import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.api.routes import files as files_route
from app.core.config import Settings
from app.main import create_app
from app.models import Feature, Layer, Measurement

STATUSES = ["OK", "NOT_APPLICABLE", "UNSUPPORTED", "INVALID_GEOMETRY", "EMPTY"]


def upload(
    client: TestClient,
    path: Path,
    filename: str | None = None,
    source_crs: str | None = None,
) -> httpx.Response:
    data = {"source_crs": source_crs} if source_crs is not None else None
    with path.open("rb") as handle:
        response: httpx.Response = client.post(
            "/api/files/", files={"file": (filename or path.name, handle)}, data=data
        )
    return response


def upload_ok(client: TestClient, path: Path, source_crs: str | None = None) -> str:
    response = upload(client, path, source_crs=source_crs)
    assert response.status_code == 202, response.text
    file_id: str = response.json()["id"]
    return file_id


def error(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()["error"]
    return body


def session_factory_of(client: TestClient) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory


def stored_files(settings: Settings) -> list[Path]:
    return list(settings.storage_dir.iterdir())


@pytest.fixture
def paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave uploads PENDING by not running the background job."""
    monkeypatch.setattr(files_route, "run_file_job", lambda *_: None)


def test_golden_kml_full_flow(client: TestClient, fixtures_dir: Path) -> None:
    response = upload(client, fixtures_dir / "golden_square.kml")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    assert response.headers["location"] == f"/api/files/{body['id']}/"

    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    info = detail.json()
    assert info["id"] == body["id"]
    assert info["filename"] == "golden_square.kml"
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 1
    assert info["crs"] == "EPSG:4326"
    assert info["layers"] == [{"name": "Golden", "crs": "EPSG:4326", "feature_count": 1}]
    assert info["error_code"] is None and info["error_message"] is None
    assert info["error_details"] is None
    assert info["processing_ms"] >= 0
    assert info["created_at"].endswith("Z") and info["processed_at"].endswith("Z")

    measurements = client.get(f"/api/files/{body['id']}/measurements/")
    assert measurements.status_code == 200
    page = measurements.json()
    (row,) = page["items"]
    assert row["feature_index"] == 0
    assert row["layer"] == "Golden"
    assert (row["geometry_type"], row["kind"], row["status"]) == ("Polygon", "AREA", "OK")
    assert row["unit"] == "m2" and row["method"] == "laea"
    assert row["value"] == pytest.approx(1_000_000, rel=1e-5)
    assert row["geodesic_value"] == pytest.approx(1_000_000, rel=1e-5)
    assert page["total"] == 1 and page["limit"] == 100 and page["offset"] == 0
    assert page["summary"]["total_area_m2"] == pytest.approx(1_000_000, rel=1e-5)
    assert page["summary"]["total_length_m"] == 0
    assert page["summary"]["count_by_status"] == {s: int(s == "OK") for s in STATUSES}

    features = client.get(f"/api/files/{body['id']}/features/")
    assert features.status_code == 200
    assert features.headers["content-type"] == "application/geo+json"
    collection = features.json()
    assert collection["type"] == "FeatureCollection"
    assert collection["total"] == 1
    (feature,) = collection["features"]
    assert feature["type"] == "Feature"
    assert (feature["id"], feature["layer"]) == (0, "Golden")
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"] == {"Name": "1 km square", "description": None}


def test_measurement_filters_pagination_and_summary(client: TestClient, fixtures_dir: Path) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_two_layers.zip")
    url = f"/api/files/{file_id}/measurements/"

    everything = client.get(url).json()
    assert [r["feature_index"] for r in everything["items"]] == [0, 1, 2]
    assert [r["layer"] for r in everything["items"]] == ["parcels", "parcels", "roads"]
    total_area = everything["summary"]["total_area_m2"]
    assert total_area == pytest.approx(10_000 + 2_500, rel=1e-3)
    assert everything["summary"]["total_length_m"] == pytest.approx(1_000, rel=1e-3)

    second = client.get(url, params={"limit": 1, "offset": 1}).json()
    assert [r["feature_index"] for r in second["items"]] == [1]
    assert (second["total"], second["limit"], second["offset"]) == (3, 1, 1)
    assert second["summary"] == everything["summary"]

    lines = client.get(url, params={"geometry_type": "LineString"}).json()
    assert [r["kind"] for r in lines["items"]] == ["LENGTH"]
    assert lines["total"] == 1
    assert lines["summary"]["total_area_m2"] == 0
    assert lines["summary"]["count_by_status"]["OK"] == 1

    areas = client.get(url, params={"kind": "AREA", "status": "OK"}).json()
    assert areas["total"] == 2
    assert areas["summary"]["total_area_m2"] == pytest.approx(total_area)

    none = client.get(url, params={"status": "EMPTY"}).json()
    assert none["items"] == [] and none["total"] == 0
    assert none["summary"]["total_area_m2"] == 0


def test_summary_includes_repaired_geometry(client: TestClient, fixtures_dir: Path) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_self_intersecting.zip")

    page = client.get(f"/api/files/{file_id}/measurements/").json()

    assert page["items"][0]["status"] == "INVALID_GEOMETRY"
    assert page["summary"]["total_area_m2"] == pytest.approx(5_000, rel=1e-3)
    assert page["summary"]["count_by_status"]["INVALID_GEOMETRY"] == 1


def test_collection_rows_are_ordered_within_a_feature(
    client: TestClient, fixtures_dir: Path
) -> None:
    file_id = upload_ok(client, fixtures_dir / "geometry_collection.kml")

    items = client.get(f"/api/files/{file_id}/measurements/").json()["items"]

    assert [(r["feature_index"], r["kind"]) for r in items] == [(0, "AREA"), (0, "LENGTH")]


@pytest.mark.parametrize(
    "params",
    [
        {"geometry_type": "Circle"},
        {"status": "BROKEN"},
        {"kind": "VOLUME"},
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
    ],
)
def test_bad_measurement_query_parameters(
    client: TestClient, fixtures_dir: Path, params: dict[str, Any]
) -> None:
    file_id = upload_ok(client, fixtures_dir / "golden_square.kml")

    response = client.get(f"/api/files/{file_id}/measurements/", params=params)

    assert response.status_code == 422
    assert error(response)["code"] == "validation_error"


def test_measurements_never_select_the_geometry_column(
    client: TestClient, fixtures_dir: Path
) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_two_layers.zip")
    engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert client.get(f"/api/files/{file_id}/measurements/").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    selects = [s for s in statements if "measurements" in s]
    assert selects
    assert not any(re.search(r"features\.geometry(?!_type)", s) for s in selects)


def test_feature_pagination(client: TestClient, fixtures_dir: Path) -> None:
    file_id = upload_ok(client, fixtures_dir / "multi_folder.kml")

    page = client.get(f"/api/files/{file_id}/features/", params={"limit": 2, "offset": 1})

    collection = page.json()
    assert collection["total"] == 3
    assert [(f["id"], f["layer"]) for f in collection["features"]] == [(1, "Roads"), (2, "Roads")]


def test_missing_prj_fails_and_results_return_409(client: TestClient, fixtures_dir: Path) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_no_prj.zip")

    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert info["error_code"] == "missing_crs"
    assert info["error_details"] == {"layer": "parcels"}
    assert info["layers"] == []

    for endpoint in ("measurements", "features"):
        response = client.get(f"/api/files/{file_id}/{endpoint}/")
        assert response.status_code == 409
        assert error(response)["code"] == "file_failed"
        assert error(response)["details"]["error_code"] == "missing_crs"


def test_source_crs_form_field_fills_in_a_missing_prj(
    client: TestClient, fixtures_dir: Path
) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_no_prj.zip", source_crs="EPSG:32643")

    info = client.get(f"/api/files/{file_id}/").json()

    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:32643"


@pytest.mark.usefixtures("paused")
def test_results_return_409_while_pending(client: TestClient, fixtures_dir: Path) -> None:
    file_id = upload_ok(client, fixtures_dir / "golden_square.kml")

    assert client.get(f"/api/files/{file_id}/").json()["status"] == "PENDING"
    for endpoint in ("measurements", "features"):
        response = client.get(f"/api/files/{file_id}/{endpoint}/")
        assert response.status_code == 409
        assert error(response)["code"] == "not_ready"
        assert error(response)["details"] == {"status": "PENDING"}


def test_invalid_source_crs_is_rejected_before_storing(
    client: TestClient, settings: Settings, fixtures_dir: Path
) -> None:
    response = upload(client, fixtures_dir / "shapefile_no_prj.zip", source_crs="EPSG:nope")

    assert response.status_code == 422
    assert error(response)["code"] == "invalid_crs"
    assert stored_files(settings) == []
    assert client.get("/api/files/").json()["total"] == 0


def test_unsupported_extension_is_415(client: TestClient, fixtures_dir: Path) -> None:
    response = upload(client, fixtures_dir / "golden_square.kml", filename="square.geojson")

    assert response.status_code == 415
    assert error(response)["code"] == "unsupported_file"
    assert error(response)["details"] == {"supported": [".kml", ".kmz", ".zip"]}


@pytest.mark.parametrize(
    ("fixture", "filename"),
    [
        ("not_a_zip.zip", "data.zip"),
        ("not_xml.kml", "data.kml"),
        ("golden_square.kml", "data.kmz"),
        ("sample.kmz", "data.kml"),
        ("empty.zip", "data.zip"),
    ],
)
def test_content_must_match_the_extension(
    client: TestClient, settings: Settings, fixtures_dir: Path, fixture: str, filename: str
) -> None:
    response = upload(client, fixtures_dir / fixture, filename=filename)

    assert response.status_code == 422
    assert error(response)["code"] == "invalid_file"
    assert "does not match" in error(response)["message"]
    assert stored_files(settings) == []


@pytest.mark.usefixtures("paused")
def test_kml_with_bom_and_leading_whitespace_is_accepted(
    client: TestClient, tmp_path: Path, fixtures_dir: Path
) -> None:
    path = tmp_path / "bom.kml"
    path.write_bytes(b"\xef\xbb\xbf\n  " + (fixtures_dir / "golden_square.kml").read_bytes())

    assert upload(client, path).status_code == 202


@pytest.mark.usefixtures("paused")
def test_client_side_path_is_reduced_to_the_file_name(
    client: TestClient, fixtures_dir: Path
) -> None:
    response = upload(client, fixtures_dir / "golden_square.kml", filename="C:\\maps\\plot.kml")

    info = client.get(response.headers["location"]).json()
    assert info["filename"] == "plot.kml"


def test_upload_without_a_file_is_a_validation_error(client: TestClient) -> None:
    response = client.post("/api/files/", data={"source_crs": "EPSG:4326"})

    assert response.status_code == 422
    assert error(response)["code"] == "validation_error"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/files/{id}/"),
        ("DELETE", "/api/files/{id}/"),
        ("GET", "/api/files/{id}/measurements/"),
        ("GET", "/api/files/{id}/features/"),
    ],
)
def test_unknown_file_is_404(client: TestClient, method: str, path: str) -> None:
    file_id = uuid.uuid4()

    response = client.request(method, path.format(id=file_id))

    assert response.status_code == 404
    assert error(response) == {
        "code": "not_found",
        "message": "File not found.",
        "details": {"id": str(file_id)},
    }


def test_malformed_file_id_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/api/files/not-a-uuid/")

    assert response.status_code == 422
    assert error(response)["code"] == "validation_error"


@pytest.mark.usefixtures("paused")
def test_list_is_newest_first_with_status_filter_and_pagination(
    client: TestClient, fixtures_dir: Path
) -> None:
    ids = [upload_ok(client, fixtures_dir / "golden_square.kml") for _ in range(3)]

    listing = client.get("/api/files/").json()
    assert [item["id"] for item in listing["items"]] == ids[::-1]
    assert listing["total"] == 3
    assert listing["items"][0]["status"] == "PENDING"
    assert listing["items"][0]["crs"] is None

    page = client.get("/api/files/", params={"limit": 1, "offset": 1}).json()
    assert [item["id"] for item in page["items"]] == [ids[1]]
    assert page["total"] == 3

    completed = client.get("/api/files/", params={"status": "COMPLETED"}).json()
    assert completed == {"items": [], "total": 0, "limit": 100, "offset": 0}

    assert client.get("/api/files/", params={"status": "DONE"}).status_code == 422


def test_delete_removes_rows_and_the_stored_file(
    client: TestClient, settings: Settings, fixtures_dir: Path
) -> None:
    file_id = upload_ok(client, fixtures_dir / "shapefile_two_layers.zip")
    assert len(stored_files(settings)) == 1

    response = client.delete(f"/api/files/{file_id}/")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/files/{file_id}/").status_code == 404
    assert stored_files(settings) == []
    with session_factory_of(client)() as session:
        for model in (Layer, Feature, Measurement):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_openapi_documents_every_route_with_examples(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    paths = spec["paths"]
    assert set(paths) >= {
        "/api/files/",
        "/api/files/{file_id}/",
        "/api/files/{file_id}/measurements/",
        "/api/files/{file_id}/features/",
    }
    features = paths["/api/files/{file_id}/features/"]["get"]["responses"]["200"]
    assert "application/geo+json" in features["content"]
    for name in ("FileDetail", "MeasurementPage", "FeatureCollection", "UploadAccepted"):
        assert spec["components"]["schemas"][name]["examples"]
    # Errors use the shared shape everywhere, as JSON even on the GeoJSON route.
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    feature_errors = paths["/api/files/{file_id}/features/"]["get"]["responses"]
    for code in ("404", "409", "422"):
        assert list(feature_errors[code]["content"]) == ["application/json"]


@pytest.fixture
def small_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"max_upload_bytes": 1000})


@pytest.fixture
def small_client(small_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(small_settings)) as test_client:
        yield test_client


BOUNDARY = "testboundary"


def multipart(filename: str, data: bytes) -> bytes:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    return head.encode() + data + f"\r\n--{BOUNDARY}--\r\n".encode()


def test_declared_content_length_over_the_limit_is_413(
    small_client: TestClient, small_settings: Settings
) -> None:
    body = multipart("big.kml", b"<kml>" + bytes(MULTIPART_OVERHEAD_BYTES + 2000))

    response = small_client.post(
        "/api/files/",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )

    assert response.status_code == 413
    assert response.headers["connection"] == "close"
    assert error(response) == {
        "code": "file_too_large",
        "message": "The file is larger than the upload limit.",
        "details": {"limit_bytes": 1000},
    }
    assert stored_files(small_settings) == []


def test_body_without_content_length_is_counted(
    small_client: TestClient, small_settings: Settings
) -> None:
    body = multipart("big.kml", b"<kml>" + bytes(MULTIPART_OVERHEAD_BYTES + 2000))

    def chunks() -> Iterator[bytes]:
        for start in range(0, len(body), 4096):
            yield body[start : start + 4096]

    request = small_client.build_request(
        "POST",
        "/api/files/",
        content=chunks(),
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    assert "content-length" not in request.headers

    response = small_client.send(request)

    assert response.status_code == 413
    assert response.headers["connection"] == "close"
    assert error(response)["code"] == "file_too_large"
    assert stored_files(small_settings) == []


def test_file_just_over_the_limit_is_stopped_by_storage(
    small_client: TestClient, small_settings: Settings
) -> None:
    response = small_client.post("/api/files/", files={"file": ("big.kml", b"<kml>" + bytes(2000))})

    assert response.status_code == 413
    assert "connection" not in response.headers
    assert error(response)["details"] == {"limit_bytes": 1000}
    assert stored_files(small_settings) == []
    assert small_client.get("/api/files/").json()["total"] == 0


def test_other_routes_are_not_size_limited(small_client: TestClient) -> None:
    response = small_client.post("/health", content=bytes(MULTIPART_OVERHEAD_BYTES + 5000))

    assert response.status_code == 405
