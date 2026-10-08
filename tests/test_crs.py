from pathlib import Path

import pytest
from pyproj import CRS

from app.core.errors import InvalidCRSError, MissingCRSError
from app.services.crs import (
    MIXED,
    crs_label,
    crs_wkt,
    parse_crs,
    resolve_layer_crs,
    summarize_crs,
)
from app.services.readers import reader_for

UTM_43N = CRS.from_epsg(32643)
ESRI_UTM_43N = (
    'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
)


@pytest.mark.parametrize("value", ["EPSG:32643", "epsg:32643", ESRI_UTM_43N])
def test_parse_crs_accepts_codes_and_wkt(value: str) -> None:
    assert parse_crs(value).to_epsg() == 32643


def test_parse_crs_rejects_garbage() -> None:
    with pytest.raises(InvalidCRSError, match="not a valid CRS"):
        parse_crs("EPSG:not-a-code")


def test_parse_crs_rejects_crs_without_a_path_to_lon_lat() -> None:
    with pytest.raises(InvalidCRSError, match="neither geographic nor projected"):
        parse_crs('LOCAL_CS["site grid",LOCAL_DATUM["site",0],UNIT["metre",1]]')


def test_layer_crs_wins_over_fallback() -> None:
    assert resolve_layer_crs("roads", UTM_43N, CRS.from_epsg(4326)) == UTM_43N


def test_fallback_is_used_when_layer_has_no_crs() -> None:
    assert resolve_layer_crs("roads", None, UTM_43N) == UTM_43N


def test_shapefile_without_prj_and_without_fallback_fails(fixtures_dir: Path) -> None:
    path = fixtures_dir / "shapefile_no_prj.zip"
    (layer,) = reader_for(path.name, max_uncompressed_bytes=10**8).read(path).layers

    with pytest.raises(MissingCRSError, match="source_crs") as exc_info:
        resolve_layer_crs(layer.name, layer.crs, None)
    assert exc_info.value.code == "missing_crs"
    assert exc_info.value.details == {"layer": "parcels"}


def test_crs_label_prefers_authority_code() -> None:
    assert crs_label(CRS.from_wkt(ESRI_UTM_43N)) == "EPSG:32643"


def test_esri_prj_without_authority_is_labelled_by_name(fixtures_dir: Path) -> None:
    path = fixtures_dir / "shapefile_kalianpur.zip"
    zone = reader_for(path.name, max_uncompressed_bytes=10**8).read(path).layers[1]
    assert zone.name == "zone" and zone.crs is not None

    assert zone.crs.to_authority() is None
    assert crs_label(zone.crs) == "Kalianpur_1975_India_Zone_IIIa"
    assert crs_wkt(zone.crs).startswith("PROJCRS[")
    assert len(crs_wkt(zone.crs)) > 500


def test_different_crs_with_the_same_name_are_mixed(fixtures_dir: Path) -> None:
    path = fixtures_dir / "shapefile_kalianpur.zip"
    layers = reader_for(path.name, max_uncompressed_bytes=10**8).read(path).layers
    crs_list = [layer.crs for layer in layers if layer.crs is not None]

    assert len(crs_list) == 2
    assert len({crs_label(crs) for crs in crs_list}) == 1
    assert summarize_crs(crs_list) == MIXED


def test_summarize_crs() -> None:
    assert summarize_crs([]) is None
    assert summarize_crs([UTM_43N, CRS.from_wkt(ESRI_UTM_43N)]) == "EPSG:32643"
    assert summarize_crs([UTM_43N, CRS.from_epsg(4326)]) == MIXED
