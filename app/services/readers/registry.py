from pathlib import PurePath

from app.core.errors import UnsupportedFileError
from app.services.readers.base import BaseReader
from app.services.readers.kml import KMLReader
from app.services.readers.shapefile_zip import ShapefileZipReader

_READERS: dict[str, type[BaseReader]] = {
    extension: reader
    for reader in (ShapefileZipReader, KMLReader)
    for extension in reader.extensions
}


def supported_extensions() -> list[str]:
    return sorted(_READERS)


def reader_for(filename: str, max_uncompressed_bytes: int) -> BaseReader:
    extension = PurePath(filename).suffix.lower()
    reader = _READERS.get(extension)
    if reader is None:
        raise UnsupportedFileError(
            f"Unsupported file extension '{extension}'.",
            {"supported": supported_extensions()},
        )
    return reader(filename=filename, max_uncompressed_bytes=max_uncompressed_bytes)
