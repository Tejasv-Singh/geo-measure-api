import pytest

from app.services.readers.zip_safety import is_junk, is_unsafe_name


@pytest.mark.parametrize(
    "name",
    ["../evil.shp", "a/../../evil.shp", "/etc/passwd", "\\windows\\x.shp", "C:/x.shp", "a\\..\\b"],
)
def test_unsafe_names(name: str) -> None:
    assert is_unsafe_name(name)


@pytest.mark.parametrize("name", ["a.shp", "data/roads/a.shp", "a..b.shp"])
def test_safe_names(name: str) -> None:
    assert not is_unsafe_name(name)


@pytest.mark.parametrize("name", ["__MACOSX/a/._x.shp", "data/._x.shp", "data/.DS_Store"])
def test_junk_names(name: str) -> None:
    assert is_junk(name)
