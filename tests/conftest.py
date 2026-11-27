import os
import uuid
import httpx
import pytest


def make_user_payload(prefix: str = "user", **overrides) -> dict:
    suffix = uuid.uuid4().hex[:10]
    payload = {
        "username": f"{prefix}_{suffix}",
        "email": f"{prefix}_{suffix}@example.com",
        "password": f"Pass_{suffix}!",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return os.environ.get("API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


@pytest.fixture()
def client(api_base_url: str):
    with httpx.Client(base_url=api_base_url, timeout=10.0) as http_client:
        yield http_client


@pytest.fixture()
def create_user(client: httpx.Client):
    def _create_user(**overrides) -> dict:
        payload = make_user_payload(**overrides)
        resp = client.post("/api/users", json=payload)
        assert resp.status_code == 201, resp.text
        return {
            "payload": payload,
            "user": resp.json(),
            "response": resp,
        }

    return _create_user


@pytest.fixture()
def auth_headers(client: httpx.Client):
    def _auth_headers(username: str, password: str) -> dict[str, str]:
        resp = client.post("/api/tokens", auth=(username, password))
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return _auth_headers
