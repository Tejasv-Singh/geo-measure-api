import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from app.services.crs import WGS84
from app.services.projection import LocalEqualAreaStrategy, UTMStrategy, utm_epsg


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


def test_local_equal_area_is_centred_on_the_feature() -> None:
    projection = LocalEqualAreaStrategy().projection_for(box(77.19, 28.59, 77.21, 28.61))

    assert projection.method == "laea"
    assert "+lat_0=28.600000 +lon_0=77.200000" in projection.label
