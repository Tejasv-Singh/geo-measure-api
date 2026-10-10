import json
import logging
import shutil
from pathlib import Path

import pytest

from app.core.errors import InvalidFileError, TooManyFeaturesError, UnsupportedFileError
from app.services.readers import (
    KMLReader,
    ReadLimits,
    ReadResult,
    ShapefileZipReader,
    reader_for,
)

LIMIT = ReadLimits(50 * 1024 * 1024)


def read(path: Path) -> ReadResult:
    return reader_for(path.name, LIMIT).read(path)


def stored_copy(fixture: Path, tmp_path: Path) -> Path:
    """Copy a fixture to an opaque name, the way uploads are stored."""
    stored = tmp_path / "storage" / f"0b9f3c2e{fixture.suffix}"
    stored.parent.mkdir()
    shutil.copyfile(fixture, stored)
    return stored


def test_registry_picks_reader_by_extension() -> None:
    assert isinstance(reader_for("a.ZIP", LIMIT), ShapefileZipReader)
    assert isinstance(reader_for("a.kml", LIMIT), KMLReader)
    assert isinstance(reader_for("a.kmz", LIMIT), KMLReader)


def test_registry_rejects_unknown_extension() -> None:
    with pytest.raises(UnsupportedFileError) as exc_info:
        reader_for("data.geojson", LIMIT)
    assert exc_info.value.details == {"supported": [".kml", ".kmz", ".zip"]}


def test_shapefile_zip_reads_every_layer_in_nested_folders(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "shapefile_two_layers.zip")

    assert result.format == "shapefile"
    assert [layer.name for layer in result.layers] == ["parcels", "roads"]
    assert all(layer.crs is not None and layer.crs.to_epsg() == 32643 for layer in result.layers)
    assert [f.feature_index for f in result.features] == [0, 1, 2]
    assert [f.geometry_type for f in result.features] == ["Polygon", "Polygon", "LineString"]


def test_shapefile_properties_are_json_safe(fixtures_dir: Path) -> None:
    first, second, _ = read(fixtures_dir / "shapefile_two_layers.zip").features

    assert first.properties == {
        "parcel_id": 1,
        "owner": "Asha",
        "area_ha": 1.5,
        "surveyed": "2024-03-01",
    }
    assert second.properties["area_ha"] is None
    assert second.properties["surveyed"] is None
    json.dumps([first.properties, second.properties], allow_nan=False)


def test_shapefile_without_prj_has_no_crs(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "shapefile_no_prj.zip")

    assert result.layers[0].crs is None


def test_shapefile_with_unparseable_prj_is_rejected(fixtures_dir: Path) -> None:
    with pytest.raises(InvalidFileError, match=r"The \.prj file could not be parsed\."):
        read(fixtures_dir / "shapefile_bad_prj.zip")


def test_shapefile_with_corrupt_dbf_keeps_features_without_properties(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "shapefile_corrupt_dbf.zip")

    assert [f.properties for f in result.features] == [{}, {}]
    assert [f.geometry_type for f in result.features] == ["Polygon", "Polygon"]


def test_shapefile_respects_cpg_encoding(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "shapefile_cp1251.zip")

    assert result.features[0].properties["city"] == "Москва"


def test_self_intersecting_polygon_is_read_and_flagged(fixtures_dir: Path) -> None:
    (feature,) = read(fixtures_dir / "shapefile_self_intersecting.zip").features

    assert feature.geometry_type == "Polygon"
    assert feature.is_valid is False


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("shapefile_missing_dbf.zip", "matching .shx and .dbf"),
        ("empty.zip", "archive is empty"),
        ("not_a_zip.zip", "not a valid zip"),
        ("shapefile_path_traversal.zip", "unsafe file paths"),
        ("shapefile_encrypted.zip", "Encrypted zip archives"),
        ("no_kml.kmz", "does not contain a .kml"),
    ],
)
def test_bad_shapefile_archives_are_rejected(
    fixtures_dir: Path, fixture: str, message: str
) -> None:
    with pytest.raises(InvalidFileError, match=message):
        read(fixtures_dir / fixture)


def test_zip_over_uncompressed_cap_is_rejected(fixtures_dir: Path) -> None:
    reader = ShapefileZipReader(filename="parcels.zip", limits=ReadLimits(100))

    with pytest.raises(InvalidFileError, match="too large"):
        reader.read(fixtures_dir / "shapefile_two_layers.zip")


def test_kml_keeps_name_description_and_extended_data(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "extended_data.kml")

    (feature,) = result.features
    assert result.layers[0].crs is not None and result.layers[0].crs.to_epsg() == 4326
    assert feature.properties == {
        "Name": "Plot 42",
        "description": "Northern plot",
        "owner": "Asha",
        "plot_no": "42",
    }


def test_kml_keeps_placemark_id_and_times_but_drops_empty_boilerplate(
    fixtures_dir: Path,
) -> None:
    plot, line = read(fixtures_dir / "ids_and_times.kml").features

    assert plot.properties["id"] == "plot-17"
    assert plot.properties["timestamp"] == "2024-03-01T10:30:00+00:00"
    assert line.properties["id"] is None
    assert line.properties["begin"] == "2023-01-01T00:00:00"
    assert line.properties["end"] == "2023-12-31T00:00:00"
    dropped = {"tessellate", "extrude", "visibility", "drawOrder", "icon", "altitudeMode"}
    assert dropped.isdisjoint(plot.properties)


def test_kml_reads_each_folder_as_a_layer(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "multi_folder.kml")

    assert [(layer.name, len(layer.features)) for layer in result.layers] == [
        ("Parcels", 1),
        ("Roads", 2),
    ]
    assert [f.feature_index for f in result.features] == [0, 1, 2]


def test_kml_multigeometry_becomes_geometry_collection(fixtures_dir: Path) -> None:
    (feature,) = read(fixtures_dir / "geometry_collection.kml").features

    assert feature.geometry_type == "GeometryCollection"


def test_kml_z_values_are_dropped(fixtures_dir: Path) -> None:
    (feature,) = read(fixtures_dir / "three_d.kml").features

    assert feature.geometry is not None
    assert feature.geometry.has_z is False


def test_kmz_is_read(fixtures_dir: Path) -> None:
    result = read(fixtures_dir / "sample.kmz")

    assert result.format == "kml"
    assert [f.geometry_type for f in result.features] == ["Polygon"]


@pytest.mark.parametrize(
    ("fixture", "upload_name", "visible_path"),
    [
        ("shapefile_corrupt_shp.zip", "My Parcels.zip", "My Parcels.zip/data/parcels.shp"),
        ("not_xml.kml", "survey.kml", "survey.kml"),
    ],
)
def test_gdal_errors_name_the_upload_not_the_stored_path(
    fixtures_dir: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fixture: str,
    upload_name: str,
    visible_path: str,
) -> None:
    stored = stored_copy(fixtures_dir / fixture, tmp_path)
    reader = reader_for(upload_name, LIMIT)

    with caplog.at_level(logging.WARNING), pytest.raises(InvalidFileError) as exc_info:
        reader.read(stored)

    message = exc_info.value.message
    assert visible_path in message
    assert stored.name not in message
    assert str(tmp_path) not in message and tmp_path.as_posix() not in message
    assert stored.name in caplog.text


def test_missing_layer_is_an_invalid_file(fixtures_dir: Path) -> None:
    path = fixtures_dir / "multi_folder.kml"
    reader = KMLReader(filename=path.name, limits=LIMIT)

    with pytest.raises(InvalidFileError, match="Layer 'Lakes' could not be opened"):
        reader.read_frame(path, path, layer="Lakes")


@pytest.mark.parametrize(
    ("fixture", "features"),
    [("shapefile_two_layers.zip", 3), ("multi_folder.kml", 3), ("sample.kmz", 1)],
)
def test_feature_cap_counts_every_layer(fixtures_dir: Path, fixture: str, features: int) -> None:
    path = fixtures_dir / fixture
    at_cap = reader_for(path.name, ReadLimits(LIMIT.max_uncompressed_bytes, features))
    over_cap = reader_for(path.name, ReadLimits(LIMIT.max_uncompressed_bytes, features - 1))

    assert len(at_cap.read(path).features) == features
    with pytest.raises(TooManyFeaturesError) as exc_info:
        over_cap.read(path)
    assert exc_info.value.code == "too_many_features"
    assert exc_info.value.details == {"feature_count": features, "limit": features - 1}


def test_feature_cap_is_checked_before_any_layer_is_read(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = fixtures_dir / "shapefile_two_layers.zip"
    reader = reader_for(path.name, ReadLimits(LIMIT.max_uncompressed_bytes, 2))

    def unexpected_read(*_: object, **__: object) -> None:
        raise AssertionError("read_frame ran before the feature cap was checked")

    monkeypatch.setattr(reader, "read_frame", unexpected_read)

    with pytest.raises(TooManyFeaturesError):
        reader.read(path)


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("shapefile_missing_dbf.zip", "matching .shx and .dbf"),
        ("shapefile_path_traversal.zip", "unsafe file paths"),
        ("shapefile_encrypted.zip", "Encrypted zip archives"),
        ("no_kml.kmz", "does not contain a .kml"),
    ],
)
def test_upload_check_rejects_bad_archives_from_a_stream(
    fixtures_dir: Path, fixture: str, message: str
) -> None:
    path = fixtures_dir / fixture
    with path.open("rb") as source:
        source.seek(7)
        with pytest.raises(InvalidFileError, match=message):
            reader_for(path.name, LIMIT).check_upload(source)
        assert source.tell() == 0


@pytest.mark.parametrize("fixture", ["shapefile_two_layers.zip", "sample.kmz", "multi_folder.kml"])
def test_upload_check_accepts_good_files_and_rewinds(fixtures_dir: Path, fixture: str) -> None:
    path = fixtures_dir / fixture
    with path.open("rb") as source:
        reader_for(path.name, LIMIT).check_upload(source)
        assert source.tell() == 0
