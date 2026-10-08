from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from app.core.errors import InvalidFileError
from app.services.readers.base import BaseReader, RawLayer
from app.services.readers.zip_safety import safe_member_names

REQUIRED_SIDECARS = (".shx", ".dbf")


class ShapefileZipReader(BaseReader):
    format = "shapefile"
    extensions = (".zip",)

    def read_layers(self, path: Path) -> Iterator[RawLayer]:
        names = safe_member_names(path, self.max_uncompressed_bytes)
        shapefiles = find_shapefiles(names)
        archive = path.resolve().as_posix()
        lowered = {name.lower() for name in names}
        for shp, layer_name in zip(shapefiles, layer_names(shapefiles), strict=True):
            # GDAL reads straight from the archive, and picks up the .prj and .cpg beside the .shp.
            frame = self.read_frame(path, f"/vsizip/{archive}/{shp}")
            # GDAL returns no CRS both for a missing .prj and for one it cannot parse.
            if frame.crs is None and _sidecar(shp, ".prj").lower() in lowered:
                raise InvalidFileError("The .prj file could not be parsed.", {"layer": layer_name})
            yield RawLayer(name=layer_name, frame=frame, crs=frame.crs)


def find_shapefiles(names: list[str]) -> list[str]:
    lowered = {name.lower() for name in names}
    shapefiles = sorted(name for name in names if name.lower().endswith(".shp"))
    if not shapefiles:
        raise InvalidFileError("The archive does not contain a .shp file.")

    missing = {
        shp: [ext for ext in REQUIRED_SIDECARS if _sidecar(shp, ext).lower() not in lowered]
        for shp in shapefiles
    }
    missing = {shp: exts for shp, exts in missing.items() if exts}
    if missing:
        raise InvalidFileError(
            "Every .shp file needs a matching .shx and .dbf file.", {"missing": missing}
        )
    return shapefiles


def layer_names(shapefiles: list[str]) -> list[str]:
    """Use the file stem as the layer name, or the full path when stems collide across folders."""
    stems = [PurePosixPath(shp).stem for shp in shapefiles]
    counts = Counter(stems)
    return [
        stem if counts[stem] == 1 else str(PurePosixPath(shp).with_suffix(""))
        for shp, stem in zip(shapefiles, stems, strict=True)
    ]


def _sidecar(shp: str, ext: str) -> str:
    return str(PurePosixPath(shp).with_suffix(ext))
