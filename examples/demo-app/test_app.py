from fastapi.testclient import TestClient

from app import User, app


def test_get_user():
    with TestClient(app) as client:
        assert client.get("/users/1").json() == {"id": 1, "email": "amal@example.com"}


def test_user_json():
    assert User(id=2, email="nimal@example.com").json() == '{"id":2,"email":"nimal@example.com"}'
