import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FileStatus
from app.schemas.common import Page, UTCDateTime

FILE_ID_EXAMPLE = "3f2b8c1e-5d4a-4b6f-9a7e-2c1d0e9f8a7b"


class UploadAccepted(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": FILE_ID_EXAMPLE, "status": "PENDING"}]}
    )

    id: uuid.UUID
    status: FileStatus


class LayerInfo(BaseModel):
    name: str = Field(examples=["parcels"])
    crs: str = Field(
        description="Authority code such as EPSG:32643, or the CRS name when none matches.",
        examples=["EPSG:32643"],
    )
    feature_count: int = Field(examples=[42])


class FileSummary(BaseModel):
    id: uuid.UUID
    filename: str
    status: FileStatus
    feature_count: int
    crs: str | None = Field(
        description="The file's CRS, MIXED when layers differ, null until processed."
    )
    created_at: UTCDateTime


class FileDetail(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": FILE_ID_EXAMPLE,
                    "filename": "parcels.zip",
                    "feature_count": 42,
                    "crs": "EPSG:32643",
                    "status": "COMPLETED",
                    "error_code": None,
                    "error_message": None,
                    "error_details": None,
                    "layers": [{"name": "parcels", "crs": "EPSG:32643", "feature_count": 42}],
                    "processing_ms": 184,
                    "created_at": "2026-10-09T08:15:02.120000Z",
                    "processed_at": "2026-10-09T08:15:02.310000Z",
                },
                {
                    "id": FILE_ID_EXAMPLE,
                    "filename": "roads.zip",
                    "feature_count": 0,
                    "crs": None,
                    "status": "FAILED",
                    "error_code": "missing_crs",
                    "error_message": "Layer 'roads' has no CRS because the shapefile has no "
                    ".prj file. Send a source_crs form field, for example EPSG:32643.",
                    "error_details": {"layer": "roads"},
                    "layers": [],
                    "processing_ms": 35,
                    "created_at": "2026-10-09T08:16:40.004000Z",
                    "processed_at": "2026-10-09T08:16:40.039000Z",
                },
            ]
        }
    )

    id: uuid.UUID
    filename: str
    feature_count: int
    crs: str | None = Field(
        description="The file's CRS, MIXED when layers differ, null until processed."
    )
    status: FileStatus
    error_code: str | None
    error_message: str | None
    error_details: Any
    layers: list[LayerInfo]
    processing_ms: int | None
    created_at: UTCDateTime
    processed_at: UTCDateTime | None


class FileList(Page):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": FILE_ID_EXAMPLE,
                            "filename": "parcels.zip",
                            "status": "COMPLETED",
                            "feature_count": 42,
                            "crs": "EPSG:32643",
                            "created_at": "2026-10-09T08:15:02.120000Z",
                        }
                    ],
                    "total": 1,
                    "limit": 100,
                    "offset": 0,
                }
            ]
        }
    )

    items: list[FileSummary]
