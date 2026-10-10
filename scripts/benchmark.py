"""Timing figures quoted in the README. Usage: python -m scripts.benchmark

1. Projecting 2,000 polygons spread over 3 x 3 degrees with LocalEqualAreaStrategy, against the
   same strategy with one CRS per feature (the behaviour before centre snapping).
2. Processing a 60,000-polygon zipped shapefile end to end on SQLite: read, measure, store.
"""

import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from app.db import create_db_engine, create_session_factory, run_migrations
from app.models import GeoFile
from app.services.pipeline import run_file_job
from app.services.projection import LocalEqualAreaStrategy, Projection, laea_projection
from app.services.readers import ReadLimits
from app.storage import LocalStorage
from tests.fixtures.generate import in_folder, write_shapefile_parts, write_zip


class PerFeatureCentre(LocalEqualAreaStrategy):
    def projection_for(self, geometry: BaseGeometry) -> Projection:
        point = geometry.representative_point()
        definition = f"+proj=laea +lat_0={point.y} +lon_0={point.x} +datum=WGS84 +units=m"
        return Projection(CRS.from_proj4(definition), definition, self.method)


def squares(
    count: int, span_degrees: float, lon: float = 77.0, lat: float = 12.0
) -> list[BaseGeometry]:
    side = int(count**0.5) + 1
    step = span_degrees / side
    return [
        box(lon + i * step, lat + j * step, lon + i * step + step / 4, lat + j * step + step / 4)
        for i in range(side)
        for j in range(side)
    ][:count]


def timed(
    function: Callable[[list[BaseGeometry]], object], geometries: list[BaseGeometry]
) -> float:
    started = time.perf_counter()
    function(geometries)
    return time.perf_counter() - started


def projection_benchmark() -> None:
    geometries = squares(2_000, 3.0)
    laea_projection.cache_clear()
    snapped = timed(LocalEqualAreaStrategy().project, geometries)
    per_feature = timed(PerFeatureCentre().project, geometries)
    print(
        f"LAEA, 2,000 polygons: per-feature centre {per_feature:.2f} s, "
        f"snapped centre {snapped:.2f} s ({per_feature / snapped:.0f}x)"
    )


def pipeline_benchmark(work: Path) -> None:
    frame = gpd.GeoDataFrame({"n": range(60_000)}, geometry=squares(60_000, 1.0), crs="EPSG:4326")
    archive = write_zip(work / "big.zip", in_folder("", write_shapefile_parts(frame, "big")))

    engine = create_db_engine(f"sqlite:///{(work / 'bench.db').as_posix()}")
    run_migrations(engine)
    session_factory = create_session_factory(engine)
    storage = LocalStorage(work / "uploads", max_bytes=10**9)
    storage.prepare()
    with archive.open("rb") as source:
        stored = storage.save(source, ".zip")
    with session_factory() as session:
        geo_file = GeoFile(
            id=uuid.uuid4(),
            filename="big.zip",
            format="shapefile",
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_key=stored.key,
        )
        session.add(geo_file)
        session.commit()
        file_id = geo_file.id

    run_file_job(file_id, session_factory, storage, ReadLimits(10**9))
    with session_factory() as session:
        done = session.get(GeoFile, file_id)
        assert done is not None
        print(
            f"Pipeline, 60,000 polygons on SQLite: {done.status}, "
            f"processing_ms={done.processing_ms}, archive {stored.size_bytes / 1e6:.1f} MB"
        )
    engine.dispose()


if __name__ == "__main__":
    projection_benchmark()
    with tempfile.TemporaryDirectory() as tmp:
        pipeline_benchmark(Path(tmp))
