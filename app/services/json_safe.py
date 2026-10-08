import base64
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


def to_json_safe(value: Any) -> JSONValue:
    """Convert values coming out of pandas/GDAL into plain JSON-compatible Python types."""
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | np.ndarray):
        return [to_json_safe(item) for item in value]
    if _is_missing(value):
        return None
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    elif isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return to_json_safe(float(value))
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
