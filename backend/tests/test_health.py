from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok_with_utc_timestamp() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "okTeam API"
    parsed_ts = datetime.fromisoformat(body["timestamp_utc"])
    assert parsed_ts.tzinfo is not None
