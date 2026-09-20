"""Authentication: token mechanics, the middleware, login flow, bind check."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from plex_auto_genres.errors import ConfigError
from plex_auto_genres.server import create_app
from plex_auto_genres.server import state as state_module
from plex_auto_genres.server.auth import (
    COOKIE,
    AuthSettings,
    LoginLimiter,
    SessionSigner,
    check_bind,
    persistent_secret,
    verify_password,
)
from plex_auto_genres.store import Store

from .test_server import FakePlex

PASSWORD = "correct horse battery staple"


# -- primitives -------------------------------------------------------------------


def test_password_check_is_exact():
    settings = AuthSettings(password=PASSWORD)
    assert verify_password(settings, PASSWORD)
    assert not verify_password(settings, PASSWORD + " ")
    assert not verify_password(settings, "")
    assert not verify_password(AuthSettings(password=None), "anything")


def test_tokens_verify_expire_and_resist_tampering():
    signer = SessionSigner(PASSWORD, b"secret")
    token = signer.issue(60)
    assert signer.verify(token)
    assert not signer.verify(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not signer.verify("v1.notanumber.abc") and not signer.verify(None) and not signer.verify("")
    expired = signer.issue(-1)
    assert not signer.verify(expired)


def test_a_different_password_or_secret_invalidates_every_token():
    token = SessionSigner(PASSWORD, b"secret").issue(60)
    assert not SessionSigner("other", b"secret").verify(token)
    assert not SessionSigner(PASSWORD, b"other-secret").verify(token)


def test_secret_persists_in_the_store(store: Store):
    first = persistent_secret(store)
    assert len(first) == 32 and persistent_secret(store) == first


def test_login_limiter_blocks_after_five_failures_and_resets():
    limiter = LoginLimiter(limit=5, window_s=60)
    for _ in range(4):
        limiter.record_failure("1.2.3.4")
    assert not limiter.blocked("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")
    limiter.reset("1.2.3.4")
    assert not limiter.blocked("1.2.3.4")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_bind_refuses_a_public_interface_without_a_password(host):
    with pytest.raises(ConfigError, match="PAG_WEB_PASSWORD"):
        check_bind(AuthSettings(password=None), host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_bind_allows_loopback_without_a_password(host):
    check_bind(AuthSettings(password=None), host)


def test_bind_allows_public_interfaces_with_a_password_or_explicit_insecure():
    check_bind(AuthSettings(password=PASSWORD), "0.0.0.0")
    check_bind(AuthSettings(password=None, insecure=True), "0.0.0.0")


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PAG_WEB_PASSWORD", "pw")
    monkeypatch.setenv("PAG_WEB_INSECURE", "true")
    monkeypatch.setenv("PAG_WEB_SESSION_DAYS", "7")
    monkeypatch.setenv("PAG_WEB_SECURE_COOKIE", "1")
    s = AuthSettings.from_env()
    assert s.password == "pw" and s.insecure and s.session_days == 7 and s.secure_cookie is True
    monkeypatch.delenv("PAG_WEB_PASSWORD")
    monkeypatch.delenv("PAG_WEB_SECURE_COOKIE")
    s = AuthSettings.from_env()
    assert not s.enabled and s.secure_cookie is None


# -- the app ----------------------------------------------------------------------


@pytest.fixture
def locked(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<title>ui</title>")
    app = create_app(config_file, tmp_path / "state.db", static_dir=static,
                     auth=AuthSettings(password=PASSWORD))
    with TestClient(app) as c:
        yield c


def test_api_is_denied_without_a_session(locked):
    response = locked.get("/api/v1/libraries")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "Basic" not in response.headers["www-authenticate"]
    assert response.json()["status"] == 401


def test_status_and_the_spa_shell_stay_public(locked):
    assert locked.get("/api/v1/auth/status").json() == {
        "enabled": True, "authenticated": False, "insecure": False}
    assert locked.get("/").status_code == 200
    assert locked.get("/runs").status_code == 200


def test_login_sets_a_cookie_that_opens_the_api(locked):
    response = locked.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200 and response.json()["authenticated"] is True
    cookie = response.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Secure" not in cookie                       # plain http in the test client

    assert locked.get("/api/v1/libraries").status_code == 200
    assert locked.get("/api/v1/auth/status").json()["authenticated"] is True


def test_wrong_password_is_401_and_rate_limited(locked):
    for _ in range(5):
        assert locked.post("/api/v1/auth/login", json={"password": "nope"}).status_code == 401
    assert locked.post("/api/v1/auth/login", json={"password": "nope"}).status_code == 429
    # Even the right password waits out the window.
    assert locked.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 429


def test_logout_drops_the_session(locked):
    locked.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert locked.get("/api/v1/libraries").status_code == 200
    response = locked.post("/api/v1/auth/logout")
    assert response.json()["authenticated"] is False
    locked.cookies.clear()
    assert locked.get("/api/v1/libraries").status_code == 401


def test_bearer_password_works_for_scripts(locked):
    ok = locked.get("/api/v1/health", headers={"Authorization": f"Bearer {PASSWORD}"})
    assert ok.status_code == 200
    bad = locked.get("/api/v1/health", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    basic = locked.get("/api/v1/health", auth=("", PASSWORD))
    assert basic.status_code == 401, "Basic is deliberately not accepted"


def test_cross_site_state_changes_are_refused_even_when_authenticated(locked):
    locked.post("/api/v1/auth/login", json={"password": PASSWORD})
    response = locked.post("/api/v1/jobs", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert locked.get("/api/v1/jobs", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert locked.post("/api/v1/jobs", json={}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 202


def test_a_forged_cookie_is_rejected(locked):
    locked.cookies.set(COOKIE, f"v1.{int(time.time()) + 3600}.deadbeef")
    assert locked.get("/api/v1/libraries").status_code == 401


def test_sessions_survive_a_restart_but_not_a_password_change(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    db = tmp_path / "state.db"

    with TestClient(create_app(config_file, db, auth=AuthSettings(password=PASSWORD))) as c:
        c.post("/api/v1/auth/login", json={"password": PASSWORD})
        token = c.cookies.get(COOKIE)

    with TestClient(create_app(config_file, db, auth=AuthSettings(password=PASSWORD))) as c:
        c.cookies.set(COOKIE, token)
        assert c.get("/api/v1/libraries").status_code == 200   # same secret, same password

    with TestClient(create_app(config_file, db, auth=AuthSettings(password="rotated"))) as c:
        c.cookies.set(COOKIE, token)
        assert c.get("/api/v1/libraries").status_code == 401   # key changed with the password


@pytest.fixture
def open_client(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    app = create_app(config_file, tmp_path / "state.db", auth=AuthSettings(password=None))
    with TestClient(app) as c:
        yield c


def test_without_a_password_everything_is_open_and_status_says_so(open_client):
    assert open_client.get("/api/v1/auth/status").json() == {
        "enabled": False, "authenticated": True, "insecure": False}
    assert open_client.get("/api/v1/libraries").status_code == 200
    assert open_client.post("/api/v1/auth/login", json={"password": "x"}).json()["authenticated"] is True


def test_sse_streams_through_the_middleware(locked, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def quick(self, job):
        return None

    monkeypatch.setattr(JobManager, "_execute", quick)
    locked.post("/api/v1/auth/login", json={"password": PASSWORD})
    job = locked.post("/api/v1/libraries/Animes/run").json()
    with locked.stream("GET", f"/api/v1/jobs/{job['job_id']}/events") as stream:
        assert stream.status_code == 200
        seen = []
        for line in stream.iter_lines():
            if line.startswith("event: "):
                seen.append(line[7:])
                if line == "event: end":
                    break
    assert "snapshot" in seen and seen[-1] == "end"


# -- review regressions ---------------------------------------------------------


def test_a_hostile_cookie_is_a_401_not_a_500(locked):
    # httpx refuses to *send* non-ASCII header text, so hand it the raw bytes a
    # browser (or any other client on the same host) would put on the wire.
    raw = ("%s=v1.9999999999.\u00e7\u00e7" % COOKIE).encode("latin-1")
    assert locked.get("/api/v1/libraries", headers={b"cookie": raw}).status_code == 401
    assert locked.get("/api/v1/auth/status", headers={b"cookie": raw}).json()["authenticated"] is False


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy(locked):
    for i in range(5):
        locked.post("/api/v1/auth/login", json={"password": "nope"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"})
    blocked = locked.post("/api/v1/auth/login", json={"password": "nope"},
                          headers={"X-Forwarded-For": "10.0.0.99"})
    assert blocked.status_code == 429, "a forged address does not buy a fresh budget"


@pytest.fixture
def proxied(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    app = create_app(config_file, tmp_path / "state.db",
                     auth=AuthSettings(password=PASSWORD, trusted_proxies=frozenset({"testclient"})))
    with TestClient(app) as c:
        yield c


def test_a_trusted_proxy_s_forwarded_for_is_honoured(proxied):
    for i in range(5):
        assert proxied.post("/api/v1/auth/login", json={"password": "nope"},
                            headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 401
    # Behind a trusted proxy each reported client has its own budget.
    assert proxied.post("/api/v1/auth/login", json={"password": "nope"},
                        headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 401
    for _ in range(5):
        proxied.post("/api/v1/auth/login", json={"password": "nope"},
                     headers={"X-Forwarded-For": "10.0.0.99"})
    assert proxied.post("/api/v1/auth/login", json={"password": "nope"},
                        headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_login_limiter_has_a_global_budget_and_bounded_memory():
    limiter = LoginLimiter(limit=5, window_s=60, global_limit=6, max_clients=3)
    for i in range(6):
        limiter.record_failure(f"client-{i}")
    assert limiter.blocked("someone-new"), "the global budget is spent"
    assert len(limiter._failures) <= 3, "old buckets are evicted"  # pylint: disable=protected-access


def test_settings_read_trusted_proxies(monkeypatch):
    monkeypatch.setenv("PAG_WEB_TRUSTED_PROXIES", "10.0.0.1, 172.16.0.1")
    assert AuthSettings.from_env().trusted_proxies == frozenset({"10.0.0.1", "172.16.0.1"})
    monkeypatch.delenv("PAG_WEB_TRUSTED_PROXIES")
    assert AuthSettings.from_env().trusted_proxies == frozenset()
