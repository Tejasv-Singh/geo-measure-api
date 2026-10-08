from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: int = Field(description="The feature_index, unique within the file.")
    layer: str = Field(description="Foreign member: the layer the feature came from.")
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in EPSG:4326.")
    properties: dict[str, Any] = Field(description="Attributes from the source file.")


class FeatureCollection(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": 0,
                            "layer": "parcels",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.0]]
                                ],
                            },
                            "properties": {"owner": "Asha", "plot_no": "42"},
                        }
                    ],
                    "total": 1,
                    "limit": 100,
                    "offset": 0,
                }
            ]
        }
    )

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature]
    total: int
    limit: int
    offset: int
