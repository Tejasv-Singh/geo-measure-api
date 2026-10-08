"""Generate the test fixtures from code so no binary blobs are checked in.

Usage: python -m tests.fixtures.generate <output-dir>
"""

import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import geopandas as gpd
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

UTM_43N = "EPSG:32643"
# ArcGIS-style .prj files that pyproj cannot match to an EPSG code. Both share one name.
KALIANPUR_IIIA_2SP = (
    'PROJCS["Kalianpur_1975_India_Zone_IIIa",GEOGCS["GCS_Kalianpur_1975",'
    'DATUM["D_Kalianpur_1975",SPHEROID["Everest_Definition_1975",6377299.151,300.8017255]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Lambert_Conformal_Conic"],PARAMETER["False_Easting",2743195.5],'
    'PARAMETER["False_Northing",914398.5],PARAMETER["Central_Meridian",80.0],'
    'PARAMETER["Standard_Parallel_1",19.0],PARAMETER["Standard_Parallel_2",19.0],'
    'PARAMETER["Scale_Factor",0.99878641],PARAMETER["Latitude_Of_Origin",19.0],'
    'UNIT["Meter",1.0]]'
)
KALIANPUR_IIIA_EVEREST_1830 = (
    'PROJCS["Kalianpur_1975_India_Zone_IIIa",GEOGCS["GCS_Kalianpur_1975",'
    'DATUM["D_Kalianpur_1975",SPHEROID["Everest_1830",6377276.345,300.8017]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Lambert_Conformal_Conic"],PARAMETER["False_Easting",2743195.5],'
    'PARAMETER["False_Northing",914398.5],PARAMETER["Central_Meridian",80.0],'
    'PARAMETER["Standard_Parallel_1",19.0],PARAMETER["Scale_Factor",0.99878641],'
    'PARAMETER["Latitude_Of_Origin",19.0],UNIT["Meter",1.0]]'
)
# About 180 km east of the zone 43N central meridian, where the UTM scale factor is close to 1,
# so a 1 km grid square is also 1 km2 on the ground. At the central meridian it is 0.9996.
GOLDEN_EASTING = 680_000
GOLDEN_NORTHING = 3_100_000
SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def square(x: float, y: float, size: float) -> Polygon:
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def parcels_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "parcel_id": [1, 2],
            "owner": ["Asha", None],
            "area_ha": [1.5, float("nan")],
            "surveyed": [date(2024, 3, 1), None],
        },
        geometry=[square(500_000, 3_100_000, 100), square(500_200, 3_100_000, 50)],
        crs=UTM_43N,
    )


def roads_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["Ring Road"]},
        geometry=[LineString([(500_000, 3_100_000), (501_000, 3_100_000)])],
        crs=UTM_43N,
    )


def write_shapefile_parts(
    frame: gpd.GeoDataFrame, stem: str, encoding: str = "UTF-8"
) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        frame.to_file(Path(tmp) / f"{stem}.shp", engine="pyogrio", encoding=encoding)
        return {
            path.name: path.read_bytes()
            for path in Path(tmp).iterdir()
            if path.suffix.lower() in SHAPEFILE_PARTS
        }


def write_zip(path: Path, members: Iterable[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


def in_folder(
    folder: str, parts: dict[str, bytes], skip: tuple[str, ...] = ()
) -> list[tuple[str, bytes]]:
    prefix = f"{folder}/" if folder else ""
    return [(prefix + name, data) for name, data in parts.items() if not name.endswith(skip)]


def shapefile_two_layers(out: Path) -> Path:
    members = [
        *in_folder("data", write_shapefile_parts(parcels_frame(), "parcels")),
        *in_folder("data/roads", write_shapefile_parts(roads_frame(), "roads")),
        ("__MACOSX/data/._parcels.shp", b"junk"),
        ("data/.DS_Store", b"junk"),
    ]
    return write_zip(out / "shapefile_two_layers.zip", members)


def shapefile_no_prj(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    return write_zip(out / "shapefile_no_prj.zip", in_folder("", parts, skip=(".prj",)))


def shapefile_missing_dbf(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    return write_zip(out / "shapefile_missing_dbf.zip", in_folder("", parts, skip=(".dbf",)))


def shapefile_corrupt_dbf(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    parts["parcels.dbf"] = bytes(40)
    return write_zip(out / "shapefile_corrupt_dbf.zip", in_folder("", parts))


def shapefile_bad_prj(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    parts["parcels.prj"] = b"this is not a projection"
    return write_zip(out / "shapefile_bad_prj.zip", in_folder("", parts))


def shapefile_corrupt_shp(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    parts["parcels.shp"] = bytes(10)
    return write_zip(out / "shapefile_corrupt_shp.zip", in_folder("data", parts))


def shapefile_kalianpur(out: Path) -> Path:
    """Two layers whose .prj files share a CRS name but differ in their ellipsoid."""
    frame = gpd.GeoDataFrame(
        {"name": ["Survey line"]},
        geometry=[LineString([(2_743_195, 914_398), (2_744_195, 914_398)])],
    )
    zone = write_shapefile_parts(frame, "zone")
    zone["zone.prj"] = KALIANPUR_IIIA_2SP.encode("ascii")
    legacy = write_shapefile_parts(frame, "legacy")
    legacy["legacy.prj"] = KALIANPUR_IIIA_EVEREST_1830.encode("ascii")
    members = [*in_folder("", zone), *in_folder("", legacy)]
    return write_zip(out / "shapefile_kalianpur.zip", members)


def shapefile_cp1251(out: Path) -> Path:
    frame = gpd.GeoDataFrame(
        {"city": ["Москва"]}, geometry=[square(500_000, 3_100_000, 10)], crs=UTM_43N
    )
    parts = write_shapefile_parts(frame, "cities", encoding="cp1251")
    return write_zip(out / "shapefile_cp1251.zip", in_folder("", parts))


def shapefile_self_intersecting(out: Path) -> Path:
    x, y = 500_000, 3_100_000
    bowtie = Polygon([(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100)])
    frame = gpd.GeoDataFrame({"label": ["bowtie"]}, geometry=[bowtie], crs=UTM_43N)
    parts = write_shapefile_parts(frame, "bowtie")
    return write_zip(out / "shapefile_self_intersecting.zip", in_folder("", parts))


def shapefile_path_traversal(out: Path) -> Path:
    parts = write_shapefile_parts(parcels_frame(), "parcels")
    return write_zip(
        out / "shapefile_path_traversal.zip",
        [*in_folder("", parts), ("../../evil.txt", b"owned")],
    )


def empty_zip(out: Path) -> Path:
    return write_zip(out / "empty.zip", [])


def not_a_zip(out: Path) -> Path:
    path = out / "not_a_zip.zip"
    path.write_text("this is plain text")
    return path


def kml_coordinates(coords: Iterable[tuple[float, ...]]) -> str:
    return " ".join(",".join(str(v) for v in coord) for coord in coords)


def kml_geometry(geometry: BaseGeometry) -> str:
    if isinstance(geometry, Point):
        return f"<Point><coordinates>{kml_coordinates(geometry.coords)}</coordinates></Point>"
    if isinstance(geometry, LineString):
        coords = kml_coordinates(geometry.coords)
        return f"<LineString><coordinates>{coords}</coordinates></LineString>"
    if isinstance(geometry, Polygon):
        ring = kml_coordinates(geometry.exterior.coords)
        return (
            "<Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{ring}</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon>"
        )
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        inner = "".join(kml_geometry(part) for part in geometry.geoms)
        return f"<MultiGeometry>{inner}</MultiGeometry>"
    raise TypeError(f"No KML writer for {geometry.geom_type}")


def kml_placemark(
    name: str,
    geometry_xml: str,
    data: dict[str, str] | None = None,
    description: str | None = None,
    placemark_id: str | None = None,
    time_xml: str = "",
) -> str:
    extended = ""
    if data:
        fields = "".join(
            f'<Data name="{escape(key)}"><value>{escape(value)}</value></Data>'
            for key, value in data.items()
        )
        extended = f"<ExtendedData>{fields}</ExtendedData>"
    desc = f"<description>{escape(description)}</description>" if description else ""
    opening = f'<Placemark id="{escape(placemark_id)}">' if placemark_id else "<Placemark>"
    body = f"<name>{escape(name)}</name>{desc}{time_xml}{extended}{geometry_xml}"
    return f"{opening}{body}</Placemark>"


def kml_document(folders: dict[str, list[str]]) -> str:
    body = "".join(
        f"<Folder><name>{escape(name)}</name>{''.join(placemarks)}</Folder>"
        for name, placemarks in folders.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2">'
        f"<Document><name>fixture</name>{body}</Document></kml>"
    )


def kml_extended_data(out: Path) -> Path:
    placemark = kml_placemark(
        "Plot 42",
        kml_geometry(square(77.0, 28.0, 0.01)),
        data={"owner": "Asha", "plot_no": "42"},
        description="Northern plot",
    )
    path = out / "extended_data.kml"
    path.write_text(kml_document({"Plots": [placemark]}), encoding="utf-8")
    return path


def kml_ids_and_times(out: Path) -> Path:
    placemarks = [
        kml_placemark(
            "Plot 17",
            kml_geometry(square(77.0, 28.0, 0.01)),
            placemark_id="plot-17",
            time_xml="<TimeStamp><when>2024-03-01T10:30:00Z</when></TimeStamp>",
        ),
        kml_placemark(
            "Survey line",
            kml_geometry(LineString([(77.0, 28.0), (77.02, 28.0)])),
            time_xml="<TimeSpan><begin>2023-01-01</begin><end>2023-12-31</end></TimeSpan>",
        ),
    ]
    path = out / "ids_and_times.kml"
    path.write_text(kml_document({"Plots": placemarks}), encoding="utf-8")
    return path


def kml_multi_folder(out: Path) -> Path:
    folders = {
        "Parcels": [kml_placemark("P1", kml_geometry(square(77.0, 28.0, 0.01)))],
        "Roads": [
            kml_placemark("R1", kml_geometry(LineString([(77.0, 28.0), (77.02, 28.0)]))),
            kml_placemark("R2", kml_geometry(LineString([(77.0, 28.01), (77.02, 28.01)]))),
        ],
    }
    path = out / "multi_folder.kml"
    path.write_text(kml_document(folders), encoding="utf-8")
    return path


def kml_geometry_collection(out: Path) -> Path:
    mixed = GeometryCollection(
        [square(77.0, 28.0, 0.01), LineString([(77.0, 28.0), (77.01, 28.01)]), Point(77, 28)]
    )
    path = out / "geometry_collection.kml"
    path.write_text(
        kml_document({"Mixed": [kml_placemark("mixed", kml_geometry(mixed))]}), encoding="utf-8"
    )
    return path


def kml_3d(out: Path) -> Path:
    ring = [(77.0, 28.0, 250.0), (77.01, 28.0, 260.0), (77.01, 28.01, 270.0), (77.0, 28.0, 250.0)]
    path = out / "three_d.kml"
    path.write_text(
        kml_document({"Hills": [kml_placemark("hill", kml_geometry(Polygon(ring)))]}),
        encoding="utf-8",
    )
    return path


def kml_not_xml(out: Path) -> Path:
    path = out / "not_xml.kml"
    path.write_text("this is plain text", encoding="utf-8")
    return path


def golden_square_wgs84() -> Polygon:
    square_utm = gpd.GeoSeries([square(GOLDEN_EASTING, GOLDEN_NORTHING, 1000)], crs=UTM_43N)
    polygon = square_utm.to_crs("EPSG:4326").iloc[0]
    assert isinstance(polygon, Polygon)
    return polygon


def kml_golden_square(out: Path) -> Path:
    placemark = kml_placemark("1 km square", kml_geometry(golden_square_wgs84()))
    path = out / "golden_square.kml"
    path.write_text(kml_document({"Golden": [placemark]}), encoding="utf-8")
    return path


def kmz_sample(out: Path) -> Path:
    document = kml_document(
        {"Parcels": [kml_placemark("P1", kml_geometry(square(77.0, 28.0, 0.01)))]}
    )
    return write_zip(out / "sample.kmz", [("doc.kml", document.encode("utf-8"))])


GENERATORS: tuple[Callable[[Path], Path], ...] = (
    shapefile_two_layers,
    shapefile_no_prj,
    shapefile_missing_dbf,
    shapefile_corrupt_dbf,
    shapefile_bad_prj,
    shapefile_corrupt_shp,
    shapefile_kalianpur,
    shapefile_cp1251,
    shapefile_self_intersecting,
    shapefile_path_traversal,
    empty_zip,
    not_a_zip,
    kml_extended_data,
    kml_ids_and_times,
    kml_multi_folder,
    kml_geometry_collection,
    kml_3d,
    kml_not_xml,
    kml_golden_square,
    kmz_sample,
)


def generate_all(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    return [generate(out) for generate in GENERATORS]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/out")
    for written in generate_all(target):
        print(written)
