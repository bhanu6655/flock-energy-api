import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert "legacy_portal_connected" in data

def test_auth_status():
    response = client.get("/api/v1/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert "authenticated" in data

def test_login():
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "operator@urja.local", "password": "urja-ops-2026"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

def test_list_meters():
    response = client.get("/api/v1/meters?page=1")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert data["total"] > 0
    assert len(data["data"]) > 0
    first = data["data"][0]
    assert "meter_id" in first
    assert "serial_number" in first
    assert "make" in first
    assert "phase_type" in first

def test_search_meters():
    response = client.get("/api/v1/meters?q=J100000")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any(m["meter_id"] == "J100000" for m in data["data"])

def test_get_meter_detail_legacy():
    response = client.get("/api/v1/meters/J100000")
    assert response.status_code == 200
    data = response.json()
    assert data["meter_id"] == "J100000"
    assert data["serial_number"] == "SE33962"
    assert data["make"] == "HPL"
    assert data["phase_type"] == "single"
    assert data["geo"]["latitude"] is not None
    assert data["geo"]["longitude"] is not None
    assert data["hierarchy"]["zone"] is not None or data["hierarchy"]["breadcrumbs"] is not None

def test_get_meter_detail_v2():
    response = client.get("/api/v1/meters/J100004")
    assert response.status_code == 200
    data = response.json()
    assert data["meter_id"] == "J100004"
    assert data["serial_number"] == "SE65293"
    assert data["make"] == "Genus"
    assert data["phase_type"] == "single"
    assert data["geo"]["latitude"] is not None

def test_get_meter_consumption():
    response = client.get("/api/v1/meters/J100000/consumption")
    assert response.status_code == 200
    data = response.json()
    assert data["meter_id"] == "J100000"
    assert data["total"] > 0
    assert len(data["readings"]) > 0
    reading = data["readings"][0]
    assert "timestamp" in reading
    assert isinstance(reading["kwh"], (float, int))
    assert isinstance(reading["kvah"], (float, int))
    assert isinstance(reading["voltage_r"], (float, int))
    assert data["summary"] is not None
    assert data["summary"]["latest_kwh"] is not None

def test_list_transformers():
    response = client.get("/api/v1/transformers?page=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert len(data["data"]) > 0
    dt = data["data"][0]
    assert "code" in dt
    assert "name" in dt
    assert "capacity_kva" in dt

def test_get_hierarchy():
    response = client.get("/api/v1/hierarchy")
    assert response.status_code == 200
    data = response.json()
    assert data["total_meters"] > 0
    assert data["zones_count"] > 0
    assert isinstance(data["tree"], dict)
