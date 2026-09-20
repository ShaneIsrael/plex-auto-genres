"""Authentication: one shared password, a signed session cookie, bearer for scripts.

There are no accounts. The password comes from the environment; a session is a
stateless HMAC token whose key is derived from that password *and* a random
secret persisted in the database, so:

* sessions survive a restart (the secret persists);
* changing the password invalidates every session (the key changes);
* the database alone cannot forge a token (the password is part of the key).

Everything under ``/api/`` is denied by default by :class:`AuthMiddleware`;
the login and status endpoints are the only public ones. Static assets and the
SPA shell are public: the bundle holds no secrets, and the app shows a login
screen when the status endpoint says so.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from ..errors import ConfigError
from ..store import Store

log = logging.getLogger(__name__)

COOKIE = "pag_session"
SECRET_KEY = "auth_secret_v1"
PUBLIC_PATHS = frozenset({"/api/v1/auth/status", "/api/v1/auth/login", "/api/v1/auth/logout"})
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """How the server authenticates. Read from the environment in production."""

    password: str | None = None
    insecure: bool = False
    session_days: int = 30
    #: ``None`` sets the cookie's Secure flag only when the request came over https.
    secure_cookie: bool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    @classmethod
    def from_env(cls) -> "AuthSettings":
        secure = os.getenv("PAG_WEB_SECURE_COOKIE")
        return cls(
            password=os.getenv("PAG_WEB_PASSWORD") or None,
            insecure=os.getenv("PAG_WEB_INSECURE", "").lower() in ("1", "true", "yes"),
            session_days=int(os.getenv("PAG_WEB_SESSION_DAYS", "30")),
            secure_cookie=None if secure is None else secure.lower() in ("1", "true", "yes"),
        )


def check_bind(settings: AuthSettings, host: str) -> None:
    """Refuse to expose an unauthenticated API beyond the loopback interface."""
    if settings.enabled or settings.insecure or host in LOOPBACK:
        return
    raise ConfigError(
        f"Refusing to listen on {host} without authentication: the API can run jobs, "
        "rewrite your config and holds your Plex connection.\n"
        "  Set PAG_WEB_PASSWORD to enable the login screen,\n"
        "  or PAG_WEB_INSECURE=1 if this host is genuinely private (you have been told)."
    )


def verify_password(settings: AuthSettings, candidate: str) -> bool:
    """Constant-time comparison; never short-circuits on length."""
    if not settings.password:
        return False
    return hmac.compare_digest(settings.password.encode("utf-8"), candidate.encode("utf-8"))


def persistent_secret(store: Store) -> bytes:
    """A random per-installation secret, generated once and kept in the store."""
    existing = store.kv_get(SECRET_KEY)
    if existing:
        return bytes.fromhex(existing)
    fresh = secrets.token_bytes(32)
    store.kv_set(SECRET_KEY, fresh.hex())
    return fresh


class SessionSigner:
    """Issues and checks ``v1.<expiry>.<hmac>`` tokens."""

    def __init__(self, password: str, secret: bytes) -> None:
        # Slow derivation so a leaked token gives nothing to brute-force quickly.
        self._key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), secret, 200_000)

    def issue(self, ttl_s: float) -> str:
        payload = f"v1.{int(time.time() + ttl_s)}"
        return f"{payload}.{self._sign(payload)}"

    def verify(self, token: str | None) -> bool:
        """True for a well-formed, correctly signed, unexpired token."""
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdigit():
            return False
        payload = f"{parts[0]}.{parts[1]}"
        if not hmac.compare_digest(self._sign(payload), parts[2]):
            return False
        return int(parts[1]) > time.time()

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode("utf-8"), "sha256").hexdigest()


class LoginLimiter:
    """At most ``limit`` failed logins per client per ``window`` seconds."""

    def __init__(self, limit: int = 5, window_s: float = 60.0) -> None:
        self._limit = limit
        self._window = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, client: str, now: float) -> deque[float]:
        bucket = self._failures[client]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        return bucket

    def blocked(self, client: str) -> bool:
        return len(self._prune(client, time.time())) >= self._limit

    def record_failure(self, client: str) -> None:
        self._prune(client, time.time()).append(time.time())

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)


@dataclass(slots=True)
class AuthRuntime:
    """What the middleware and routes need at request time."""

    settings: AuthSettings
    signer: SessionSigner | None
    limiter: LoginLimiter

    @classmethod
    def build(cls, settings: AuthSettings, store: Store) -> "AuthRuntime":
        signer = (
            SessionSigner(settings.password, persistent_secret(store))
            if settings.password
            else None
        )
        return cls(settings=settings, signer=signer, limiter=LoginLimiter())

    def authenticated(self, request: StarletteRequest) -> bool:
        """Session cookie or bearer password; always true when auth is off."""
        if not self.settings.enabled:
            return True
        assert self.signer is not None
        if self.signer.verify(request.cookies.get(COOKIE)):
            return True
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value:
            return verify_password(self.settings, value.strip())
        return False


def _client_of(request: StarletteRequest) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _cross_site(request: StarletteRequest) -> bool:
    """Reject state changes that a browser flags as coming from another site.

    SameSite=Lax already keeps the cookie off cross-site POSTs in current
    browsers; this is the second lock on the same door.
    """
    return request.headers.get("sec-fetch-site", "").lower() == "cross-site"


class AuthMiddleware:
    """Pure ASGI: no response buffering, so server-sent events stream through."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        runtime: AuthRuntime | None = getattr(scope["app"].state, "auth", None)
        if runtime is None or not runtime.settings.enabled or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        request = StarletteRequest(scope)
        if not runtime.authenticated(request):
            response = JSONResponse(
                status_code=401,
                content={"title": "Unauthorized", "status": 401,
                         "detail": "Sign in, or send the password as a Bearer token."},
                headers={"WWW-Authenticate": 'Bearer realm="plex-auto-genres"'},
            )
            await response(scope, receive, send)
            return

        if scope["method"] in UNSAFE_METHODS and _cross_site(request):
            response = JSONResponse(
                status_code=403,
                content={"title": "Forbidden", "status": 403,
                         "detail": "Cross-site requests cannot change anything here."},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# -- routes ------------------------------------------------------------------


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    """The one credential there is."""

    password: str = Field(min_length=1, max_length=1024)


class AuthStatus(BaseModel):
    """Whether a login is required, and whether this client has one."""

    enabled: bool
    authenticated: bool
    insecure: bool


def _runtime(request: Request) -> AuthRuntime:
    return request.app.state.auth


def _cookie_secure(request: Request, settings: AuthSettings) -> bool:
    if settings.secure_cookie is not None:
        return settings.secure_cookie
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.lower() == "https"


@router.get("/status", response_model=AuthStatus)
async def status(request: Request) -> AuthStatus:
    """Public: tells the UI whether to show the login screen."""
    runtime = _runtime(request)
    return AuthStatus(
        enabled=runtime.settings.enabled,
        authenticated=runtime.authenticated(request),
        insecure=not runtime.settings.enabled and runtime.settings.insecure,
    )


@router.post("/login", response_model=AuthStatus)
async def login(request: Request, response: Response, body: LoginBody) -> AuthStatus:
    """Exchange the password for a session cookie."""
    runtime = _runtime(request)
    if not runtime.settings.enabled:
        return AuthStatus(enabled=False, authenticated=True, insecure=runtime.settings.insecure)

    client = _client_of(request)
    if runtime.limiter.blocked(client):
        raise HTTPException(
            status_code=429, detail="Too many failed attempts. Try again in a minute."
        )
    if not verify_password(runtime.settings, body.password):
        runtime.limiter.record_failure(client)
        log.warning("Failed login from %s", client)
        raise HTTPException(status_code=401, detail="Wrong password.")

    runtime.limiter.reset(client)
    assert runtime.signer is not None
    ttl = runtime.settings.session_days * 86400
    response.set_cookie(
        COOKIE,
        runtime.signer.issue(ttl),
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, runtime.settings),
        path="/",
    )
    log.info("Login from %s", client)
    return AuthStatus(enabled=True, authenticated=True, insecure=False)


@router.post("/logout", response_model=AuthStatus)
async def logout(request: Request, response: Response) -> AuthStatus:
    """Drop the session cookie. The token itself simply expires."""
    runtime = _runtime(request)
    response.delete_cookie(COOKIE, path="/")
    return AuthStatus(
        enabled=runtime.settings.enabled,
        authenticated=not runtime.settings.enabled,
        insecure=not runtime.settings.enabled and runtime.settings.insecure,
    )
