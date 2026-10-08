from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.services.json_safe import to_json_safe


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (float("nan"), None),
        (float("inf"), None),
        (np.float64("nan"), None),
        (pd.NaT, None),
        (np.datetime64("NaT", "ns"), None),
        (np.int64(7), 7),
        (np.float32(1.5), 1.5),
        (np.bool_(True), True),
        (pd.Timestamp("2024-03-01 10:30"), "2024-03-01T10:30:00"),
        (np.datetime64("2024-03-01"), "2024-03-01T00:00:00"),
        (datetime(2024, 3, 1, 8), "2024-03-01T08:00:00"),
        (date(2024, 3, 1), "2024-03-01"),
        (Decimal("2.5"), 2.5),
        (b"\x00\x01", "AAE="),
        ("text", "text"),
        (None, None),
    ],
)
def test_scalars(value: object, expected: object) -> None:
    assert to_json_safe(value) == expected


def test_nested_containers() -> None:
    value = {"a": [np.int64(1), float("nan")], 2: {"b": np.array([1.0, np.nan])}}

    assert to_json_safe(value) == {"a": [1, None], "2": {"b": [1.0, None]}}
