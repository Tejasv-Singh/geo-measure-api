import json
from pathlib import Path

import pytest

from app.core.errors import InvalidFileError, UnsupportedFileError
from app.services.readers import KMLReader, ReadResult, ShapefileZipReader, reader_for

LIMIT = 50 * 1024 * 1024


def read(path: Path) -> ReadResult:
    return reader_for(path.name, max_uncompressed_bytes=LIMIT).read(path)


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
    ],
)
def test_bad_shapefile_archives_are_rejected(
    fixtures_dir: Path, fixture: str, message: str
) -> None:
    with pytest.raises(InvalidFileError, match=message):
        read(fixtures_dir / fixture)


def test_zip_over_uncompressed_cap_is_rejected(fixtures_dir: Path) -> None:
    reader = ShapefileZipReader(max_uncompressed_bytes=100)

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
