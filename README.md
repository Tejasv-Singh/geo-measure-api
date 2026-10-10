# geo-measure-api

[![CI](https://github.com/Tejasv-Singh/geo-measure-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Tejasv-Singh/geo-measure-api/actions/workflows/ci.yml)

An HTTP API that accepts a zipped Shapefile, a KML or a KMZ file and returns the area of every
polygon and the length of every line in square metres and metres. It reads each layer with GDAL,
resolves the source CRS from the `.prj` file (or from a `source_crs` form field, never by
guessing), normalizes to EPSG:4326 and measures each feature in a projected CRS chosen for it:
Lambert Azimuthal Equal-Area for areas, UTM for lengths. Every value is cross-checked against the
geodesic value on the WGS84 ellipsoid. Processing runs in the background; the client polls for the
result.

## Setup

### Docker Compose

```bash
docker compose up --build
```

This starts the API on port 8000 with Postgres 16. Migrations run when the API starts. Open
<http://localhost:8000/docs> for the interactive API documentation.

### Local, with a virtual environment and SQLite

Python 3.12 is required. `requirements.lock` holds the dependency versions the tests pass with;
pass it to pip as a constraints file.

bash (Linux, macOS):

```bash
git clone https://github.com/Tejasv-Singh/geo-measure-api.git
cd geo-measure-api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -c requirements.lock -e ".[dev]"
uvicorn app.main:app --reload
```

Windows PowerShell:

```powershell
git clone https://github.com/Tejasv-Singh/geo-measure-api.git
cd geo-measure-api
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -c requirements.lock -e ".[dev]"
uvicorn app.main:app --reload
```

If PowerShell refuses to run `Activate.ps1`, allow local scripts for your user first:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The API listens on <http://localhost:8000>. With the defaults it writes `data/app.db` and stores
uploads in `data/uploads/`; both are created on startup. GDAL and PROJ come bundled in the pyogrio
and pyproj wheels, so no system GIS packages are needed.

### Tests

On SQLite, each test gets a fresh database file:

```bash
pytest
```

On Postgres, set `DATABASE_URL` to a server where the user may create databases. Migrations run
once into a template database, and each test gets its own copy, dropped afterwards. Existing
databases are not touched.

```bash
pip install -c requirements.lock -e ".[dev,postgres]"
DATABASE_URL=postgresql+psycopg://geo:geo@localhost:5432/geo pytest
```

In PowerShell, set the variable with `$env:DATABASE_URL = "postgresql+psycopg://..."` and then run
`pytest`. CI runs the suite on both databases, plus lint (`ruff`), type checks (`mypy`) and a
Docker smoke test (`scripts/smoke_test.sh`) that uploads `samples/golden_square.kml` to the
composed stack.

### Configuration

Settings come from environment variables or a `.env` file (see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `geo-measure-api` | Title shown in the API docs. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy URL. Use `postgresql+psycopg://user:password@host:5432/db` for Postgres (install the `postgres` extra). |
| `STORAGE_DIR` | `./data/uploads` | Where uploaded files are stored. |
| `MAX_UPLOAD_BYTES` | `52428800` (50 MiB) | Largest accepted upload. |
| `MAX_UNCOMPRESSED_BYTES` | `524288000` (500 MiB) | Largest total uncompressed size of a `.zip` or `.kmz`. |
| `MAX_FEATURES` | `250000` | Most features accepted in one file, across all layers. |
| `LOG_LEVEL` | `INFO` | Python logging level. |

## API

The examples use the files in `samples/` and a server on `localhost:8000`. In Windows PowerShell
5.1, `curl` is an alias for `Invoke-WebRequest`; type `curl.exe` instead. Responses are real
output from the running server, trimmed where marked.

| Method and path | Purpose |
| --- | --- |
| `POST /api/files/` | Upload a file. Returns 202 and starts processing. |
| `GET /api/files/` | List files, newest first. |
| `GET /api/files/{id}/` | Status and metadata of one file. |
| `GET /api/files/{id}/measurements/` | Per-feature measurements with a summary. |
| `GET /api/files/{id}/features/` | Features as GeoJSON in EPSG:4326. |
| `DELETE /api/files/{id}/` | Delete a file, its results and the stored upload. |
| `GET /health` | Liveness check. |

### Upload, then poll

```bash
curl -i -F "file=@samples/survey_bengaluru.kml" http://localhost:8000/api/files/
```

```text
HTTP/1.1 202 Accepted
location: /api/files/c9bbd08c-1e29-4475-91fa-5a4defefdf90/
x-request-id: 686ba3136c5b41e9bb6fd8c9b387f15d
(other headers trimmed)

{"id":"c9bbd08c-1e29-4475-91fa-5a4defefdf90","status":"PENDING"}
```

The file is validated (extension, content, archive structure, size) and stored before the 202;
reading and measuring happen afterwards. Poll the `Location` until `status` is `COMPLETED` or
`FAILED`:

```bash
ID=c9bbd08c-1e29-4475-91fa-5a4defefdf90
curl http://localhost:8000/api/files/$ID/
```

```json
{
  "id": "c9bbd08c-1e29-4475-91fa-5a4defefdf90",
  "filename": "survey_bengaluru.kml",
  "feature_count": 5,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_code": null,
  "error_message": null,
  "error_details": null,
  "layers": [
    {"name": "Plots", "crs": "EPSG:4326", "feature_count": 2},
    {"name": "Infrastructure", "crs": "EPSG:4326", "feature_count": 3}
  ],
  "processing_ms": 147,
  "created_at": "2026-10-10T05:49:10.790806Z",
  "processed_at": "2026-10-10T05:49:10.967575Z"
}
```

`crs` is the file's CRS, `MIXED` when its layers differ, and `null` until it is processed.

### Measurements

```bash
curl "http://localhost:8000/api/files/$ID/measurements/?limit=3"
```

```json
{
  "total": 6,
  "limit": 3,
  "offset": 0,
  "items": [
    {
      "feature_index": 0,
      "layer": "Plots",
      "geometry_type": "Polygon",
      "kind": "AREA",
      "value": 7561.934155485395,
      "unit": "m2",
      "status": "OK",
      "projected_crs": "+proj=laea +lat_0=12.5 +lon_0=77.5 +datum=WGS84 +units=m +no_defs",
      "method": "laea",
      "note": null,
      "geodesic_value": 7561.934155583382,
      "deviation_pct": -1.2957826485775664e-09
    },
    "... feature 1, a polygon with a hole, trimmed ...",
    {
      "feature_index": 2,
      "layer": "Infrastructure",
      "geometry_type": "LineString",
      "kind": "LENGTH",
      "value": 434.38182768077763,
      "unit": "m",
      "status": "OK",
      "projected_crs": "EPSG:32643",
      "method": "utm",
      "note": null,
      "geodesic_value": 434.1305964208838,
      "deviation_pct": 0.057869973221202874
    }
  ],
  "summary": {
    "total_area_m2": 15061.452921861188,
    "total_length_m": 743.9728806258356,
    "count_by_status": {
      "OK": 5,
      "NOT_APPLICABLE": 1,
      "UNSUPPORTED": 0,
      "INVALID_GEOMETRY": 0,
      "EMPTY": 0
    }
  }
}
```

Rows are ordered by `feature_index`. Query parameters:

- `geometry_type`: `Point`, `MultiPoint`, `LineString`, `MultiLineString`, `LinearRing`,
  `Polygon`, `MultiPolygon` or `GeometryCollection`.
- `status`: `OK`, `NOT_APPLICABLE`, `UNSUPPORTED`, `INVALID_GEOMETRY` or `EMPTY`.
- `kind`: `AREA`, `LENGTH` or `NONE`.
- `limit` (1 to 1000, default 100) and `offset` (default 0).

The summary covers every row matching the filters, ignoring `limit` and `offset`. Its totals add
up the values of `OK` and `INVALID_GEOMETRY` rows (the latter measured after `make_valid`).

A GeometryCollection gets one row per kind it contains. The survey's pump house is a polygon, a
line and a point:

```bash
curl "http://localhost:8000/api/files/$ID/measurements/?geometry_type=GeometryCollection"
```

```json
{
  "total": 2,
  "items": [
    {
      "feature_index": 4,
      "kind": "AREA",
      "value": 57.61458292307708,
      "status": "OK",
      "note": "Merged the polygon parts of a GeometryCollection into 1 polygon(s). Ignored 1 point part(s)."
    },
    {
      "feature_index": 4,
      "kind": "LENGTH",
      "value": 309.59105294505804,
      "status": "OK",
      "note": "Merged the line parts of a GeometryCollection into 1 line(s). Ignored 1 point part(s)."
    }
  ],
  "summary": {"total_area_m2": 57.61458292307708, "total_length_m": 309.59105294505804, "...": "trimmed"}
}
```

### Features as GeoJSON

```bash
curl "http://localhost:8000/api/files/$ID/features/?limit=1"
```

The response has `Content-Type: application/geo+json`:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": 0,
      "layer": "Plots",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.591, 12.971], [77.5919, 12.971], [77.5919, 12.9717], [77.591, 12.9717], [77.591, 12.971]]]
      },
      "properties": {
        "id": "plot-101",
        "Name": "Plot 101",
        "description": null,
        "survey_no": "SY-101",
        "land_use": "residential",
        "surveyor": "Field team A"
      }
    }
  ],
  "total": 5,
  "limit": 1,
  "offset": 0
}
```

`id` is the feature index, `layer` is a top-level member, and `properties` holds only the
attributes from the source file.

### A shapefile without a .prj

```bash
curl -F "file=@samples/parcels_no_prj.zip" http://localhost:8000/api/files/
# {"id":"e65feb52-d0e3-4a57-98e7-fb184c088e99","status":"PENDING"}
curl http://localhost:8000/api/files/e65feb52-d0e3-4a57-98e7-fb184c088e99/
```

```json
{
  "id": "e65feb52-d0e3-4a57-98e7-fb184c088e99",
  "filename": "parcels_no_prj.zip",
  "feature_count": 0,
  "crs": null,
  "status": "FAILED",
  "error_code": "missing_crs",
  "error_message": "Layer 'parcels' has no CRS because the shapefile has no .prj file. Send a source_crs form field, for example EPSG:32643.",
  "error_details": {"layer": "parcels"},
  "layers": [],
  "...": "trimmed"
}
```

Results of a failed file return 409:

```bash
curl http://localhost:8000/api/files/e65feb52-d0e3-4a57-98e7-fb184c088e99/measurements/
```

```json
{
  "error": {
    "code": "file_failed",
    "message": "The file could not be processed.",
    "details": {
      "error_code": "missing_crs",
      "error_message": "Layer 'parcels' has no CRS because the shapefile has no .prj file. Send a source_crs form field, for example EPSG:32643."
    }
  }
}
```

Upload it again with `source_crs`. It accepts anything pyproj parses: an `EPSG:` code, WKT or a
PROJ string. It applies only to layers with no CRS of their own.

```bash
curl -F "file=@samples/parcels_no_prj.zip" -F "source_crs=EPSG:32643" http://localhost:8000/api/files/
# {"id":"169d31e9-9518-418d-ab50-453482cf14fa","status":"PENDING"}
curl http://localhost:8000/api/files/169d31e9-9518-418d-ab50-453482cf14fa/
```

```json
{
  "id": "169d31e9-9518-418d-ab50-453482cf14fa",
  "filename": "parcels_no_prj.zip",
  "feature_count": 3,
  "crs": "EPSG:32643",
  "status": "COMPLETED",
  "layers": [{"name": "parcels", "crs": "EPSG:32643", "feature_count": 3}],
  "...": "trimmed"
}
```

### List and delete

```bash
curl "http://localhost:8000/api/files/?status=FAILED"
```

```json
{
  "total": 1,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "id": "e65feb52-d0e3-4a57-98e7-fb184c088e99",
      "filename": "parcels_no_prj.zip",
      "status": "FAILED",
      "feature_count": 0,
      "crs": null,
      "created_at": "2026-10-10T05:49:26.259971Z"
    }
  ]
}
```

`status` filters by `PENDING`, `PROCESSING`, `COMPLETED` or `FAILED`; `limit` and `offset` work as
for measurements.

```bash
curl -i -X DELETE http://localhost:8000/api/files/e65feb52-d0e3-4a57-98e7-fb184c088e99/
# HTTP/1.1 204 No Content
```

### Rejected uploads

These fail at upload time, before anything is stored:

```bash
curl -F "file=@README.md" http://localhost:8000/api/files/
# HTTP 415: {"error":{"code":"unsupported_file","message":"Unsupported file extension '.md'.","details":{"supported":[".kml",".kmz",".zip"]}}}

curl -F "file=@samples/survey_bengaluru.kml;filename=survey.zip" http://localhost:8000/api/files/
# HTTP 422: {"error":{"code":"invalid_file","message":"The file content does not match its .zip extension.","details":null}}

curl -F "file=@samples/parcels_no_prj.zip" -F "source_crs=EPSG:999999" http://localhost:8000/api/files/
# HTTP 422: {"error":{"code":"invalid_crs","message":"'EPSG:999999' is not a valid CRS.","details":null}}
```

## Error codes

Every error response has the same shape:

```json
{"error": {"code": "file_failed", "message": "The file could not be processed.", "details": {}}}
```

`code` is stable and meant for programs; `message` is for people and may change. Every response,
errors included, carries an `X-Request-ID` header that also appears on the server's log lines for
that request and for the background processing it started.

### Request errors

| Code | Status | Returned by | When |
| --- | --- | --- | --- |
| `validation_error` | 422 | all routes | A path, query or form parameter is invalid: a malformed file id, an unknown filter value, `limit` outside 1 to 1000, or no `file` field. `details` lists the problems. |
| `bad_request` | 400 | `POST /api/files/` | The multipart body cannot be parsed. |
| `unsupported_file` | 415 | `POST /api/files/` | The extension is not `.zip`, `.kml` or `.kmz`. `details.supported` lists them. |
| `invalid_file` | 422 | `POST /api/files/` | The upload has no filename, its content does not match its extension, or the archive is unusable: unsafe entry paths, encryption, more than `MAX_UNCOMPRESSED_BYTES` uncompressed, no `.shp`, a `.shp` without its `.shx` or `.dbf`, or a `.kmz` without a `.kml`. |
| `invalid_crs` | 422 | `POST /api/files/` | `source_crs` cannot be parsed, or is neither geographic nor projected. |
| `file_too_large` | 413 | `POST /api/files/` | The upload is larger than `MAX_UPLOAD_BYTES`. |
| `not_found` | 404 | `GET` and `DELETE /api/files/{id}/`, `GET .../measurements/`, `GET .../features/`, unknown paths | No file has that id, or no route matches. |
| `method_not_allowed` | 405 | all routes | The method is not supported. The `Allow` header lists the ones that are. |
| `not_ready` | 409 | `GET .../measurements/`, `GET .../features/` | The file is still `PENDING` or `PROCESSING`. |
| `file_failed` | 409 | `GET .../measurements/`, `GET .../features/` | The file is `FAILED`. `details` repeats its `error_code` and `error_message`. |
| `http_error` | other 4xx | all routes | Any other HTTP error raised by the framework. |
| `internal_error` | 500 | all routes | An unexpected server error. The traceback is logged under the request ID. |

### Processing errors

A file that cannot be processed ends `FAILED`. `GET /api/files/{id}/` then reports `error_code`,
`error_message` and `error_details`.

| `error_code` | When |
| --- | --- |
| `invalid_file` | GDAL cannot read the data, or a `.prj` file exists but cannot be parsed. |
| `missing_crs` | A layer has no CRS (a shapefile without `.prj`) and no `source_crs` was sent. `details.layer` names it. |
| `invalid_crs` | A layer's CRS, or `source_crs`, is neither geographic nor projected. |
| `too_many_features` | The file has more than `MAX_FEATURES` features. `details` has `feature_count` and `limit`. |
| `internal_error` | An unexpected error during processing. The traceback is logged. |
| `interrupted` | The server restarted while the file was waiting or being processed. |

## Architecture

### Layout

- `app/main.py`: app factory, lifespan (engine, migrations, storage, restart recovery), middleware and routers.
- `app/api/routes/files.py`: the `/api/files/` routes; thin, no geospatial logic.
- `app/api/routes/health.py`: `GET /health`.
- `app/api/deps.py`: dependencies for the session, storage, settings and read limits.
- `app/api/middleware.py`: request IDs and the upload size limit.
- `app/core/config.py`: settings from the environment.
- `app/core/errors.py`: error classes, the shared error shape and exception handlers.
- `app/core/logging.py`: log format and the request ID context.
- `app/db.py`: engine creation (SQLite pragmas, parent directory) and migrations.
- `app/models/`: SQLAlchemy models for files, layers, features and measurements, and their enums.
- `app/schemas/`: Pydantic response models with the examples shown in `/docs`.
- `app/storage.py`: the `Storage` interface and `LocalStorage`.
- `app/services/files.py`: upload validation and file listing, lookup and deletion.
- `app/services/results.py`: measurement and feature queries, filters and the summary.
- `app/services/pipeline.py`: `process_file` (read, resolve CRS, measure) and the background job around it.
- `app/services/readers/base.py`: `BaseReader`, feature records, the feature cap and GDAL error handling.
- `app/services/readers/shapefile_zip.py`: zipped shapefiles, read through `/vsizip/`.
- `app/services/readers/kml.py`: KML and KMZ, every layer, LIBKML column cleanup.
- `app/services/readers/zip_safety.py`: archive checks without extraction.
- `app/services/readers/registry.py`: picks a reader by file extension.
- `app/services/crs.py`: CRS parsing, resolution, labels and the file-level summary.
- `app/services/projection.py`: the `ProjectionStrategy` interface, UTM/UPS and local equal-area.
- `app/services/measurement.py`: per-feature area and length, repair and the geodesic cross-check.
- `app/services/json_safe.py`: converts pandas and NumPy values into JSON types.
- `migrations/`: Alembic migrations.
- `scripts/make_samples.py`: writes `samples/`.
- `scripts/benchmark.py`: the timing figures in the Accuracy section.
- `scripts/smoke_test.sh`: uploads the golden square to a running API and checks the area.
- `tests/fixtures/generate.py`: builds the test fixtures in code, so no binary test files are committed.

### Processing flow

```mermaid
flowchart TD
    A["POST /api/files/"] --> B["Size limit middleware: Content-Length, then byte count"]
    B --> C["Upload checks: extension, magic bytes, zip structure, source_crs"]
    C -->|"413, 415 or 422"| X["Error response, nothing stored"]
    C --> D["Storage.save: chunked copy, size cap, SHA-256"]
    D --> E[("geo_files row, PENDING")]
    E --> F["202 Accepted with Location"]
    E --> G["Background job: run_file_job"]
    G --> H{"Claim: PENDING to PROCESSING"}
    H -->|"already claimed"| Z["Stop"]
    H --> I["Reader: list layers, feature cap, read with GDAL"]
    I --> J["CRS resolution: .prj or KML WGS84, else source_crs, else fail"]
    J --> K["Measurement: to EPSG:4326, make_valid, LAEA areas, UTM lengths, geodesic check"]
    K --> L[("layers, features, measurements: bulk insert")]
    L --> M[("geo_files: COMPLETED")]
    I -->|"error"| N[("geo_files: FAILED with error_code")]
    J -->|"error"| N
```

A file is `FAILED` only when it cannot be read as a whole. Features that cannot be measured get a
measurement status and the file still ends `COMPLETED`.

### Measurement flow

Each feature is routed by geometry type:

| Geometry | Result |
| --- | --- |
| `Polygon`, `MultiPolygon` | `AREA` in m2 |
| `LineString`, `MultiLineString`, `LinearRing` | `LENGTH` in m |
| `Point`, `MultiPoint` | `NONE`, status `NOT_APPLICABLE` |
| `GeometryCollection` | An `AREA` row for its polygonal parts and a `LENGTH` row for its linear parts; points are ignored and noted |
| null or empty | `NONE`, status `EMPTY` |
| any other type | `NONE`, status `UNSUPPORTED` |

Then:

1. Invalid geometries are repaired with `shapely.make_valid`. The row is flagged
   `INVALID_GEOMETRY`, the note gives GEOS's reason (for example `Self-intersection[77.05 28.05]`),
   and the value is measured on the repaired geometry. If no polygonal (or linear) part survives the
   repair, the value is null.
2. In a GeometryCollection, the polygonal parts are merged with `shapely.union_all` before
   measuring, and so are the linear parts, so overlapping parts are not counted twice.
3. The geometry is converted to EPSG:4326 and projected with the strategy for its kind (see CRS
   handling). Coordinates outside the longitude and latitude range after conversion, which usually
   means a wrong source CRS, give `UNSUPPORTED` with a note instead of a number.
4. The same quantity is computed on the WGS84 ellipsoid with `pyproj.Geod` and stored as
   `geodesic_value`, with `deviation_pct = (value - geodesic_value) / geodesic_value * 100`.
   Polygons are oriented before the geodesic area, because pyproj sums signed ring areas and an
   unoriented hole would be added instead of subtracted.

Z values are dropped when the file is read (`force_2d`), so every value is planimetric.

### CRS handling

- **Source CRS.** A shapefile layer uses its `.prj`. A `.prj` that GDAL cannot parse fails the
  file (`invalid_file`); it is not treated as missing. A layer without a `.prj` uses the
  `source_crs` form field if one was sent, and otherwise fails the file (`missing_crs`). Nothing is
  guessed from coordinate ranges. KML is always EPSG:4326, as the KML specification requires. The
  CRS is stored per layer; a file whose layers differ reports `MIXED`.
- **Normalization.** Every layer is converted to EPSG:4326 (longitude, latitude) first.
- **Areas** use Lambert Azimuthal Equal-Area. LAEA preserves area everywhere, so its centre only
  affects shape. The centre is the middle of the feature's 1 degree cell, so nearby features share
  one CRS and are projected in one vectorized call.
- **Lengths** use the UTM zone of the feature's representative point, grouped by EPSG code with one
  `to_crs` per group. Beyond the UTM limits (north of 84N, south of 80S) the polar UPS projections,
  EPSG:32661 and EPSG:32761, are used and the note says so.
- Every measurement records its `projected_crs` and `method` (`laea`, `utm` or `ups`).

## Accuracy

Numbers from this implementation. Each row names the test that reproduces it (in `tests/`), or
the script for timings, which are not asserted in tests.

| Quantity | Result | Reproduced by |
| --- | --- | --- |
| A 1000 m square built in EPSG:32643 at easting 680,000, uploaded as KML in EPSG:4326 | 999,995.73 m2 (relative error 4.3e-6) | `test_measurement.py::test_golden_square_from_kml_is_one_square_kilometre`, `test_samples.py::test_golden_square_sample` |
| The same square at the zone's central meridian, easting 500,000 | 1,000,800.47 m2 | `test_measurement.py::test_utm_grid_square_at_the_central_meridian_is_larger_on_the_ground` |
| UTM length against geodesic, 0.05 degree diagonal lines | +0.096% at the equator, +0.009% at 28N mid-zone, +0.062% at 28N near the zone edge, -0.036% at 60N, -0.033% at 45S, -0.039% at 83.9N | `test_measurement.py::test_utm_length_is_within_a_quarter_percent_of_geodesic` (asserts under 0.25%) |
| UPS length against geodesic at 86N | -0.49% | `test_measurement.py::test_polar_length_uses_ups_and_says_so` (asserts under 0.6%) |
| LAEA area against geodesic, at the same seven locations | at most 1e-5% (largest: -9.5e-6%) | `test_measurement.py::test_equal_area_matches_geodesic_area` (asserts under 0.001%) |
| Web Mercator (EPSG:3857) area of a 0.01 degree square at Bengaluru, the rejected option | +5.95% | `test_measurement.py::test_web_mercator_overstates_area_at_bengaluru` |
| Projecting 2,000 polygons over 3 x 3 degrees with LAEA | 8.23 s with one CRS per feature, 0.07 s with snapped centres (9 CRS objects) | `scripts/benchmark.py`; the CRS count by `test_projection.py::test_layer_over_three_by_three_degrees_builds_at_most_nine_crs` |
| Processing a 60,000-polygon zipped shapefile on SQLite: read, measure, store | 28.3 s | `scripts/benchmark.py` |

**Why easting 680,000.** UTM shrinks distances by 0.9996 on the central meridian, and the scale
factor grows to 1 about 180 km either side of it. A square that is 1000 m on the UTM grid at the
central meridian is therefore about 1,000,800 m2 on the ground. The golden test places its square
where the scale factor is close to 1, so that 1,000,000 m2 is the correct answer, and the second
row shows the same square at the central meridian.

Timings were taken on a 16-thread Intel laptop running Windows 11 and Python 3.12. In the 60,000
feature run, most of the time goes to the per-feature geodesic calculation and the bulk inserts.

## Design Decisions

This section is a draft.

**FastAPI.** Chosen for typed request parsing, Pydantic response models and generated OpenAPI
documentation. Alternatives: Django REST Framework, which brings an ORM, admin and auth that this
service does not need; Flask, which would need extra libraries for validation and docs. Trade-off:
FastAPI is async-first, but most of the work here is CPU-bound GDAL and GEOS calls, so the routes
are plain functions run in a thread pool.

**BackgroundTasks instead of a job queue.** Processing runs in-process after the response is sent.
Alternatives: Celery or arq with Redis, or a database-backed queue. Trade-off: no extra service to
run, but jobs are lost if the process stops. A restart marks unfinished files `FAILED` with
`interrupted`, and the job function takes only a file id, so it can move to a queue unchanged.

**SQLite by default, Postgres in Docker.** SQLite needs no setup for local runs and tests; Postgres
is what a deployment would use. Alternative: Postgres only, which is closer to production but makes
local setup heavier. Trade-off: SQL must stay portable, so both run in CI. SQLite runs in WAL mode
because the background thread writes while requests read.

**A projection chosen per feature.** Areas use a local equal-area projection, lengths the
feature's UTM zone. Alternatives: one projection per file (wrong for files spanning zones), Web
Mercator (area off by about 6% at Bengaluru and much more at higher latitudes), or purely geodesic
measurement (no projection at all, but less familiar to GIS users and harder to cross-check).
Trade-off: more CRS handling code, kept fast by grouping features by CRS. Geodesic values are also
stored, so the projection error is visible per row.

**Flat measurement rows.** One row per measurement, with the feature index, layer and geometry type
repeated. Alternative: measurements nested under features. Trade-off: some repetition, but simple
filtering, pagination and SQL aggregates, and a GeometryCollection with both an area and a length
fits without special cases.

**409 for unfinished or failed files.** The results endpoints return 409 with `not_ready` or
`file_failed`. Alternatives: 200 with an empty list, which hides the state, or 202, which suggests
the request itself was accepted for later. Trade-off: clients must handle 409, but cannot mistake a
pending file for one with no features.

**Upload-time and background checks.** Cheap checks (extension, magic bytes, zip central directory,
size, `source_crs`) run before the file is stored and return 4xx at once. Everything that needs
GDAL runs in the background. Alternative: validate everything in the request, which makes uploads
slow, or nothing, which stores files that can never be processed. Trade-off: some errors arrive as
an HTTP status and others as a `FAILED` file, so both are documented.

**Geometry stored as WGS84 GeoJSON.** Features are stored as GeoJSON in EPSG:4326 in a JSON column,
not in the source CRS and not as a spatial type. Alternative: PostGIS geometry columns, which allow
spatial queries but tie the app to Postgres. Trade-off: no spatial indexing, but the features
endpoint serves stored JSON directly and both databases work.

**Idempotent jobs.** A job claims its file with a conditional update from `PENDING` to
`PROCESSING` and does nothing if the claim fails. Alternative: rely on each job running exactly
once, which no queue guarantees. Trade-off: one extra query per job, and a repeated delivery can
no longer duplicate rows or fail a finished file.

## Known limitations

- **Antimeridian.** A geometry that crosses 180 degrees longitude is stored in EPSG:4326 with
  coordinates near both -180 and 180 and is measured as if it spanned the globe.
- **One process.** Restart recovery marks every unfinished file `FAILED` when the app starts. With
  several worker processes, a starting worker would also fail files another worker is processing.
- **BackgroundTasks is not durable.** A job in progress is lost if the process stops; the file is
  marked `interrupted` on the next start and must be uploaded again.
- **UPS near the poles.** Lengths north of 84N or south of 80S are measured in UPS, which is 0.3% to
  0.6% off at those latitudes. The note says so; `geodesic_value` is the better figure there.
- **Planimetric values.** Z values are ignored, so areas and lengths are horizontal projections,
  not surface areas or slope lengths.
- **Swapped coordinates.** A file with latitude and longitude swapped but valid-looking values
  cannot be detected and is measured in the wrong place. Only values outside the valid range are
  caught.
- **source_crs does not override a .prj.** A wrong `.prj` has to be fixed in the file.
- **The layer member in GeoJSON.** `layer` is a top-level member of each feature. GeoJSON allows
  this, but most GIS tools only read `properties` and will not show it.

## Learnings

TODO: written by the author

## Future scope

- Presigned S3 uploads, so large files go straight to object storage instead of through the API.
- A durable job queue with retries.
- Chunked reading for large files, to bound memory use.
- PostGIS storage and spatial queries.
- Surface area and volume from a DSM.
- Vector tiles of the processed features.
- Authentication and multi-tenancy.
