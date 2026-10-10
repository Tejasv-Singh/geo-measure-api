# geo-measure-api

Geospatial file measurement API. Full documentation is added in a later phase.

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
