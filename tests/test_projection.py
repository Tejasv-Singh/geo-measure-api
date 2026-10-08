import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import LineString, box

from app.services.crs import WGS84
from app.services.projection import (
    LocalEqualAreaStrategy,
    UTMStrategy,
    laea_centre,
    laea_projection,
    utm_epsg,
)


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (77.2, 28.6, 32643),
        (-73.9, 40.7, 32618),
        (151.2, -33.9, 32756),
        (-180.0, 0.0, 32601),
        (180.0, 0.0, 32660),
        (10.0, 84.0, 32632),
        (10.0, 84.1, 32661),
        (10.0, -80.0, 32732),
        (10.0, -80.1, 32761),
    ],
)
def test_utm_epsg(lon: float, lat: float, epsg: int) -> None:
    assert utm_epsg(lon, lat) == epsg


def test_utm_projects_each_zone_group_like_a_direct_to_crs() -> None:
    delhi = LineString([(77.2, 28.6), (77.3, 28.7)])
    new_york = LineString([(-73.9, 40.7), (-73.8, 40.8)])

    projected = UTMStrategy().project([delhi, new_york, delhi])

    assert [p.projection.label for p in projected] == ["EPSG:32643", "EPSG:32618", "EPSG:32643"]
    expected = gpd.GeoSeries([delhi], crs=WGS84).to_crs(32643).iloc[0]
    assert projected[0].geometry.equals_exact(expected, tolerance=1e-6)
    assert projected[2].geometry.equals_exact(expected, tolerance=1e-6)


def test_utm_switches_to_ups_near_the_poles() -> None:
    (north,) = UTMStrategy().project([LineString([(0, 86), (1, 86)])])

    assert north.projection.label == "EPSG:32661"
    assert north.projection.method == "ups"
    assert north.projection.note is not None and "UPS" in north.projection.note


def test_local_equal_area_centre_snaps_to_the_middle_of_a_degree_cell() -> None:
    projection = LocalEqualAreaStrategy().projection_for(box(77.19, 28.59, 77.21, 28.61))

    assert projection.method == "laea"
    assert "+lat_0=28.5 +lon_0=77.5" in projection.label


@pytest.mark.parametrize(
    ("lon", "lat", "centre"),
    [
        (77.2, 28.6, (77.5, 28.5)),
        (-0.3, -0.3, (-0.5, -0.5)),
        (180.0, 90.0, (179.5, 89.5)),
        (-180.0, -90.0, (-179.5, -89.5)),
    ],
)
def test_laea_centre(lon: float, lat: float, centre: tuple[float, float]) -> None:
    assert laea_centre(lon, lat) == centre


@pytest.mark.parametrize(("lon", "lat"), [(77.0, 28.0), (10.0, 60.0), (170.0, -45.0)])
def test_snapped_centre_gives_the_same_area_as_a_per_feature_centre(lon: float, lat: float) -> None:
    # About 50 km across and off-centre in its cell, so the snapped centre is well away.
    square = box(lon + 0.02, lat + 0.02, lon + 0.47, lat + 0.47)
    point = square.representative_point()
    own = CRS.from_proj4(f"+proj=laea +lat_0={point.y} +lon_0={point.x} +datum=WGS84 +units=m")

    (snapped,) = LocalEqualAreaStrategy().project([square])
    exact = gpd.GeoSeries([square], crs=WGS84).to_crs(own).iloc[0]

    assert snapped.geometry.area == pytest.approx(exact.area, rel=1e-6)


def test_layer_over_three_by_three_degrees_builds_at_most_nine_crs() -> None:
    squares = [
        box(lon + offset, lat + offset, lon + offset + 0.01, lat + offset + 0.01)
        for lon in (77, 78, 79)
        for lat in (28, 29, 30)
        for offset in (0.1, 0.4, 0.7)
    ]
    laea_projection.cache_clear()

    projected = LocalEqualAreaStrategy().project(squares)

    assert len(projected) == 27
    assert laea_projection.cache_info().misses <= 9
    assert len({p.projection.label for p in projected}) == 9
