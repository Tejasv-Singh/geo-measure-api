"""The committed samples/ files: current with their generator, and processing as documented."""

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.make_samples import make_samples
from tests.test_api import upload_ok

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def contents(path: Path) -> dict[str, bytes]:
    """A file's bytes, or for a zip the bytes of each member.

    Compressed zip bytes depend on the zlib build, which differs between platforms.
    """
    if path.suffix != ".zip":
        return {path.name: path.read_bytes()}
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_committed_samples_match_the_generator(tmp_path: Path) -> None:
    for generated in make_samples(tmp_path):
        assert contents(SAMPLES / generated.name) == contents(generated), generated.name


def test_survey_sample(client: TestClient) -> None:
    file_id = upload_ok(client, SAMPLES / "survey_bengaluru.kml")

    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "COMPLETED"
    assert [(layer["name"], layer["feature_count"]) for layer in info["layers"]] == [
        ("Plots", 2),
        ("Infrastructure", 3),
    ]
    page = client.get(f"/api/files/{file_id}/measurements/").json()
    assert [(r["feature_index"], r["geometry_type"], r["kind"]) for r in page["items"]] == [
        (0, "Polygon", "AREA"),
        (1, "Polygon", "AREA"),
        (2, "LineString", "LENGTH"),
        (3, "Point", "NONE"),
        (4, "GeometryCollection", "AREA"),
        (4, "GeometryCollection", "LENGTH"),
    ]
    assert page["summary"]["count_by_status"]["OK"] == 5
    assert page["summary"]["count_by_status"]["NOT_APPLICABLE"] == 1


def test_utm_parcels_sample(client: TestClient) -> None:
    file_id = upload_ok(client, SAMPLES / "parcels_utm43n.zip")

    info = client.get(f"/api/files/{file_id}/").json()
    page = client.get(f"/api/files/{file_id}/measurements/").json()

    assert (info["status"], info["crs"], info["feature_count"]) == ("COMPLETED", "EPSG:32643", 3)
    areas = [r["value"] for r in page["items"]]
    # 90 x 70, 110 x 70 and 60 x 120 m in UTM grid units. About 280 km east of the central
    # meridian the UTM scale factor is about 1.0006, so the ground areas are 0.1% smaller.
    assert areas == pytest.approx([6_300, 7_700, 7_200], rel=2e-3)


def test_no_prj_sample_needs_source_crs(client: TestClient) -> None:
    failed = upload_ok(client, SAMPLES / "parcels_no_prj.zip")
    fixed = upload_ok(client, SAMPLES / "parcels_no_prj.zip", source_crs="EPSG:32643")

    assert client.get(f"/api/files/{failed}/").json()["error_code"] == "missing_crs"
    assert client.get(f"/api/files/{fixed}/").json()["status"] == "COMPLETED"


def test_golden_square_sample(client: TestClient) -> None:
    file_id = upload_ok(client, SAMPLES / "golden_square.kml")

    (row,) = client.get(f"/api/files/{file_id}/measurements/").json()["items"]

    assert row["value"] == pytest.approx(1_000_000, rel=1e-5)
