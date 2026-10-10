import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": None}
    }


def test_method_not_allowed_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unhandled_exception_returns_internal_error(settings: Settings) -> None:
    app = create_app(settings)

    def boom() -> None:
        raise RuntimeError("secret detail")

    app.add_api_route("/boom", boom)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error.", "details": None}
    }


@pytest.mark.parametrize(
    ("content_type", "body", "status", "code"),
    [
        ("multipart/form-data; boundary=xyz", b"garbage", 400, "bad_request"),
        ("multipart/form-data", b"x", 400, "bad_request"),
        ("application/json", b"{}", 422, "validation_error"),
    ],
)
def test_malformed_upload_requests_use_the_error_shape(
    client: TestClient, content_type: str, body: bytes, status: int, code: str
) -> None:
    response = client.post("/api/files/", content=body, headers={"content-type": content_type})

    assert response.status_code == status
    assert set(response.json()["error"]) == {"code", "message", "details"}
    assert response.json()["error"]["code"] == code
