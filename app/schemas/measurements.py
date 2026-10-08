from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MeasurementKind, MeasurementStatus
from app.schemas.common import Page

SUMMARY_RULE = (
    "Totals cover every row matching the filters, ignoring limit and offset. They add up the "
    "values of OK and INVALID_GEOMETRY rows (the latter measured after make_valid)."
)


class MeasurementRow(BaseModel):
    feature_index: int = Field(examples=[0])
    layer: str = Field(examples=["parcels"])
    geometry_type: str | None = Field(examples=["Polygon"])
    kind: MeasurementKind
    value: float | None = Field(description="Square metres for AREA, metres for LENGTH.")
    unit: str | None = Field(examples=["m2"])
    status: MeasurementStatus
    projected_crs: str | None = Field(
        description="The CRS the value was measured in.",
        examples=["+proj=laea +lat_0=28.5 +lon_0=77.5 +datum=WGS84 +units=m +no_defs"],
    )
    method: str | None = Field(examples=["laea"])
    note: str | None
    geodesic_value: float | None = Field(description="The same quantity on the WGS84 ellipsoid.")
    deviation_pct: float | None = Field(description="(value - geodesic_value) / geodesic_value.")


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_length_m: float
    count_by_status: dict[MeasurementStatus, int]


class MeasurementPage(Page):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "feature_index": 0,
                            "layer": "parcels",
                            "geometry_type": "Polygon",
                            "kind": "AREA",
                            "value": 1000000.4,
                            "unit": "m2",
                            "status": "OK",
                            "projected_crs": "+proj=laea +lat_0=27.5 +lon_0=76.5 "
                            "+datum=WGS84 +units=m +no_defs",
                            "method": "laea",
                            "note": None,
                            "geodesic_value": 1000000.5,
                            "deviation_pct": -0.00001,
                        },
                        {
                            "feature_index": 1,
                            "layer": "wells",
                            "geometry_type": "Point",
                            "kind": "NONE",
                            "value": None,
                            "unit": None,
                            "status": "NOT_APPLICABLE",
                            "projected_crs": None,
                            "method": None,
                            "note": "Points have no area or length.",
                            "geodesic_value": None,
                            "deviation_pct": None,
                        },
                    ],
                    "total": 2,
                    "limit": 100,
                    "offset": 0,
                    "summary": {
                        "total_area_m2": 1000000.4,
                        "total_length_m": 0.0,
                        "count_by_status": {
                            "OK": 1,
                            "NOT_APPLICABLE": 1,
                            "UNSUPPORTED": 0,
                            "INVALID_GEOMETRY": 0,
                            "EMPTY": 0,
                        },
                    },
                }
            ]
        }
    )

    items: list[MeasurementRow]
    summary: MeasurementSummary = Field(description=SUMMARY_RULE)
