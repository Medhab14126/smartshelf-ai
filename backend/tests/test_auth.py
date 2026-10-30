from tests.conftest import register


def test_register_login_me_logout(client):
    headers = register(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["business"]["name"] == "Corner Cup Cafe"
    assert me.json()["business"]["timezone"] == "Asia/Kolkata"

    login = client.post("/api/auth/login", json={"email": "OWNER@example.com", "password": "secret123"})
    assert login.status_code == 200
    token_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.post("/api/auth/logout", headers=token_headers).status_code == 204
    # The token is revoked server-side.
    assert client.get("/api/auth/me", headers=token_headers).status_code == 401
    # Other sessions stay valid.
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_password_is_hashed(client, db):
    from app.models.account import User

    register(client)
    user = db.query(User).one()
    assert user.password_hash != "secret123" and user.password_hash.startswith("$argon2")


def test_login_rejects_wrong_password_and_unknown_user(client):
    register(client)
    assert (
        client.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope12345"}).status_code == 401
    )
    assert (
        client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "secret123"}).status_code == 401
    )


def test_duplicate_email_and_weak_password(client):
    register(client)
    dup = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "secret123", "full_name": "X", "business_name": "Y"},
    )
    assert dup.status_code == 409
    weak = client.post(
        "/api/auth/register",
        json={"email": "a@example.org", "password": "password", "full_name": "X", "business_name": "Y"},
    )
    assert weak.status_code == 422


def test_protected_routes_require_auth(client):
    assert client.get("/api/inventory/batches").status_code == 401
    assert client.get("/api/dashboard/summary", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_cookie_session_requires_csrf_header_for_writes(client):
    register(client)  # sets the HttpOnly session cookie on the client
    assert client.get("/api/auth/me").status_code == 200
    payload = {"name": "Sugar"}
    assert client.post("/api/products", json=payload).status_code == 403
    assert client.post("/api/products", json=payload, headers={"X-Requested-With": "fetch"}).status_code == 201


def test_login_rate_limited(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_rate_limit_attempts", 3)
    register(client)
    codes = [
        client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-pass1"}).status_code
        for _ in range(5)
    ]
    assert codes[-1] == 429
