import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import geopandas as gpd
from pyogrio.errors import DataLayerError, DataSourceError
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.core.errors import InvalidFileError
from app.services.json_safe import JSONValue, to_json_safe

logger = logging.getLogger(__name__)

GDAL_ERRORS = (DataSourceError, DataLayerError)


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    layer: str
    feature_index: int
    geometry: BaseGeometry | None
    geometry_type: str | None
    properties: dict[str, JSONValue]
    is_valid: bool | None


@dataclass(frozen=True, slots=True)
class LayerRecord:
    name: str
    crs: CRS | None
    features: list[FeatureRecord]


@dataclass(frozen=True, slots=True)
class ReadResult:
    format: str
    layers: list[LayerRecord]

    @property
    def features(self) -> list[FeatureRecord]:
        return [feature for layer in self.layers for feature in layer.features]


@dataclass(frozen=True, slots=True)
class RawLayer:
    name: str
    frame: gpd.GeoDataFrame
    crs: CRS | None


class BaseReader(ABC):
    format: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]

    def __init__(self, filename: str, max_uncompressed_bytes: int) -> None:
        self.filename = filename
        self.max_uncompressed_bytes = max_uncompressed_bytes

    def read(self, path: Path) -> ReadResult:
        layers: list[LayerRecord] = []
        next_index = 0
        for raw in self.read_layers(path):
            features = to_feature_records(raw, start_index=next_index)
            next_index += len(features)
            layers.append(LayerRecord(name=raw.name, crs=raw.crs, features=features))
        return ReadResult(format=self.format, layers=layers)

    @abstractmethod
    def read_layers(self, path: Path) -> Iterator[RawLayer]: ...

    def read_frame(
        self, path: Path, source: str | Path, layer: str | None = None
    ) -> gpd.GeoDataFrame:
        try:
            return gpd.read_file(
                source,
                layer=layer,
                engine="pyogrio",
                force_2d=True,
                on_invalid="fix",
            )
        except GDAL_ERRORS as exc:
            raise self.gdal_error(exc, path, "GDAL could not read the data") from exc

    def gdal_error(self, exc: Exception, path: Path, prefix: str) -> InvalidFileError:
        """Log the full GDAL message; return one naming the upload instead of its stored path."""
        message = str(exc)
        logger.warning("GDAL failed on %s stored at %s: %s", self.filename, path, message)
        return InvalidFileError(f"{prefix}: {redact_path(message, path, self.filename)}")


def redact_path(message: str, path: Path, filename: str) -> str:
    resolved = path.resolve()
    variants = {
        f"/vsizip/{resolved.as_posix()}",
        f"/vsizip/{path.as_posix()}",
        str(resolved),
        resolved.as_posix(),
        str(path),
        path.as_posix(),
    }
    for variant in sorted(variants, key=len, reverse=True):
        message = message.replace(variant, filename)
    return message


def to_feature_records(raw: RawLayer, start_index: int) -> list[FeatureRecord]:
    frame = raw.frame
    attributes = frame.drop(columns=frame.geometry.name)
    # to_dict("records") returns [] for a frame with rows but no columns.
    rows = attributes.to_dict("records") if len(attributes.columns) else [{}] * len(frame)
    return [
        _to_feature_record(raw.name, start_index + offset, geometry, row)
        for offset, (geometry, row) in enumerate(zip(frame.geometry, rows, strict=True))
    ]


def _to_feature_record(
    layer: str, index: int, geometry: BaseGeometry | None, row: dict[Any, Any]
) -> FeatureRecord:
    properties = to_json_safe(row)
    assert isinstance(properties, dict)
    return FeatureRecord(
        layer=layer,
        feature_index=index,
        geometry=geometry,
        geometry_type=geometry.geom_type if geometry is not None else None,
        properties=properties,
        is_valid=bool(geometry.is_valid) if geometry is not None else None,
    )
