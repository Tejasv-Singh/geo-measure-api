"""Per-feature area and length, measured in a projected CRS and checked against the ellipsoid.

Areas use a Lambert Azimuthal Equal-Area projection centred on each feature; lengths use the
feature's UTM zone. Z values are ignored, so every value is planimetric.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import geopandas as gpd
import shapely
from pyproj import CRS, Geod
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry
from shapely.geometry.polygon import orient

from app.services.crs import WGS84
from app.services.projection import LocalEqualAreaStrategy, ProjectionStrategy, UTMStrategy
from app.services.readers import FeatureRecord, LayerRecord

POLYGONAL = frozenset({"Polygon", "MultiPolygon"})
LINEAR = frozenset({"LineString", "MultiLineString", "LinearRing"})
PUNTAL = frozenset({"Point", "MultiPoint"})
COLLECTION = "GeometryCollection"

GEOD = Geod(ellps="WGS84")


class MeasurementKind(StrEnum):
    AREA = "AREA"
    LENGTH = "LENGTH"
    NONE = "NONE"


class MeasurementStatus(StrEnum):
    OK = "OK"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    EMPTY = "EMPTY"


UNITS = {MeasurementKind.AREA: "m2", MeasurementKind.LENGTH: "m"}


@dataclass(frozen=True, slots=True)
class Measurement:
    kind: MeasurementKind
    status: MeasurementStatus
    value: float | None = None
    unit: str | None = None
    projected_crs: str | None = None
    method: str | None = None
    note: str | None = None
    geodesic_value: float | None = None
    deviation_pct: float | None = None


@dataclass(frozen=True, slots=True)
class MeasuredFeature:
    feature: FeatureRecord
    geometry_wgs84: BaseGeometry | None
    measurements: list[Measurement]


@dataclass(frozen=True, slots=True)
class Target:
    """Polygonal or linear parts of one feature, still in the source CRS, waiting to be measured."""

    kind: MeasurementKind
    geometry: BaseGeometry
    status: MeasurementStatus
    note: str | None


STRATEGIES: dict[MeasurementKind, ProjectionStrategy] = {
    MeasurementKind.AREA: LocalEqualAreaStrategy(),
    MeasurementKind.LENGTH: UTMStrategy(),
}


def measure_layer(layer: LayerRecord, crs: CRS) -> list[MeasuredFeature]:
    """Measure every feature of a layer whose coordinates are in `crs`.

    A feature normally gets one measurement. A GeometryCollection with both polygonal and
    linear parts gets two: an AREA and a LENGTH.
    """
    results: list[list[Measurement]] = [[] for _ in layer.features]
    targets: list[tuple[int, Target]] = []
    for position, feature in enumerate(layer.features):
        for item in plan(feature.geometry):
            if isinstance(item, Measurement):
                results[position].append(item)
            else:
                targets.append((position, item))

    for kind in (MeasurementKind.AREA, MeasurementKind.LENGTH):
        batch = [(position, target) for position, target in targets if target.kind is kind]
        measured = measure_targets([target for _, target in batch], crs)
        for (position, _), measurement in zip(batch, measured, strict=True):
            results[position].append(measurement)

    geometries = to_wgs84([feature.geometry for feature in layer.features], crs)
    return [
        MeasuredFeature(feature, geometry, measurements)
        for feature, geometry, measurements in zip(layer.features, geometries, results, strict=True)
    ]


def plan(geometry: BaseGeometry | None) -> list[Measurement | Target]:
    """Decide what to measure: final measurements for features with nothing to measure,
    targets for the polygonal and linear parts of the rest."""
    if geometry is None or geometry.is_empty:
        return [Measurement(MeasurementKind.NONE, MeasurementStatus.EMPTY, note="No geometry.")]

    geometry_type = geometry.geom_type
    if geometry_type in PUNTAL:
        return [
            Measurement(
                MeasurementKind.NONE,
                MeasurementStatus.NOT_APPLICABLE,
                note="Points have no area or length.",
            )
        ]
    if geometry_type not in POLYGONAL | LINEAR | {COLLECTION}:
        return [
            Measurement(
                MeasurementKind.NONE,
                MeasurementStatus.UNSUPPORTED,
                note=f"Geometry type {geometry_type} is not supported.",
            )
        ]

    repaired, repair_note = repair(geometry)
    status = MeasurementStatus.INVALID_GEOMETRY if repair_note else MeasurementStatus.OK
    polygons, lines, points = split_parts(repaired)
    is_collection = geometry_type == COLLECTION

    planned: list[Measurement | Target] = []
    if geometry_type in POLYGONAL or (is_collection and polygons):
        planned.append(
            area_target(
                polygons,
                status,
                join(repair_note, collection_note(is_collection, polygons, "polygon", points)),
            )
        )
    if geometry_type in LINEAR or (is_collection and lines):
        planned.append(
            Target(
                MeasurementKind.LENGTH,
                MultiLineString([LineString(line.coords) for line in lines]),
                status,
                join(repair_note, collection_note(is_collection, lines, "line", points)),
            )
        )
    if not planned:
        planned.append(
            Measurement(
                MeasurementKind.NONE,
                MeasurementStatus.NOT_APPLICABLE,
                note=f"GeometryCollection holds only points ({len(points)}).",
            )
        )
    return planned


def area_target(
    polygons: list[Polygon], status: MeasurementStatus, note: str | None
) -> Measurement | Target:
    if not polygons:
        return Measurement(
            MeasurementKind.AREA,
            MeasurementStatus.INVALID_GEOMETRY,
            note=join(note, "No polygonal part is left after make_valid."),
        )
    return Target(MeasurementKind.AREA, MultiPolygon(polygons), status, note)


def repair(geometry: BaseGeometry) -> tuple[BaseGeometry, str | None]:
    if geometry.is_valid:
        return geometry, None
    reason = shapely.is_valid_reason(geometry)
    return shapely.make_valid(
        geometry
    ), f"Geometry was invalid ({reason}); measured after make_valid."


def split_parts(
    geometry: BaseGeometry,
) -> tuple[list[Polygon], list[LineString], list[BaseGeometry]]:
    polygons: list[Polygon] = []
    lines: list[LineString] = []
    others: list[BaseGeometry] = []
    for part in flatten(geometry):
        if isinstance(part, Polygon):
            polygons.append(part)
        elif isinstance(part, LineString):
            lines.append(part)
        else:
            others.append(part)
    return polygons, lines, others


def flatten(geometry: BaseGeometry) -> list[BaseGeometry]:
    if isinstance(geometry, BaseMultipartGeometry):
        return [part for child in geometry.geoms for part in flatten(child)]
    return [] if geometry.is_empty else [geometry]


def collection_note(
    is_collection: bool, parts: Sequence[BaseGeometry], noun: str, points: Sequence[BaseGeometry]
) -> str | None:
    if not is_collection:
        return None
    note = f"Summed {len(parts)} {noun} part(s) of a GeometryCollection."
    return f"{note} Ignored {len(points)} point part(s)." if points else note


def join(*notes: str | None) -> str | None:
    present = [note for note in notes if note]
    return " ".join(present) if present else None


def measure_targets(targets: Sequence[Target], crs: CRS) -> list[Measurement]:
    if not targets:
        return []
    kind = targets[0].kind
    geographic = to_wgs84([target.geometry for target in targets], crs)

    in_range = [i for i, geometry in enumerate(geographic) if is_lon_lat(geometry)]
    projected = STRATEGIES[kind].project([geographic[i] for i in in_range])
    by_position = dict(zip(in_range, projected, strict=True))

    measurements: list[Measurement] = []
    for position, (target, geometry) in enumerate(zip(targets, geographic, strict=True)):
        result = by_position.get(position)
        if result is None or geometry is None:
            measurements.append(
                unsupported(
                    kind,
                    "Coordinates are outside the valid longitude/latitude range after "
                    "conversion to EPSG:4326. Check the source CRS.",
                )
            )
            continue
        value = planar_value(kind, result.geometry)
        if not math.isfinite(value):
            measurements.append(unsupported(kind, "Projection produced non-finite coordinates."))
            continue
        geodesic = geodesic_value(kind, geometry)
        measurements.append(
            Measurement(
                kind=kind,
                status=target.status,
                value=value,
                unit=UNITS[kind],
                projected_crs=result.projection.label,
                method=result.projection.method,
                note=join(target.note, result.projection.note),
                geodesic_value=geodesic,
                deviation_pct=(value - geodesic) / geodesic * 100 if geodesic > 0 else None,
            )
        )
    return measurements


def unsupported(kind: MeasurementKind, note: str) -> Measurement:
    return Measurement(kind, MeasurementStatus.UNSUPPORTED, unit=UNITS[kind], note=note)


def planar_value(kind: MeasurementKind, geometry: BaseGeometry) -> float:
    return float(geometry.area if kind is MeasurementKind.AREA else geometry.length)


def geodesic_value(kind: MeasurementKind, geometry: BaseGeometry) -> float:
    if kind is MeasurementKind.LENGTH:
        return float(GEOD.geometry_length(geometry))
    # pyproj sums signed ring areas, so holes only subtract when rings are oriented.
    assert isinstance(geometry, MultiPolygon)
    oriented = MultiPolygon([orient(polygon, sign=1.0) for polygon in geometry.geoms])
    return abs(float(GEOD.geometry_area_perimeter(oriented)[0]))


def to_wgs84(geometries: Sequence[BaseGeometry | None], crs: CRS) -> list[BaseGeometry | None]:
    series = gpd.GeoSeries(list(geometries), crs=crs)
    if not crs.equals(WGS84):
        series = series.to_crs(WGS84)
    return list(series)


def is_lon_lat(geometry: BaseGeometry | None) -> bool:
    if geometry is None or geometry.is_empty:
        return False
    min_x, min_y, max_x, max_y = geometry.bounds
    finite = all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y))
    return finite and -180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90
