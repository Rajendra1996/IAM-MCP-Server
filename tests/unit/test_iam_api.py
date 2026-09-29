import pytest
from fastapi.testclient import TestClient

from mock_services.iam_api import app

client = TestClient(app)


@pytest.mark.parametrize("username", ["alice", "john", "bob"])
def test_user_lookup(username):
    response = client.get(f"/users/{username}")
    assert response.status_code == 200
    assert response.json()["username"] == username
    assert response.json()["active"] is True


@pytest.mark.parametrize("path", ["/users/notfound", "/applications/notfound"])
def test_missing_lookup(path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("name,risk", [("sap", "High"), ("salesforce", "Medium"), ("servicenow", "Low")])
def test_application_lookup(name, risk):
    response = client.get(f"/applications/{name}")
    assert response.status_code == 200
    assert response.json()["risk_level"] == risk


def test_health_and_case_normalization():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/users/ALICE").json()["display_name"] == "Alice Brown"
    assert client.get("/applications/SAP").json()["owner"] == "ERP Team"
