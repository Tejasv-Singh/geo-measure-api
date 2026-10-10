from pathlib import Path
from typing import BinaryIO

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS

from app.core.errors import InvalidFileError
from app.services.readers.base import GDAL_ERRORS, BaseReader, LayerSource, RawLayer
from app.services.readers.zip_safety import safe_member_names

# Columns the LIBKML driver adds to every layer, with the value it fills in when the KML is silent.
# A column is dropped only when it carries nothing else: "id" is the Placemark's own id attribute,
# and timestamp/begin/end hold TimeStamp and TimeSpan values.
LIBKML_DEFAULTS: dict[str, object] = {
    "id": None,
    "timestamp": None,
    "begin": None,
    "end": None,
    "altitudeMode": None,
    "tessellate": -1,
    "extrude": 0,
    "visibility": -1,
    "drawOrder": None,
    "icon": None,
}

KML_CRS = CRS.from_epsg(4326)


class KMLReader(BaseReader):
    format = "kml"
    extensions = (".kml", ".kmz")

    def check_upload(self, source: BinaryIO) -> None:
        if not self.filename.lower().endswith(".kmz"):
            return
        try:
            self._check_kmz(source)
        finally:
            source.seek(0)

    def layer_sources(self, path: Path) -> list[LayerSource]:
        if path.suffix.lower() == ".kmz":
            self._check_kmz(path)
        return [LayerSource(name=name, source=path, layer=name) for name in self.layer_names(path)]

    def read_layer(self, path: Path, source: LayerSource) -> RawLayer:
        frame = drop_empty_boilerplate(self.read_frame(path, source.source, layer=source.layer))
        # The KML spec fixes coordinates to WGS84 lon/lat, whatever GDAL reports.
        return RawLayer(
            name=source.name, frame=frame.set_crs(KML_CRS, allow_override=True), crs=KML_CRS
        )

    def layer_names(self, path: Path) -> list[str]:
        try:
            layers = pyogrio.list_layers(path)
        except GDAL_ERRORS as exc:
            raise self.gdal_error(exc, path, "GDAL could not read the KML document") from exc
        if len(layers) == 0:
            raise InvalidFileError("The KML document contains no layers.")
        return [str(name) for name, _ in layers]

    def _check_kmz(self, archive: Path | BinaryIO) -> None:
        names = safe_member_names(archive, self.limits.max_uncompressed_bytes)
        if not any(name.lower().endswith(".kml") for name in names):
            raise InvalidFileError("The KMZ archive does not contain a .kml document.")


def drop_empty_boilerplate(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    empty = [
        column
        for column, default in LIBKML_DEFAULTS.items()
        if column in frame.columns and carries_no_data(frame[column], default)
    ]
    return frame.drop(columns=empty)


def carries_no_data(column: pd.Series, default: object) -> bool:
    return bool((column.isna() | (column == default)).all())
