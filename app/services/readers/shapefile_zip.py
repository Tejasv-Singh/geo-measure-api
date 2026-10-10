from collections import Counter
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.core.errors import InvalidFileError
from app.services.readers.base import BaseReader, LayerSource, RawLayer
from app.services.readers.zip_safety import safe_member_names

REQUIRED_SIDECARS = (".shx", ".dbf")


class ShapefileZipReader(BaseReader):
    format = "shapefile"
    extensions = (".zip",)

    def check_upload(self, source: BinaryIO) -> None:
        try:
            find_shapefiles(safe_member_names(source, self.limits.max_uncompressed_bytes))
        finally:
            source.seek(0)

    def layer_sources(self, path: Path) -> list[LayerSource]:
        names = safe_member_names(path, self.limits.max_uncompressed_bytes)
        shapefiles = find_shapefiles(names)
        archive = path.resolve().as_posix()
        lowered = {name.lower() for name in names}
        return [
            # GDAL reads straight from the archive, and picks up the .prj and .cpg beside the .shp.
            LayerSource(
                name=layer_name,
                source=f"/vsizip/{archive}/{shp}",
                has_crs_file=_sidecar(shp, ".prj").lower() in lowered,
            )
            for shp, layer_name in zip(shapefiles, layer_names(shapefiles), strict=True)
        ]

    def read_layer(self, path: Path, source: LayerSource) -> RawLayer:
        frame = self.read_frame(path, source.source)
        # GDAL returns no CRS both for a missing .prj and for one it cannot parse.
        if frame.crs is None and source.has_crs_file:
            raise InvalidFileError("The .prj file could not be parsed.", {"layer": source.name})
        return RawLayer(name=source.name, frame=frame, crs=frame.crs)


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
