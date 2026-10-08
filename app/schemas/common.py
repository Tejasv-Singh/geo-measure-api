from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every timestamp is stored in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]


class ErrorBody(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["File not found."])
    details: Any = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"error": {"code": "not_found", "message": "File not found.", "details": None}}
            ]
        }
    )

    error: ErrorBody


class Page(BaseModel):
    total: int = Field(description="Rows matching the filters, ignoring limit and offset.")
    limit: int
    offset: int
