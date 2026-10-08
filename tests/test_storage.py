import hashlib
import io
from pathlib import Path

import pytest

from app.core.errors import FileTooLargeError
from app.storage import LocalStorage


class CountingReader(io.BytesIO):
    """Records each read size, to show the copy is chunked."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.reads: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        self.reads.append(-1 if size is None else size)
        return super().read(size)


def make_storage(tmp_path: Path, max_bytes: int = 1000) -> LocalStorage:
    storage = LocalStorage(tmp_path / "uploads", max_bytes=max_bytes, chunk_bytes=64)
    storage.prepare()
    return storage


def test_save_streams_in_chunks_and_hashes(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    data = bytes(range(256)) * 3
    source = CountingReader(data)

    stored = storage.save(source, ".ZIP")

    assert stored.key.endswith(".zip")
    assert stored.size_bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert storage.local_path(stored.key).read_bytes() == data
    assert set(source.reads) == {64}


def test_save_stops_at_the_limit_and_leaves_nothing_behind(tmp_path: Path) -> None:
    storage = make_storage(tmp_path, max_bytes=100)
    source = CountingReader(bytes(10_000))

    with pytest.raises(FileTooLargeError) as exc_info:
        storage.save(source, ".kml")

    assert exc_info.value.status_code == 413
    assert exc_info.value.details == {"limit_bytes": 100}
    assert len(source.reads) == 2
    assert list((tmp_path / "uploads").iterdir()) == []


def test_file_at_exactly_the_limit_is_accepted(tmp_path: Path) -> None:
    storage = make_storage(tmp_path, max_bytes=128)

    assert storage.save(io.BytesIO(bytes(128)), ".kml").size_bytes == 128


@pytest.mark.parametrize("key", ["../x.zip", "a/b.zip", "..\\x.zip"])
def test_local_path_rejects_keys_with_directories(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="Invalid storage key"):
        make_storage(tmp_path).local_path(key)


@pytest.mark.parametrize("suffix", ["", "zip", ".zi/p", "/../x"])
def test_save_rejects_odd_suffixes(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError, match="Invalid file suffix"):
        make_storage(tmp_path).save(io.BytesIO(b"x"), suffix)


def test_delete_is_idempotent(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    stored = storage.save(io.BytesIO(b"data"), ".kml")

    storage.delete(stored.key)
    storage.delete(stored.key)

    assert not storage.local_path(stored.key).exists()
