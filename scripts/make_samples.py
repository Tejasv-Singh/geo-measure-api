"""Write the sample files in samples/. All attribute values are invented.

Usage: python -m scripts.make_samples [output-dir]

The output is deterministic, so running it again on an unchanged script changes no committed
file: zip entries get a fixed timestamp and the .dbf header a fixed date.
"""

import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from tests.fixtures.generate import golden_square_wgs84, write_shapefile_parts

UTM_43N = "EPSG:32643"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
# .dbf header bytes 1 to 3 hold the last-update date as years since 1900, month, day.
FIXED_DBF_DATE = bytes([126, 1, 1])


def coordinates(geometry: BaseGeometry) -> str:
    return " ".join(f"{x:.6f},{y:.6f}" for x, y, *_ in geometry.coords)


def ring(coords: str, indent: str) -> str:
    return f"{indent}<LinearRing><coordinates>{coords}</coordinates></LinearRing>"


def geometry_xml(geometry: BaseGeometry | list[BaseGeometry], indent: str) -> list[str]:
    if isinstance(geometry, list):
        inner = [line for part in geometry for line in geometry_xml(part, indent + "  ")]
        return [f"{indent}<MultiGeometry>", *inner, f"{indent}</MultiGeometry>"]
    if isinstance(geometry, Point):
        return [f"{indent}<Point><coordinates>{coordinates(geometry)}</coordinates></Point>"]
    if isinstance(geometry, LineString):
        coords = coordinates(geometry)
        return [f"{indent}<LineString><coordinates>{coords}</coordinates></LineString>"]
    if isinstance(geometry, Polygon):
        lines = [f"{indent}<Polygon>", f"{indent}  <outerBoundaryIs>"]
        lines.append(ring(coordinates(geometry.exterior), indent + "    "))
        lines.append(f"{indent}  </outerBoundaryIs>")
        for interior in geometry.interiors:
            lines.append(f"{indent}  <innerBoundaryIs>")
            lines.append(ring(coordinates(interior), indent + "    "))
            lines.append(f"{indent}  </innerBoundaryIs>")
        return [*lines, f"{indent}</Polygon>"]
    raise TypeError(f"No KML writer for {geometry.geom_type}")


def placemark(
    name: str,
    geometry: BaseGeometry | list[BaseGeometry],
    data: dict[str, str] | None = None,
    placemark_id: str | None = None,
) -> list[str]:
    opening = f'<Placemark id="{escape(placemark_id)}">' if placemark_id else "<Placemark>"
    lines = [f"    {opening}", f"      <name>{escape(name)}</name>"]
    if data:
        lines.append("      <ExtendedData>")
        lines += [
            f'        <Data name="{escape(key)}"><value>{escape(value)}</value></Data>'
            for key, value in data.items()
        ]
        lines.append("      </ExtendedData>")
    return [*lines, *geometry_xml(geometry, "      "), "    </Placemark>"]


def document(name: str, folders: dict[str, list[list[str]]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"  <name>{escape(name)}</name>",
    ]
    for folder, placemarks in folders.items():
        lines += ["  <Folder>", f"    <name>{escape(folder)}</name>"]
        lines += [line for mark in placemarks for line in mark]
        lines.append("  </Folder>")
    return "\n".join([*lines, "</Document>", "</kml>", ""])


def rectangle(lon: float, lat: float, width: float, height: float) -> Polygon:
    return Polygon(
        [(lon, lat), (lon + width, lat), (lon + width, lat + height), (lon, lat + height)]
    )


def survey_bengaluru() -> str:
    courtyard = rectangle(77.59340, 12.97125, 0.00020, 0.00015)
    plots = [
        placemark(
            "Plot 101",
            rectangle(77.59100, 12.97100, 0.00090, 0.00070),
            {"survey_no": "SY-101", "land_use": "residential", "surveyor": "Field team A"},
            placemark_id="plot-101",
        ),
        placemark(
            "Plot 102 with courtyard",
            Polygon(
                rectangle(77.59300, 12.97100, 0.00100, 0.00065).exterior.coords,
                [courtyard.exterior.coords],
            ),
            {"survey_no": "SY-102", "land_use": "commercial", "surveyor": "Field team A"},
            placemark_id="plot-102",
        ),
    ]
    infrastructure = [
        placemark(
            "Access road",
            LineString([(77.59050, 12.97080), (77.59250, 12.97085), (77.59450, 12.97090)]),
            {"surface": "asphalt", "lanes": "2"},
        ),
        placemark("Borewell", Point(77.59150, 12.97140), {"depth_m": "120"}),
        placemark(
            "Pump house and pipeline",
            [
                rectangle(77.59420, 12.97200, 0.00008, 0.00006),
                LineString([(77.59424, 12.97200), (77.59424, 12.97140), (77.59200, 12.97140)]),
                Point(77.59200, 12.97140),
            ],
            {"status": "planned"},
        ),
    ]
    return document("Survey, Bengaluru", {"Plots": plots, "Infrastructure": infrastructure})


def golden_square() -> str:
    mark = [
        "    <Placemark>",
        "      <name>1 km square</name>",
        "      <description>Built as a 1000 m square in EPSG:32643 at easting 680000, where "
        "the UTM scale factor is close to 1, then converted to EPSG:4326.</description>",
        "      <Polygon>",
        "        <outerBoundaryIs>",
        # Full precision, so the measured area reproduces the test to 1e-5.
        "          <LinearRing><coordinates>"
        + " ".join(f"{x!r},{y!r}" for x, y in golden_square_wgs84().exterior.coords)
        + "</coordinates></LinearRing>",
        "        </outerBoundaryIs>",
        "      </Polygon>",
        "    </Placemark>",
    ]
    return document("Golden square", {"Golden": [mark]})


def parcels_frame() -> gpd.GeoDataFrame:
    corners = gpd.GeoSeries(
        [Point(77.59100, 12.97100), Point(77.59300, 12.97100), Point(77.59100, 12.97300)],
        crs="EPSG:4326",
    ).to_crs(UTM_43N)
    sizes = [(90.0, 70.0), (110.0, 70.0), (60.0, 120.0)]
    polygons = [
        Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        for (x, y), (w, h) in zip(((round(p.x), round(p.y)) for p in corners), sizes, strict=True)
    ]
    return gpd.GeoDataFrame(
        {
            "parcel_id": ["P-001", "P-002", "P-003"],
            "survey_no": ["SY-201", "SY-202", "SY-203"],
            "land_use": ["residential", "commercial", "agricultural"],
            "recorded": ["2025-11-04", "2025-11-04", "2025-12-15"],
        },
        geometry=polygons,
        crs=UTM_43N,
    )


def write_deterministic_zip(path: Path, parts: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(parts):
            data = parts[name]
            if name.endswith(".dbf"):
                data = data[:1] + FIXED_DBF_DATE + data[4:]
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return path


def make_samples(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    survey = out / "survey_bengaluru.kml"
    survey.write_text(survey_bengaluru(), encoding="utf-8", newline="\n")
    golden = out / "golden_square.kml"
    golden.write_text(golden_square(), encoding="utf-8", newline="\n")

    parts = write_shapefile_parts(parcels_frame(), "parcels")
    with_prj = write_deterministic_zip(out / "parcels_utm43n.zip", parts)
    without = {name: data for name, data in parts.items() if not name.endswith(".prj")}
    no_prj = write_deterministic_zip(out / "parcels_no_prj.zip", without)
    return [survey, with_prj, no_prj, golden]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples")
    for written in make_samples(target):
        print(written)
