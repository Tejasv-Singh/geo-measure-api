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
) -> str:
    extended = ""
    if data:
        fields = "".join(
            f'<Data name="{escape(key)}"><value>{escape(value)}</value></Data>'
            for key, value in data.items()
        )
        extended = f"<ExtendedData>{fields}</ExtendedData>"
    desc = f"<description>{escape(description)}</description>" if description else ""
    return f"<Placemark><name>{escape(name)}</name>{desc}{extended}{geometry_xml}</Placemark>"


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


def kmz_sample(out: Path) -> Path:
    document = kml_document(
        {"Parcels": [kml_placemark("P1", kml_geometry(square(77.0, 28.0, 0.01)))]}
    )
    return write_zip(out / "sample.kmz", [("doc.kml", document.encode("utf-8"))])


GENERATORS: tuple[Callable[[Path], Path], ...] = (
    shapefile_two_layers,
    shapefile_no_prj,
    shapefile_missing_dbf,
    shapefile_cp1251,
    shapefile_self_intersecting,
    shapefile_path_traversal,
    empty_zip,
    not_a_zip,
    kml_extended_data,
    kml_multi_folder,
    kml_geometry_collection,
    kml_3d,
    kmz_sample,
)


def generate_all(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    return [generate(out) for generate in GENERATORS]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/out")
    for written in generate_all(target):
        print(written)
