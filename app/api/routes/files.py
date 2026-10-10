import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import ReadLimitsDep, SessionDep, SessionFactoryDep, StorageDep
from app.core.logging import request_id_var
from app.models import FileStatus, GeoFile, MeasurementKind, MeasurementStatus
from app.models.enums import GeometryType
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT, ErrorResponse
from app.schemas.features import FeatureCollection, GeoJSONFeature
from app.schemas.files import FileDetail, FileList, FileSummary, LayerInfo, UploadAccepted
from app.schemas.measurements import (
    SUMMARY_RULE,
    MeasurementPage,
    MeasurementRow,
    MeasurementSummary,
)
from app.services import files, results
from app.services.pipeline import run_file_job

router = APIRouter(
    prefix="/api/files",
    tags=["files"],
    # Our handler returns the shared error shape, not FastAPI's HTTPValidationError.
    responses={422: {"model": ErrorResponse}},
)

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Page size.")]
Offset = Annotated[int, Query(ge=0, description="Rows to skip.")]


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse} for code in codes}


def json_errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Errors for a route whose success media type isn't JSON.

    FastAPI documents a response "model" under the route's own media type, so these reference
    the shared schema under application/json explicitly.
    """
    content = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
    return {code: {"description": "Error", "content": content} for code in codes}


class GeoJSONResponse(JSONResponse):
    media_type = "application/geo+json"


@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadAccepted,
    responses=errors(413, 415),
    summary="Upload a zipped shapefile, KML or KMZ file",
)
def upload_file(
    response: Response,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    storage: StorageDep,
    limits: ReadLimitsDep,
    file: Annotated[UploadFile, File(description="A .zip with a shapefile, a .kml or a .kmz.")],
    source_crs: Annotated[
        str | None,
        Form(
            description="CRS for layers without one (a shapefile with no .prj), "
            "for example EPSG:32643. A layer's own CRS always wins."
        ),
    ] = None,
) -> UploadAccepted:
    """Store the file and start processing in the background. Poll GET /api/files/{id}/."""
    geo_file = files.accept_upload(session, storage, file.filename, file.file, source_crs, limits)
    background_tasks.add_task(
        run_file_job, geo_file.id, session_factory, storage, limits, request_id_var.get()
    )
    response.headers["Location"] = f"/api/files/{geo_file.id}/"
    return UploadAccepted(id=geo_file.id, status=geo_file.status)


@router.get("/", response_model=FileList, summary="List files, newest first")
def list_files(
    session: SessionDep,
    status_filter: Annotated[FileStatus | None, Query(alias="status")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> FileList:
    items, total = files.list_files(session, status_filter, limit, offset)
    return FileList(
        items=[file_summary(geo_file) for geo_file in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{file_id}/",
    response_model=FileDetail,
    responses=errors(404),
    summary="Get a file's status and metadata",
)
def get_file(file_id: uuid.UUID, session: SessionDep) -> FileDetail:
    return file_detail(files.get_file(session, file_id))


@router.delete(
    "/{file_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=errors(404),
    summary="Delete a file, its results and the stored upload",
)
def delete_file(file_id: uuid.UUID, session: SessionDep, storage: StorageDep) -> Response:
    files.delete_file(session, storage, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementPage,
    responses=errors(404, 409),
    summary="Per-feature measurements with a summary",
    description="Rows are ordered by feature_index. 409 while the file is PENDING or "
    f"PROCESSING (not_ready) or when it FAILED (file_failed). {SUMMARY_RULE}",
)
def get_measurements(
    file_id: uuid.UUID,
    session: SessionDep,
    geometry_type: GeometryType | None = None,
    status_filter: Annotated[MeasurementStatus | None, Query(alias="status")] = None,
    kind: MeasurementKind | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MeasurementPage:
    results.require_completed(files.get_file(session, file_id))
    filters = results.MeasurementFilters(geometry_type, status_filter, kind)
    found = results.measurement_results(session, file_id, filters, limit, offset)
    summary = found.summary
    return MeasurementPage(
        items=[MeasurementRow.model_validate(row) for row in found.rows],
        total=found.total,
        limit=limit,
        offset=offset,
        summary=MeasurementSummary(
            total_area_m2=summary.total_area_m2,
            total_length_m=summary.total_length_m,
            count_by_status=summary.count_by_status,
        ),
    )


@router.get(
    "/{file_id}/features/",
    response_model=FeatureCollection,
    response_class=GeoJSONResponse,
    responses=json_errors(404, 409, 422),
    summary="Features as a GeoJSON FeatureCollection in EPSG:4326",
)
def get_features(
    file_id: uuid.UUID,
    session: SessionDep,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> FeatureCollection:
    results.require_completed(files.get_file(session, file_id))
    found = results.feature_results(session, file_id, limit, offset)
    return FeatureCollection(
        features=[
            GeoJSONFeature(
                id=row["feature_index"],
                layer=row["layer"],
                geometry=row["geometry"],
                properties=row["properties"],
            )
            for row in found.rows
        ],
        total=found.total,
        limit=limit,
        offset=offset,
    )


def file_summary(geo_file: GeoFile) -> FileSummary:
    return FileSummary(
        id=geo_file.id,
        filename=geo_file.filename,
        status=geo_file.status,
        feature_count=geo_file.feature_count,
        crs=geo_file.source_crs,
        created_at=geo_file.created_at,
    )


def file_detail(geo_file: GeoFile) -> FileDetail:
    return FileDetail(
        id=geo_file.id,
        filename=geo_file.filename,
        feature_count=geo_file.feature_count,
        crs=geo_file.source_crs,
        status=geo_file.status,
        error_code=geo_file.error_code,
        error_message=geo_file.error_message,
        error_details=geo_file.error_details,
        layers=[
            LayerInfo(name=layer.name, crs=layer.crs_label, feature_count=layer.feature_count)
            for layer in geo_file.layers
        ],
        processing_ms=geo_file.processing_ms,
        created_at=geo_file.created_at,
        processed_at=geo_file.processed_at,
    )
