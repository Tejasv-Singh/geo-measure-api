import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.core.errors import InvalidFileError

_JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def safe_member_names(archive_file: Path | BinaryIO, max_uncompressed_bytes: int) -> list[str]:
    """Validate a zip archive without extracting it and return its meaningful file entries.

    Raises InvalidFileError for corrupt or encrypted archives, unsafe entry names,
    archives that exceed the uncompressed size cap, and archives with no usable files.
    """
    try:
        with zipfile.ZipFile(archive_file) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise InvalidFileError("The file is not a valid zip archive.") from exc

    unsafe = [info.filename for info in infos if is_unsafe_name(info.filename)]
    if unsafe:
        raise InvalidFileError("The archive contains unsafe file paths.", {"entries": unsafe})
    if any(info.flag_bits & 0x1 for info in infos):
        raise InvalidFileError("Encrypted zip archives are not supported.")

    total = sum(info.file_size for info in infos)
    if total > max_uncompressed_bytes:
        raise InvalidFileError(
            "The archive is too large when uncompressed.",
            {"uncompressed_bytes": total, "limit_bytes": max_uncompressed_bytes},
        )

    names = [info.filename for info in infos if not info.is_dir() and not is_junk(info.filename)]
    if not names:
        raise InvalidFileError("The archive is empty.")
    return names


def is_unsafe_name(name: str) -> bool:
    if "\x00" in name or name.startswith(("/", "\\")) or _DRIVE_LETTER.match(name):
        return True
    return ".." in re.split(r"[\\/]", name)


def is_junk(name: str) -> bool:
    path = PurePosixPath(name)
    return path.parts[0] == "__MACOSX" or path.name.startswith("._") or path.name in _JUNK_NAMES
