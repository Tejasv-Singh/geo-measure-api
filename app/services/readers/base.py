from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import geopandas as gpd
from pyogrio.errors import DataSourceError
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.core.errors import InvalidFileError
from app.services.json_safe import JSONValue, to_json_safe


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

    def __init__(self, max_uncompressed_bytes: int) -> None:
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


def read_frame(source: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(
            source,
            layer=layer,
            engine="pyogrio",
            force_2d=True,
            on_invalid="fix",
        )
    except DataSourceError as exc:
        raise InvalidFileError(f"GDAL could not read the data: {exc}") from exc


def to_feature_records(raw: RawLayer, start_index: int) -> list[FeatureRecord]:
    frame = raw.frame
    attributes = frame.drop(columns=frame.geometry.name).to_dict("records")
    return [
        _to_feature_record(raw.name, start_index + offset, geometry, row)
        for offset, (geometry, row) in enumerate(zip(frame.geometry, attributes, strict=True))
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
