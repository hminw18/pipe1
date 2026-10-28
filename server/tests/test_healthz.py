from fastapi.testclient import TestClient

from pipe1_server.main import app


def test_healthz() -> None:
    response = TestClient(app).get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
