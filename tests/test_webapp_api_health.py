from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs" in data


def test_docs_page():
    response = client.get("/docs")
    assert response.status_code == 200


def test_static_files():
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert "SimpleQuant" in response.text
