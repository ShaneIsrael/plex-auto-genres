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
from collections import deque
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from ..errors import ConfigError
from ..store import Store

log = logging.getLogger(__name__)

COOKIE = "pag_session"
SECRET_KEY = "auth_secret_v1"  # noqa: S105  # nosec B105 - a kv-table row name, not a credential
AUTH_PREFIX = "/api/v1/auth"
#: The routes that must work without a session, derived from the router
#: prefix so a version bump cannot silently lock the login endpoint.
PUBLIC_PATHS = frozenset(f"{AUTH_PREFIX}/{leaf}" for leaf in ("status", "login", "logout"))
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
    #: Peer addresses whose ``X-Forwarded-For`` is believed (a reverse proxy in
    #: front of this process). Anyone else's header is ignored: it is the
    #: easiest thing in the world to forge.
    trusted_proxies: frozenset[str] = frozenset()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    @classmethod
    def from_env(cls) -> "AuthSettings":
        secure = os.getenv("PAG_WEB_SECURE_COOKIE")
        proxies = os.getenv("PAG_WEB_TRUSTED_PROXIES", "")
        return cls(
            password=os.getenv("PAG_WEB_PASSWORD") or None,
            insecure=os.getenv("PAG_WEB_INSECURE", "").lower() in ("1", "true", "yes"),
            session_days=int(os.getenv("PAG_WEB_SESSION_DAYS", "30")),
            secure_cookie=None if secure is None else secure.lower() in ("1", "true", "yes"),
            trusted_proxies=frozenset(p.strip() for p in proxies.split(",") if p.strip()),
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
        try:
            return bytes.fromhex(existing)
        except ValueError:
            log.warning("Stored auth secret is corrupt; issuing a new one (sessions reset)")
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
        """True for a well-formed, correctly signed, unexpired token.

        The token is attacker-controlled input: nothing in here may raise.
        """
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdecimal():
            return False
        payload = f"{parts[0]}.{parts[1]}"
        # Compare bytes, not str: compare_digest raises TypeError on a str
        # holding non-ASCII characters, which a hostile cookie can carry.
        expected = self._sign(payload).encode("ascii")
        if not hmac.compare_digest(expected, parts[2].encode("utf-8")):
            return False
        try:
            return int(parts[1]) > time.time()
        except ValueError:
            return False

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode("utf-8"), "sha256").hexdigest()


class LoginLimiter:
    """At most ``limit`` failed logins per client per ``window`` seconds.

    A second, global budget covers all clients together, so the guard still
    holds when every attempt arrives under a different (forged) address.
    Buckets are pruned as they empty and capped in number, so the limiter's
    memory cannot grow without bound.
    """

    def __init__(
        self,
        limit: int = 5,
        window_s: float = 60.0,
        *,
        global_limit: int = 50,
        max_clients: int = 1000,
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._global_limit = global_limit
        self._max_clients = max_clients
        self._failures: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._all and now - self._all[0] > self._window:
            self._all.popleft()
        for client, bucket in list(self._failures.items()):
            while bucket and now - bucket[0] > self._window:
                bucket.popleft()
            if not bucket:
                del self._failures[client]

    def blocked(self, client: str) -> bool:
        """Whether ``client`` (or everyone) has used up its failed-login budget."""
        now = time.time()
        self._prune(now)
        if len(self._all) >= self._global_limit:
            return True
        bucket = self._failures.get(client)
        return bucket is not None and len(bucket) >= self._limit

    def record_failure(self, client: str) -> None:
        """Count one failed attempt against ``client`` and the global budget."""
        now = time.time()
        self._prune(now)
        self._all.append(now)
        self._failures.setdefault(client, deque()).append(now)
        while len(self._failures) > self._max_clients:
            self._failures.pop(next(iter(self._failures)))

    def reset(self, client: str) -> None:
        """Forget a client's failures, on a successful login."""
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
        if self.signer is not None and self.signer.verify(request.cookies.get(COOKIE)):
            return True
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value:
            return verify_password(self.settings, value.strip())
        return False


def client_address(request: StarletteRequest, settings: AuthSettings) -> str:
    """The address to rate-limit on: the peer, or what a trusted proxy reports."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in settings.trusted_proxies:
        # A proxy appends the address it saw; the last entry is the one it wrote.
        return forwarded.split(",")[-1].strip() or peer
    return peer


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


router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])


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
    if not runtime.settings.enabled or runtime.signer is None:
        return AuthStatus(enabled=False, authenticated=True, insecure=runtime.settings.insecure)

    client = client_address(request, runtime.settings)
    if runtime.limiter.blocked(client):
        raise HTTPException(
            status_code=429, detail="Too many failed attempts. Try again in a minute."
        )
    if not verify_password(runtime.settings, body.password):
        runtime.limiter.record_failure(client)
        log.warning("Failed login from %s", client)
        raise HTTPException(status_code=401, detail="Wrong password.")

    runtime.limiter.reset(client)
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
    # Same attributes as set_cookie, so the browser matches the right cookie.
    response.delete_cookie(
        COOKIE, path="/", httponly=True, samesite="lax",
        secure=_cookie_secure(request, runtime.settings),
    )
    return AuthStatus(
        enabled=runtime.settings.enabled,
        authenticated=not runtime.settings.enabled,
        insecure=not runtime.settings.enabled and runtime.settings.insecure,
    )
