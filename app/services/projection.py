import math
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import ClassVar

import geopandas as gpd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.services.crs import WGS84

UTM_NORTH_LIMIT = 84.0
UTM_SOUTH_LIMIT = -80.0
UPS_NORTH_EPSG = 32661
UPS_SOUTH_EPSG = 32761


@dataclass(frozen=True, slots=True)
class Projection:
    crs: CRS
    label: str
    method: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectedGeometry:
    geometry: BaseGeometry
    projection: Projection


class ProjectionStrategy(ABC):
    method: ClassVar[str]

    @abstractmethod
    def projection_for(self, geometry: BaseGeometry) -> Projection:
        """Pick the projected CRS for one geometry given in EPSG:4326."""

    def project(self, geometries: Sequence[BaseGeometry]) -> list[ProjectedGeometry]:
        """Project EPSG:4326 geometries, with one vectorized to_crs per target CRS."""
        projections = [self.projection_for(geometry) for geometry in geometries]
        groups: dict[str, list[int]] = defaultdict(list)
        for position, projection in enumerate(projections):
            groups[projection.label].append(position)

        projected: list[BaseGeometry] = list(geometries)
        for positions in groups.values():
            target = projections[positions[0]].crs
            series = gpd.GeoSeries([geometries[i] for i in positions], crs=WGS84).to_crs(target)
            for position, geometry in zip(positions, series, strict=True):
                projected[position] = geometry
        return [
            ProjectedGeometry(geometry, projection)
            for geometry, projection in zip(projected, projections, strict=True)
        ]


class UTMStrategy(ProjectionStrategy):
    """UTM zone of the representative point, or UPS beyond the UTM latitude limits.

    Zones follow the regular 6 degree grid; the Norway and Svalbard exceptions are ignored,
    which only moves those features into a neighbouring, equally valid zone.
    """

    method = "utm"

    def projection_for(self, geometry: BaseGeometry) -> Projection:
        point = geometry.representative_point()
        return utm_projection(utm_epsg(point.x, point.y))


class LocalEqualAreaStrategy(ProjectionStrategy):
    """Lambert Azimuthal Equal-Area centred near the feature's representative point.

    LAEA preserves area everywhere, so the centre only affects shape. Snapping it to the middle
    of the feature's 1 degree cell lets nearby features share one CRS and project together.
    """

    method = "laea"

    def projection_for(self, geometry: BaseGeometry) -> Projection:
        point = geometry.representative_point()
        return laea_projection(*laea_centre(point.x, point.y))


def laea_centre(lon: float, lat: float) -> tuple[float, float]:
    return min(math.floor(lon), 179) + 0.5, min(math.floor(lat), 89) + 0.5


@lru_cache(maxsize=1024)
def laea_projection(lon_0: float, lat_0: float) -> Projection:
    definition = f"+proj=laea +lat_0={lat_0} +lon_0={lon_0} +datum=WGS84 +units=m +no_defs"
    return Projection(crs=CRS.from_proj4(definition), label=definition, method="laea")


def utm_epsg(lon: float, lat: float) -> int:
    if lat > UTM_NORTH_LIMIT:
        return UPS_NORTH_EPSG
    if lat < UTM_SOUTH_LIMIT:
        return UPS_SOUTH_EPSG
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=128)
def utm_projection(epsg: int) -> Projection:
    crs = CRS.from_epsg(epsg)
    if epsg in (UPS_NORTH_EPSG, UPS_SOUTH_EPSG):
        note = (
            "Latitude is outside the UTM limits (80S to 84N), so UPS was used. Its scale error "
            "here is 0.3% to 0.6%; geodesic_value is the more accurate figure."
        )
        return Projection(crs=crs, label=f"EPSG:{epsg}", method="ups", note=note)
    return Projection(crs=crs, label=f"EPSG:{epsg}", method="utm")
