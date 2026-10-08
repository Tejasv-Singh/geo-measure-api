from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS

from app.core.errors import InvalidFileError
from app.services.readers.base import GDAL_ERRORS, BaseReader, RawLayer
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

    def read_layers(self, path: Path) -> Iterator[RawLayer]:
        if path.suffix.lower() == ".kmz":
            self._check_kmz(path)

        for layer_name in self.list_layer_names(path):
            frame = drop_empty_boilerplate(self.read_frame(path, path, layer=layer_name))
            # The KML spec fixes coordinates to WGS84 lon/lat, whatever GDAL reports.
            yield RawLayer(
                name=layer_name, frame=frame.set_crs(KML_CRS, allow_override=True), crs=KML_CRS
            )

    def list_layer_names(self, path: Path) -> list[str]:
        try:
            layers = pyogrio.list_layers(path)
        except GDAL_ERRORS as exc:
            raise self.gdal_error(exc, path, "GDAL could not read the KML document") from exc
        if len(layers) == 0:
            raise InvalidFileError("The KML document contains no layers.")
        return [str(name) for name, _ in layers]

    def _check_kmz(self, path: Path) -> None:
        names = safe_member_names(path, self.max_uncompressed_bytes)
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
