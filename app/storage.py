import hashlib
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.errors import FileTooLargeError

CHUNK_BYTES = 1024 * 1024
_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
# Exactly what save() generates, so a key can never name a path outside the root on any OS.
_KEY = re.compile(r"[0-9a-f]{32}\.[a-z0-9]{1,10}")


@dataclass(frozen=True, slots=True)
class StoredFile:
    key: str
    size_bytes: int
    sha256: str


class Storage(ABC):
    @abstractmethod
    def save(self, source: BinaryIO, suffix: str) -> StoredFile:
        """Store an upload and return its key, size and SHA-256."""

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """A local path GDAL can read. A remote backend would download to a cache here."""

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, root: Path, max_bytes: int, chunk_bytes: int = CHUNK_BYTES) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.chunk_bytes = chunk_bytes

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source: BinaryIO, suffix: str) -> StoredFile:
        """Copy in chunks, enforcing the size cap and hashing in the same pass."""
        suffix = suffix.lower()
        if not _SUFFIX.fullmatch(suffix):
            raise ValueError(f"Invalid file suffix {suffix!r}")
        key = f"{uuid.uuid4().hex}{suffix}"
        target = self.local_path(key)
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("xb") as out:
                while chunk := source.read(self.chunk_bytes):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FileTooLargeError(
                            "The file is larger than the upload limit.",
                            {"limit_bytes": self.max_bytes},
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return StoredFile(key=key, size_bytes=size, sha256=digest.hexdigest())

    def local_path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError(f"Invalid storage key {key!r}")
        return self.root / key

    def delete(self, key: str) -> None:
        self.local_path(key).unlink(missing_ok=True)
