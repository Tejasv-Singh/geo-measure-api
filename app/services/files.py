import uuid
from pathlib import PurePath
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InvalidFileError, NotFoundError
from app.models import FileStatus, GeoFile
from app.services.crs import parse_crs
from app.services.readers import reader_for
from app.storage import Storage

MAGIC_BYTES = 512
ZIP_MAGIC = b"PK\x03\x04"
UTF8_BOM = b"\xef\xbb\xbf"
MAX_FILENAME = 255


def accept_upload(
    session: Session,
    storage: Storage,
    filename: str | None,
    source: BinaryIO,
    source_crs: str | None,
    max_uncompressed_bytes: int,
) -> GeoFile:
    """Validate and store an upload and create its PENDING row. Processing happens later."""
    name = clean_filename(filename)
    reader = reader_for(name, max_uncompressed_bytes)
    requested_crs = clean_source_crs(source_crs)
    suffix = PurePath(name).suffix.lower()
    check_magic(suffix, source)

    stored = storage.save(source, suffix)
    geo_file = GeoFile(
        filename=name,
        format=reader.format,
        status=FileStatus.PENDING,
        requested_crs=requested_crs,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        storage_key=stored.key,
    )
    try:
        session.add(geo_file)
        session.commit()
    except Exception:
        storage.delete(stored.key)
        raise
    return geo_file


def clean_filename(filename: str | None) -> str:
    # Some clients send a full client-side path; keep only the last part.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        raise InvalidFileError("The upload has no filename.")
    return name[-MAX_FILENAME:]


def clean_source_crs(source_crs: str | None) -> str | None:
    value = (source_crs or "").strip()
    if not value:
        return None
    parse_crs(value)
    return value


def check_magic(suffix: str, source: BinaryIO) -> None:
    head = source.read(MAGIC_BYTES)
    source.seek(0)
    is_zip = suffix in (".zip", ".kmz")
    matches = head.startswith(ZIP_MAGIC) if is_zip else looks_like_xml(head)
    if not matches:
        raise InvalidFileError(f"The file content does not match its {suffix} extension.")


def looks_like_xml(head: bytes) -> bool:
    text = head.removeprefix(UTF8_BOM).lstrip()
    return text.startswith((b"<?xml", b"<kml"))


def get_file(session: Session, file_id: uuid.UUID) -> GeoFile:
    geo_file = session.get(GeoFile, file_id)
    if geo_file is None:
        raise NotFoundError("File not found.", {"id": str(file_id)})
    return geo_file


def list_files(
    session: Session, status: FileStatus | None, limit: int, offset: int
) -> tuple[list[GeoFile], int]:
    query = select(GeoFile)
    if status is not None:
        query = query.where(GeoFile.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = query.order_by(GeoFile.created_at.desc(), GeoFile.id).limit(limit).offset(offset)
    return list(session.scalars(page)), total


def delete_file(session: Session, storage: Storage, file_id: uuid.UUID) -> None:
    geo_file = get_file(session, file_id)
    key = geo_file.storage_key
    # Layers, features and measurements go with it through ON DELETE CASCADE.
    session.delete(geo_file)
    session.commit()
    storage.delete(key)
