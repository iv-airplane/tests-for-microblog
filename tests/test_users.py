from datetime import datetime
from urllib.parse import parse_qs, urlparse
import httpx
import pytest
from conftest import make_user_payload


def assert_link_matches(
    url: str,
    expected_path: str,
    expected_query: dict[str, str] | None = None,
) -> None:
    parsed = urlparse(url)
    assert parsed.path == expected_path
    if expected_query is not None:
        actual_query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        for key, value in expected_query.items():
            assert actual_query.get(key) == str(value)


def assert_user_schema(user: dict, expected_username: str | None = None) -> None:
    assert isinstance(user["id"], int)
    assert isinstance(user["username"], str)
    if expected_username is not None:
        assert user["username"] == expected_username
    assert "email" not in user
    assert "password" not in user
    assert "password_hash" not in user
    datetime.fromisoformat(user["last_seen"].replace("Z", "+00:00"))
    assert user["about_me"] is None or isinstance(user["about_me"], str)
    assert isinstance(user["post_count"], int)
    assert isinstance(user["follower_count"], int)
    assert isinstance(user["following_count"], int)

    links = user["_links"]
    user_id = user["id"]
    assert_link_matches(links["self"], f"/api/users/{user_id}")
    assert_link_matches(links["followers"], f"/api/users/{user_id}/followers")
    assert_link_matches(links["following"], f"/api/users/{user_id}/following")
    assert isinstance(links["avatar"], str) and links["avatar"]


def assert_error_response(resp: httpx.Response, expected_status: int) -> None:
    assert resp.status_code == expected_status
    body = resp.json()
    assert isinstance(body, dict)
    assert "error" in body


def find_user_in_list(
    client: httpx.Client,
    headers: dict[str, str],
    user_id: int,
) -> dict | None:
    next_url: str | None = "/api/users?page=1&per_page=100"
    while next_url:
        resp = client.get(next_url, headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for item in data["items"]:
            if item["id"] == user_id:
                return item
        next_url = data["_links"]["next"]
    return None


class TestCreateUser:
    def test_create_user_success(self, client: httpx.Client, auth_headers):
        payload = make_user_payload(about_me="Hello microblog")
        resp = client.post("/api/users", json=payload)

        assert resp.status_code == 201
        user = resp.json()
        assert_user_schema(user, expected_username=payload["username"])
        assert user["about_me"] == "Hello microblog"
        assert user["post_count"] == 0
        assert user["follower_count"] == 0
        assert user["following_count"] == 0

        location = resp.headers.get("Location")
        assert location is not None
        assert_link_matches(location, f"/api/users/{user['id']}")

        headers = auth_headers(payload["username"], payload["password"])
        assert headers["Authorization"].startswith("Bearer ")

    def test_create_user_without_about_me(self, client: httpx.Client):
        payload = make_user_payload()
        resp = client.post("/api/users", json=payload)

        assert resp.status_code == 201
        user = resp.json()
        assert_user_schema(user, expected_username=payload["username"])
        assert user["about_me"] is None

    @pytest.mark.parametrize("missing_field", ["username", "email", "password"])
    def test_create_user_missing_required_field(
        self,
        client: httpx.Client,
        missing_field: str,
    ):
        payload = make_user_payload()
        payload.pop(missing_field)
        resp = client.post("/api/users", json=payload)

        assert_error_response(resp, 400)

    def test_create_user_duplicate_username(self, client: httpx.Client, create_user):
        existing = create_user()
        payload = make_user_payload(username=existing["payload"]["username"])
        resp = client.post("/api/users", json=payload)

        assert_error_response(resp, 400)

    def test_create_user_duplicate_email(self, client: httpx.Client, create_user):
        existing = create_user()
        payload = make_user_payload(email=existing["payload"]["email"])
        resp = client.post("/api/users", json=payload)

        assert_error_response(resp, 400)


class TestListUsers:
    def test_list_users_requires_auth(self, client: httpx.Client):
        resp = client.get("/api/users")
        assert_error_response(resp, 401)

    def test_list_users_rejects_invalid_token(self, client: httpx.Client):
        resp = client.get(
            "/api/users",
            headers={"Authorization": "Bearer invalid-token-value"},
        )
        assert_error_response(resp, 401)

    def test_list_users_default_collection_structure(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user()
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )

        resp = client.get("/api/users", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert isinstance(data["items"], list)
        assert 1 <= len(data["items"]) <= 10
        for item in data["items"]:
            assert_user_schema(item)

        meta = data["_meta"]
        assert meta["page"] == 1
        assert meta["per_page"] == 10
        assert meta["total_items"] >= 1
        assert meta["total_pages"] >= 1

        links = data["_links"]
        assert_link_matches(
            links["self"],
            "/api/users",
            {"page": "1", "per_page": "10"},
        )
        assert links["prev"] is None

    def test_list_users_pagination_and_links(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created_users = [create_user() for _ in range(3)]
        headers = auth_headers(
            created_users[0]["payload"]["username"],
            created_users[0]["payload"]["password"],
        )

        page1_resp = client.get("/api/users?page=1&per_page=2", headers=headers)
        assert page1_resp.status_code == 200
        page1 = page1_resp.json()

        assert len(page1["items"]) == 2
        assert page1["_meta"]["page"] == 1
        assert page1["_meta"]["per_page"] == 2
        assert page1["_meta"]["total_items"] >= 3
        assert page1["_meta"]["total_pages"] >= 2
        assert page1["_links"]["prev"] is None
        assert page1["_links"]["next"] is not None
        assert_link_matches(
            page1["_links"]["next"],
            "/api/users",
            {"page": "2", "per_page": "2"},
        )

        page2_resp = client.get(page1["_links"]["next"], headers=headers)
        assert page2_resp.status_code == 200
        page2 = page2_resp.json()

        assert page2["_meta"]["page"] == 2
        assert page2["_meta"]["per_page"] == 2
        assert page2["_links"]["prev"] is not None
        assert_link_matches(
            page2["_links"]["prev"],
            "/api/users",
            {"page": "1", "per_page": "2"},
        )

        page1_ids = {item["id"] for item in page1["items"]}
        page2_ids = {item["id"] for item in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

        all_ids: set[int] = set()
        next_url: str | None = "/api/users?page=1&per_page=100"
        reported_total = None
        while next_url:
            resp = client.get(next_url, headers=headers)
            assert resp.status_code == 200
            body = resp.json()
            reported_total = body["_meta"]["total_items"]
            all_ids.update(item["id"] for item in body["items"])
            next_url = body["_links"]["next"]

        assert len(all_ids) == reported_total
        for entry in created_users:
            assert entry["user"]["id"] in all_ids

    def test_list_users_per_page_capped_at_100(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user()
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )

        resp = client.get("/api/users?page=1&per_page=200", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["_meta"]["per_page"] == 100
        assert len(data["items"]) <= 100


class TestUpdateUser:
    def test_update_user_requires_auth(self, client: httpx.Client, create_user):
        created = create_user()
        user_id = created["user"]["id"]
        resp = client.put(f"/api/users/{user_id}", json={"about_me": "updated"})
        assert_error_response(resp, 401)

    def test_update_user_rejects_invalid_token(
        self,
        client: httpx.Client,
        create_user,
    ):
        created = create_user()
        user_id = created["user"]["id"]
        resp = client.put(
            f"/api/users/{user_id}",
            json={"about_me": "updated"},
            headers={"Authorization": "Bearer invalid-token-value"},
        )
        assert_error_response(resp, 401)

    def test_update_other_user_forbidden(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        user_a = create_user()
        user_b = create_user()
        headers_a = auth_headers(
            user_a["payload"]["username"],
            user_a["payload"]["password"],
        )

        resp = client.put(
            f"/api/users/{user_b['user']['id']}",
            json={"about_me": "hijacked"},
            headers=headers_a,
        )
        assert_error_response(resp, 403)

    def test_update_own_about_me(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user(about_me="initial bio")
        user_id = created["user"]["id"]
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )

        resp = client.put(
            f"/api/users/{user_id}",
            json={"about_me": "updated bio"},
            headers=headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert_user_schema(updated, expected_username=created["payload"]["username"])
        assert updated["about_me"] == "updated bio"

        listed = find_user_in_list(client, headers, user_id)
        assert listed is not None
        assert listed["about_me"] == "updated bio"

    def test_update_own_username(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user()
        user_id = created["user"]["id"]
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )
        new_username = make_user_payload(prefix="renamed")["username"]

        resp = client.put(
            f"/api/users/{user_id}",
            json={"username": new_username},
            headers=headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert_user_schema(updated, expected_username=new_username)

        new_headers = auth_headers(new_username, created["payload"]["password"])
        assert new_headers["Authorization"].startswith("Bearer ")

    def test_update_own_email_updates_avatar(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user()
        user_id = created["user"]["id"]
        old_avatar = created["user"]["_links"]["avatar"]
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )
        new_email = make_user_payload(prefix="email")["email"]

        resp = client.put(
            f"/api/users/{user_id}",
            json={"email": new_email},
            headers=headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert_user_schema(updated, expected_username=created["payload"]["username"])
        assert updated["_links"]["avatar"] != old_avatar

    def test_update_own_with_unchanged_username_and_email(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        created = create_user()
        user_id = created["user"]["id"]
        headers = auth_headers(
            created["payload"]["username"],
            created["payload"]["password"],
        )

        resp = client.put(
            f"/api/users/{user_id}",
            json={
                "username": created["payload"]["username"],
                "email": created["payload"]["email"],
                "about_me": "same identity",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["username"] == created["payload"]["username"]
        assert updated["about_me"] == "same identity"

    def test_update_user_duplicate_username_rejected(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        user_a = create_user()
        user_b = create_user()
        headers_a = auth_headers(
            user_a["payload"]["username"],
            user_a["payload"]["password"],
        )

        resp = client.put(
            f"/api/users/{user_a['user']['id']}",
            json={"username": user_b["payload"]["username"]},
            headers=headers_a,
        )
        assert_error_response(resp, 400)

    def test_update_user_duplicate_email_rejected(
        self,
        client: httpx.Client,
        create_user,
        auth_headers,
    ):
        user_a = create_user()
        user_b = create_user()
        headers_a = auth_headers(
            user_a["payload"]["username"],
            user_a["payload"]["password"],
        )

        resp = client.put(
            f"/api/users/{user_a['user']['id']}",
            json={"email": user_b["payload"]["email"]},
            headers=headers_a,
        )
        assert_error_response(resp, 400)
