import logging
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import files as files_route
from app.core.config import Settings
from app.core.logging import clean_request_id, request_context, request_id_var
from app.main import create_app
from app.services.pipeline import run_file_job
from app.storage import LocalStorage
from tests.test_pipeline import LIMIT, upload

GENERATED = re.compile(r"[0-9a-f]{32}")


def records_for(caplog: pytest.LogCaptureFixture, logger: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == logger]


def test_generated_when_missing(client: TestClient) -> None:
    response = client.get("/health")

    assert GENERATED.fullmatch(response.headers["x-request-id"])


def test_safe_incoming_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "lb-7f3a.1_2-x"})

    assert response.headers["x-request-id"] == "lb-7f3a.1_2-x"


@pytest.mark.parametrize("value", ["a" * 129, "has space", "<script>", "line\tbreak", "é".encode()])
def test_unsafe_incoming_id_is_replaced(client: TestClient, value: str | bytes) -> None:
    response = client.get("/health", headers={"X-Request-ID": value})

    assert GENERATED.fullmatch(response.headers["x-request-id"])


def test_longest_allowed_id_is_kept() -> None:
    assert clean_request_id("a" * 128) == "a" * 128


def test_request_log_line_carries_the_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.requests"):
        client.get("/health", headers={"X-Request-ID": "trace-1"})

    (record,) = records_for(caplog, "app.requests")
    assert record.request_id == "trace-1"  # type: ignore[attr-defined]
    assert record.getMessage().startswith("GET /health 200 ")


def test_upload_and_background_processing_share_the_id(
    client: TestClient, fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.INFO),
        (fixtures_dir / "golden_square.kml").open("rb") as handle,
    ):
        response = client.post(
            "/api/files/",
            files={"file": ("golden_square.kml", handle)},
            headers={"X-Request-ID": "upload-42"},
        )

    assert response.headers["x-request-id"] == "upload-42"
    (processed,) = [
        r for r in records_for(caplog, "app.services.pipeline") if "Processed file" in r.msg
    ]
    assert processed.request_id == "upload-42"  # type: ignore[attr-defined]


def test_error_responses_carry_the_id(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_bytes": 10})
    app = create_app(small)

    def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        server_error = client.get("/boom", headers={"X-Request-ID": "err-500"})
        not_found = client.get("/nope", headers={"X-Request-ID": "err-404"})
        too_large = client.post(
            "/api/files/",
            content=bytes(200_000),
            headers={"X-Request-ID": "err-413", "content-type": "multipart/form-data; b=x"},
        )

    assert (server_error.status_code, server_error.headers["x-request-id"]) == (500, "err-500")
    assert (not_found.status_code, not_found.headers["x-request-id"]) == (404, "err-404")
    assert (too_large.status_code, too_large.headers["x-request-id"]) == (413, "err-413")


# app.main builds an app at import, which installs the record factory for these tests.
def test_log_records_outside_a_request_have_a_placeholder(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="test.outside"):
        logging.getLogger("test.outside").info("hello")

    (record,) = records_for(caplog, "test.outside")
    assert record.request_id == "-"  # type: ignore[attr-defined]


def test_request_context_sets_and_restores() -> None:
    with request_context(None) as generated:
        assert GENERATED.fullmatch(generated)
        assert request_id_var.get() == generated
    with request_context("job-1") as kept:
        assert kept == "job-1"
    assert request_id_var.get() is None


def test_background_job_logs_under_the_id_it_is_given(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    file_id = upload(session_factory, storage, fixtures_dir / "golden_square.kml")

    with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
        run_file_job(file_id, session_factory, storage, LIMIT, request_id="job-7")

    records = records_for(caplog, "app.services.pipeline")
    assert records
    assert {r.request_id for r in records} == {"job-7"}  # type: ignore[attr-defined]
    assert request_id_var.get() is None


def test_request_duration_excludes_background_work(
    client: TestClient,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def slow_job(*_: object) -> None:
        time.sleep(0.5)

    monkeypatch.setattr(files_route, "run_file_job", slow_job)

    with (
        caplog.at_level(logging.INFO, logger="app.requests"),
        (fixtures_dir / "golden_square.kml").open("rb") as handle,
    ):
        started = time.perf_counter()
        response = client.post("/api/files/", files={"file": ("golden_square.kml", handle)})
        total = time.perf_counter() - started

    assert response.status_code == 202
    assert total >= 0.5, "the background job should have run inside the request call"
    (record,) = records_for(caplog, "app.requests")
    duration_ms = float(re.search(r" (\d+)ms$", record.getMessage()).group(1))  # type: ignore[union-attr]
    assert duration_ms < 500


def test_request_is_logged_once_when_the_app_fails(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)

    def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    with (
        caplog.at_level(logging.INFO, logger="app.requests"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.get("/boom")

    (record,) = records_for(caplog, "app.requests")
    assert record.getMessage().startswith("GET /boom 500 ")
