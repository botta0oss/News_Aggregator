from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from backend.auth import service
from backend.auth.deps import COOKIE_NAME
from backend.auth.passwords import hash_password, validate_password, verify_password
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import User, UserSession
from tests.conftest import TEST_PASSWORD, api_client, ensure_user, login_client


def test_password_hashing_and_policy():
    h = hash_password(TEST_PASSWORD)
    assert h.startswith("$argon2id$") and TEST_PASSWORD not in h
    assert verify_password(h, TEST_PASSWORD)
    assert not verify_password(h, TEST_PASSWORD + "x")
    assert not verify_password("not-a-hash", TEST_PASSWORD)
    for bad in ("short", "x" * 300, "alice-password-123"):
        with pytest.raises(ValueError):
            validate_password(bad, "alice")


async def test_protected_endpoints_require_login(db):
    async with api_client() as client:
        for path in ("/articles", "/markets", "/status", "/categories", "/predictions/opportunities", "/auth/me"):
            assert (await client.get(path)).status_code == 401, path
        assert (await client.post("/ingest")).status_code == 401
        # Static dashboard and login stay public
        assert (await client.get("/")).status_code == 200


async def test_login_sets_hardened_cookie_and_session(db):
    await ensure_user("admin", "admin")
    async with api_client() as client:
        r = await client.post("/auth/login", json={"username": "  ADMIN ", "password": TEST_PASSWORD})
        assert r.status_code == 200
        assert r.json()["user"] == {"username": "admin", "role": "admin"}
        cookie = r.headers["set-cookie"]
        assert f"{COOKIE_NAME}=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Secure" not in cookie  # plain-HTTP localhost in "auto" mode
        token = client.cookies[COOKIE_NAME]
        me = await client.get("/auth/me")
        assert me.status_code == 200 and me.json()["csrf_token"] == r.json()["csrf_token"]
        assert me.headers["cache-control"] == "no-store"
    async with SessionLocal() as s:
        stored = (await s.execute(select(UserSession.token_hash))).scalars().all()
        assert stored == [service.hash_token(token)]  # raw token never stored


async def test_cookie_is_secure_outside_localhost(db):
    import httpx
    from backend.main import app
    await ensure_user("admin", "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://news.example.com") as client:
        r = await client.post("/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
        assert "Secure" in r.headers["set-cookie"]
        assert "max-age=31536000" in r.headers["strict-transport-security"]


async def test_wrong_credentials_same_error(db):
    await ensure_user("admin", "admin")
    async with api_client() as client:
        a = await client.post("/auth/login", json={"username": "admin", "password": "wrong-password-123"})
        b = await client.post("/auth/login", json={"username": "ghost", "password": "wrong-password-123"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json()
        assert COOKIE_NAME not in client.cookies


async def test_login_rate_limit(db, monkeypatch):
    await ensure_user("admin", "admin")
    async with api_client() as client:
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            assert (await client.post("/auth/login", json={"username": "admin", "password": "wrong-password-123"})).status_code == 401
        r = await client.post("/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


async def test_csrf_required_for_unsafe_methods(db):
    async with login_client("admin") as client:
        csrf = client.headers.pop("X-CSRF-Token")
        assert (await client.post("/markets/sync")).status_code == 403
        assert (await client.post("/markets/sync", headers={"X-CSRF-Token": "forged"})).status_code == 403
        assert (await client.get("/markets")).status_code == 200  # safe methods need no token
        r = await client.post("/markets/does-not-exist/predict", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 404  # passed auth + CSRF


async def test_viewer_cannot_start_paid_actions(db):
    async with login_client("viewer") as client:
        assert (await client.get("/markets")).status_code == 200
        for path in ("/ingest", "/markets/sync", "/markets/x/predict"):
            r = await client.post(path)
            assert r.status_code == 403, path
            assert "amministratore" in r.json()["detail"]


async def test_logout_revokes_session(db):
    async with login_client("admin") as client:
        token = client.cookies[COOKIE_NAME]
        assert (await client.post("/auth/logout")).status_code == 204
        assert (await client.get("/auth/me")).status_code == 401
    async with api_client() as other:  # a stolen copy of the cookie is dead too
        other.cookies.set(COOKIE_NAME, token)
        assert (await other.get("/auth/me")).status_code == 401


async def test_password_change_ends_other_sessions(db):
    async with login_client("admin") as first, login_client("admin") as second:
        bad = await first.post("/auth/password", json={"current_password": "nope-nope-nope", "new_password": "a-brand-new-password"})
        assert bad.status_code == 400
        weak = await first.post("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"})
        assert weak.status_code == 422
        ok = await first.post("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"})
        assert ok.status_code == 204
        assert (await first.get("/auth/me")).status_code == 200    # current session kept
        assert (await second.get("/auth/me")).status_code == 401   # other sessions ended
    async with api_client() as client:
        assert (await client.post("/auth/login", json={"username": "admin", "password": TEST_PASSWORD})).status_code == 401
        assert (await client.post("/auth/login", json={"username": "admin", "password": "a-brand-new-password"})).status_code == 200


async def test_expired_idle_and_disabled_sessions_rejected(db):
    async with login_client("admin") as client:
        async with SessionLocal() as s:
            session = (await s.execute(select(UserSession))).scalar_one()
            session.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=settings.SESSION_IDLE_MINUTES + 1)
            await s.commit()
        assert (await client.get("/auth/me")).status_code == 401

    async with login_client("admin") as client:
        async with SessionLocal() as s:
            user = await service.get_user(s, "admin")
            user.is_active = False
            await s.commit()
        assert (await client.get("/auth/me")).status_code == 401
        async with api_client() as fresh:
            r = await fresh.post("/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
            assert r.status_code == 401


async def test_bootstrap_admin_only_when_no_users(db, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "Boss")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "bootstrap-password-1")
    async with SessionLocal() as s:
        await service.ensure_bootstrap_admin(s)
        await service.ensure_bootstrap_admin(s)  # idempotent
        users = (await s.execute(select(User))).scalars().all()
        assert [(u.username, u.role) for u in users] == [("boss", "admin")]
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "other")
    async with SessionLocal() as s:
        await service.ensure_bootstrap_admin(s)
        assert (await s.execute(select(func.count(User.id)))).scalar() == 1


async def test_security_headers(db):
    async with api_client() as client:
        r = await client.get("/")
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "script-src 'self'" in r.headers["content-security-policy"]


def test_client_ip_from_the_proxy_header(monkeypatch):
    """Behind Cloudflare Tunnel every request comes from the tunnel: the real client is in
    CF-Connecting-IP, used only when configured."""
    from starlette.requests import Request
    from backend.auth.deps import client_ip
    from backend.config import settings

    def request(headers):
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "client": ("172.18.0.5", 5000)})
    monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
    assert client_ip(request({"CF-Connecting-IP": "203.0.113.7"})) == "172.18.0.5"   # not trusted by default
    monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "CF-Connecting-IP")
    assert client_ip(request({"CF-Connecting-IP": "203.0.113.7"})) == "203.0.113.7"
    assert client_ip(request({})) == "172.18.0.5"
