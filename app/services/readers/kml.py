from collections.abc import Iterator
from pathlib import Path

import pyogrio
from pyogrio.errors import DataSourceError
from pyproj import CRS

from app.core.errors import InvalidFileError
from app.services.readers.base import BaseReader, RawLayer, read_frame
from app.services.readers.zip_safety import safe_member_names

# Columns the LIBKML driver adds to every layer. Name, description and ExtendedData fields are kept.
LIBKML_BOILERPLATE = (
    "id",
    "timestamp",
    "begin",
    "end",
    "altitudeMode",
    "tessellate",
    "extrude",
    "visibility",
    "drawOrder",
    "icon",
)

KML_CRS = CRS.from_epsg(4326)


class KMLReader(BaseReader):
    format = "kml"
    extensions = (".kml", ".kmz")

    def read_layers(self, path: Path) -> Iterator[RawLayer]:
        if path.suffix.lower() == ".kmz":
            self._check_kmz(path)

        for layer_name in list_layer_names(path):
            frame = read_frame(path, layer=layer_name)
            frame = frame.drop(columns=[c for c in LIBKML_BOILERPLATE if c in frame.columns])
            # The KML spec fixes coordinates to WGS84 lon/lat, whatever GDAL reports.
            yield RawLayer(
                name=layer_name, frame=frame.set_crs(KML_CRS, allow_override=True), crs=KML_CRS
            )

    def _check_kmz(self, path: Path) -> None:
        names = safe_member_names(path, self.max_uncompressed_bytes)
        if not any(name.lower().endswith(".kml") for name in names):
            raise InvalidFileError("The KMZ archive does not contain a .kml document.")


def list_layer_names(path: Path) -> list[str]:
    try:
        layers = pyogrio.list_layers(path)
    except DataSourceError as exc:
        raise InvalidFileError(f"GDAL could not read the KML document: {exc}") from exc
    if len(layers) == 0:
        raise InvalidFileError("The KML document contains no layers.")
    return [str(name) for name, _ in layers]
