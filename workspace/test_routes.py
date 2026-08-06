import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_amor_route_status(client):
    response = client.get("/amor")
    assert response.status_code == 200


def test_amor_route_content_type(client):
    response = client.get("/amor")
    assert response.mimetype == "text/html"


def test_amor_route_contains_expected_text(client):
    response = client.get("/amor")
    data = response.get_data(as_text=True)
    assert "❤ Amor ❤" in data
    assert "<title>Amor</title>" in data
