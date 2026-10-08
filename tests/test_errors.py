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
    assert response.json()["error"]["code"] == "http_error"


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
