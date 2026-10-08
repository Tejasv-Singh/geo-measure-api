from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.geometry.base import BaseGeometry

from app.services.crs import WGS84
from app.services.measurement import (
    Measurement,
    MeasurementKind,
    MeasurementStatus,
    measure_layer,
)
from app.services.readers import FeatureRecord, LayerRecord, reader_for

AREA = MeasurementKind.AREA
LENGTH = MeasurementKind.LENGTH
NONE = MeasurementKind.NONE
UTM_43N = CRS.from_epsg(32643)


def layer_of(geometries: list[BaseGeometry | None], crs: CRS = WGS84) -> LayerRecord:
    features = [
        FeatureRecord(
            layer="test",
            feature_index=index,
            geometry=geometry,
            geometry_type=None if geometry is None else geometry.geom_type,
            properties={},
            is_valid=None if geometry is None else bool(geometry.is_valid),
        )
        for index, geometry in enumerate(geometries)
    ]
    return LayerRecord(name="test", crs=crs, features=features)


def measure_one(geometry: BaseGeometry | None, crs: CRS = WGS84) -> list[Measurement]:
    (measured,) = measure_layer(layer_of([geometry], crs), crs)
    return measured.measurements


def read_layer(path: Path) -> LayerRecord:
    (layer,) = reader_for(path.name, max_uncompressed_bytes=10**8).read(path).layers
    return layer


def test_golden_square_from_kml_is_one_square_kilometre(fixtures_dir: Path) -> None:
    layer = read_layer(fixtures_dir / "golden_square.kml")
    assert layer.crs is not None

    (measured,) = measure_layer(layer, layer.crs)
    (area,) = measured.measurements

    assert area.kind is AREA and area.status is MeasurementStatus.OK
    assert area.unit == "m2"
    assert area.method == "laea"
    assert area.projected_crs is not None and area.projected_crs.startswith("+proj=laea")
    assert area.value == pytest.approx(1_000_000, rel=1e-5)
    assert area.geodesic_value == pytest.approx(1_000_000, rel=1e-5)


def test_projected_source_is_measured_after_normalizing_to_wgs84() -> None:
    (area,) = measure_one(box(680_000, 3_100_000, 681_000, 3_101_000), UTM_43N)

    assert area.value == pytest.approx(1_000_000, rel=1e-5)


LOCATIONS = [
    pytest.param(0.0, 0.0, id="equator"),
    pytest.param(77.0, 28.0, id="delhi"),
    pytest.param(77.9, 28.0, id="zone-edge"),
    pytest.param(10.0, 60.0, id="oslo"),
    pytest.param(170.0, -45.0, id="south"),
    pytest.param(-179.95, 10.0, id="antimeridian-zone"),
    pytest.param(0.0, 83.9, id="utm-north-limit"),
]


@pytest.mark.parametrize(("lon", "lat"), LOCATIONS)
def test_utm_length_is_within_a_quarter_percent_of_geodesic(lon: float, lat: float) -> None:
    (length,) = measure_one(LineString([(lon, lat), (lon + 0.05, lat + 0.05)]))

    assert length.kind is LENGTH and length.method == "utm"
    assert length.deviation_pct is not None
    assert abs(length.deviation_pct) < 0.25


@pytest.mark.parametrize(("lon", "lat"), LOCATIONS)
def test_equal_area_matches_geodesic_area(lon: float, lat: float) -> None:
    (area,) = measure_one(box(lon, lat, lon + 0.05, lat + 0.05))

    assert area.kind is AREA and area.method == "laea"
    assert area.deviation_pct is not None
    assert abs(area.deviation_pct) < 0.001


def test_polar_length_uses_ups_and_says_so() -> None:
    (length,) = measure_one(LineString([(0, 86), (1, 86.5)]))

    assert length.status is MeasurementStatus.OK
    assert length.projected_crs == "EPSG:32661"
    assert length.method == "ups"
    assert length.note is not None and "UPS" in length.note
    assert length.deviation_pct is not None and abs(length.deviation_pct) < 0.6


def test_holes_are_subtracted_in_projected_and_geodesic_area() -> None:
    outer = box(77.0, 28.0, 77.1, 28.1)
    inner = box(77.02, 28.02, 77.04, 28.04)
    with_hole = Polygon(outer.exterior.coords, [inner.exterior.coords])

    (solid,) = measure_one(outer)
    (holed,) = measure_one(with_hole)
    (hole,) = measure_one(inner)

    assert solid.value is not None and holed.value is not None and hole.value is not None
    assert holed.value == pytest.approx(solid.value - hole.value, rel=1e-6)
    assert holed.deviation_pct is not None and abs(holed.deviation_pct) < 0.001


@pytest.mark.parametrize(
    ("geometry", "kind"),
    [
        (MultiPolygon([box(77, 28, 77.01, 28.01), box(77.02, 28, 77.03, 28.01)]), AREA),
        (LineString([(77, 28), (77.01, 28)]), LENGTH),
        (MultiLineString([[(77, 28), (77.01, 28)], [(77, 28.01), (77.01, 28.01)]]), LENGTH),
        (LinearRing([(77, 28), (77.01, 28), (77.01, 28.01)]), LENGTH),
    ],
)
def test_measurable_types(geometry: BaseGeometry, kind: MeasurementKind) -> None:
    (measurement,) = measure_one(geometry)

    assert measurement.kind is kind
    assert measurement.status is MeasurementStatus.OK
    assert measurement.unit == ("m2" if kind is AREA else "m")
    assert measurement.value is not None and measurement.value > 0


@pytest.mark.parametrize(
    ("geometry", "status"),
    [
        (Point(77, 28), MeasurementStatus.NOT_APPLICABLE),
        (MultiPoint([(77, 28), (77.1, 28)]), MeasurementStatus.NOT_APPLICABLE),
        (GeometryCollection([Point(77, 28)]), MeasurementStatus.NOT_APPLICABLE),
        (None, MeasurementStatus.EMPTY),
        (Polygon(), MeasurementStatus.EMPTY),
        (GeometryCollection(), MeasurementStatus.EMPTY),
    ],
)
def test_features_without_a_measurement(
    geometry: BaseGeometry | None, status: MeasurementStatus
) -> None:
    (measurement,) = measure_one(geometry)

    assert measurement.kind is NONE
    assert measurement.status is status
    assert measurement.value is None


def test_geometry_collection_gets_area_and_length(fixtures_dir: Path) -> None:
    layer = read_layer(fixtures_dir / "geometry_collection.kml")

    (measured,) = measure_layer(layer, WGS84)
    area, length = measured.measurements

    assert (area.kind, length.kind) == (AREA, LENGTH)
    assert area.status is length.status is MeasurementStatus.OK
    assert area.note == "Summed 1 polygon part(s) of a GeometryCollection. Ignored 1 point part(s)."
    assert length.note == "Summed 1 line part(s) of a GeometryCollection. Ignored 1 point part(s)."


def test_self_intersecting_polygon_is_repaired_and_flagged(fixtures_dir: Path) -> None:
    layer = read_layer(fixtures_dir / "shapefile_self_intersecting.zip")
    assert layer.crs is not None

    (measured,) = measure_layer(layer, layer.crs)
    (area,) = measured.measurements

    assert area.status is MeasurementStatus.INVALID_GEOMETRY
    assert area.note is not None and "Self-intersection" in area.note
    # The 100 m bowtie splits into two triangles of 100 x 50 / 2 each.
    assert area.value == pytest.approx(5_000, rel=1e-3)


def test_polygon_with_nothing_left_after_repair() -> None:
    flat = Polygon([(77, 28), (77.1, 28), (77.2, 28), (77, 28)])

    (area,) = measure_one(flat)

    assert area.kind is AREA
    assert area.status is MeasurementStatus.INVALID_GEOMETRY
    assert area.value is None


def test_coordinates_outside_lon_lat_are_unsupported_not_a_crash() -> None:
    # UTM coordinates mislabelled as EPSG:4326.
    (area,) = measure_one(box(500_000, 3_100_000, 500_100, 3_100_100), WGS84)

    assert area.status is MeasurementStatus.UNSUPPORTED
    assert area.value is None
    assert area.note is not None and "source CRS" in area.note


def test_layer_results_keep_feature_order_and_wgs84_geometry() -> None:
    geometries: list[BaseGeometry | None] = [
        box(680_000, 3_100_000, 681_000, 3_101_000),
        None,
        LineString([(680_000, 3_100_000), (681_000, 3_100_000)]),
    ]

    measured = measure_layer(layer_of(geometries, UTM_43N), UTM_43N)

    assert [m.feature.feature_index for m in measured] == [0, 1, 2]
    assert [m.measurements[0].kind for m in measured] == [AREA, NONE, LENGTH]
    expected = gpd.GeoSeries([geometries[0]], crs=UTM_43N).to_crs(WGS84).iloc[0]
    assert measured[0].geometry_wgs84 is not None
    assert measured[0].geometry_wgs84.equals_exact(expected, tolerance=1e-9)
    assert measured[1].geometry_wgs84 is None
    assert measured[2].measurements[0].value == pytest.approx(1_000, rel=1e-3)
