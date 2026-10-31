"""Tailnet owner session boundary for the Fleet dashboard."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket

OWNER_LOGIN_ENV = "CONTROLCENTER_OWNER_LOGIN"
ALLOWED_ORIGINS_ENV = "CONTROLCENTER_ALLOWED_ORIGINS"
SESSION_TTL_ENV = "CONTROLCENTER_SESSION_TTL_SECONDS"
TRUSTED_PROXY_IPS_ENV = "CONTROLCENTER_TRUSTED_PROXY_IPS"
TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"
SESSION_COOKIE = "__Host-controlcenter_session"
CSRF_COOKIE = "__Host-controlcenter_csrf"
CSRF_HEADER = "X-CSRF-Token"

_BOOTSTRAP_PATHS = frozenset(
    {
        "/",
        "/index.html",
        "/api/v1/session",
    }
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_OWNER_SEARCH_POST_PATHS = frozenset({"/notes/.search/query", "/notes/.search/related"})


@dataclass(frozen=True)
class Principal:
    login: str
    role: str = "owner"


@dataclass(frozen=True)
class OwnerSession:
    principal: Principal
    csrf_nonce: str
    expires_at: float


@dataclass(frozen=True)
class AuthFailure:
    status_code: int
    code: str
    message: str

    def payload(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": False,
        }


@dataclass(frozen=True)
class AuthPolicy:
    owner_login: str
    allowed_origins: frozenset[str]
    session_ttl_seconds: int
    trusted_proxy_ips: frozenset[str]
    configuration_error: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AuthPolicy":
        owner_login = os.getenv(OWNER_LOGIN_ENV, "").strip()
        raw_origins = os.getenv(ALLOWED_ORIGINS_ENV, "")
        origins = frozenset(part.strip() for part in raw_origins.split(",") if part.strip())
        raw_proxy_ips = os.getenv(TRUSTED_PROXY_IPS_ENV, "")
        trusted_proxy_ips = frozenset(
            part.strip() for part in raw_proxy_ips.split(",") if part.strip()
        )

        try:
            ttl = int(os.getenv(SESSION_TTL_ENV, "900"))
        except ValueError:
            ttl = 0

        error = None
        if not owner_login:
            error = f"{OWNER_LOGIN_ENV} is required"
        elif not origins or any(not _valid_https_origin(origin) for origin in origins):
            error = f"{ALLOWED_ORIGINS_ENV} must contain exact HTTPS origins"
        elif not 60 <= ttl <= 3600:
            error = f"{SESSION_TTL_ENV} must be between 60 and 3600"

        return cls(owner_login, origins, ttl, trusted_proxy_ips, error)


class SessionStore:
    """Small in-memory store; sessions intentionally expire on process restart."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._sessions: dict[str, OwnerSession] = {}
        self._lock = threading.RLock()

    def create(self, principal: Principal, ttl_seconds: int) -> tuple[str, OwnerSession]:
        token = secrets.token_urlsafe(32)
        session = OwnerSession(
            principal=principal,
            csrf_nonce=secrets.token_urlsafe(24),
            expires_at=self._clock() + ttl_seconds,
        )
        with self._lock:
            self._prune_expired()
            self._sessions[_digest(token)] = session
        return token, session

    def resolve(self, token: Optional[str], principal: Principal) -> tuple[Optional[OwnerSession], str]:
        if not token:
            return None, "missing"
        digest = _digest(token)
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None, "invalid"
            if session.expires_at <= self._clock():
                self._sessions.pop(digest, None)
                return None, "expired"
        if not hmac.compare_digest(session.principal.login, principal.login):
            return None, "invalid"
        return session, "valid"

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)


class TailnetAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, store: SessionStore):
        super().__init__(app)
        self.store = store

    async def dispatch(self, request: Request, call_next) -> Response:
        policy = AuthPolicy.from_env()
        principal, failure = authenticate_proxy_identity(
            request.client.host if request.client else None,
            request.headers.get(TAILSCALE_LOGIN_HEADER),
            policy,
        )
        if failure:
            return _secure_response(auth_failure_response(failure), policy)
        if principal is None:
            return _secure_response(
                auth_failure_response(
                    AuthFailure(401, "identity_invalid", "The owner identity could not be resolved.")
                ),
                policy,
            )

        session, session_state = self.store.resolve(
            request.cookies.get(SESSION_COOKIE), principal
        )
        new_token = None
        if session is None:
            if request.url.path not in _BOOTSTRAP_PATHS:
                code = "session_expired" if session_state == "expired" else "session_required"
                return _secure_response(
                    auth_failure_response(
                        AuthFailure(401, code, "An owner session is required.")
                    ),
                    policy,
                )
            new_token, session = self.store.create(principal, policy.session_ttl_seconds)

        if request.method not in _SAFE_METHODS:
            # SilverBullet's native read-only search uses POST without our CSRF
            # header. Permit just its two search routes with a verified owner
            # session, matching cookie and exact same-origin request. The proxy
            # separately forbids all other POST routes and write methods.
            owner_search = (
                request.method == "POST"
                and request.url.path in _OWNER_SEARCH_POST_PATHS
                and request.headers.get("origin") in policy.allowed_origins
                and hmac.compare_digest(
                    request.cookies.get(CSRF_COOKIE, ""), session.csrf_nonce
                )
            )
            failure = None if owner_search else validate_origin_and_csrf(
                request.headers.get("origin"),
                request.headers.get(CSRF_HEADER),
                request.cookies.get(CSRF_COOKIE),
                session,
                policy,
            )
            if failure:
                return _secure_response(auth_failure_response(failure), policy)

        request.state.principal = principal
        request.state.owner_session = session
        response = await call_next(request)
        if new_token:
            _set_session_cookies(response, new_token, session, policy.session_ttl_seconds)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return _secure_response(response, policy)


def authenticate_proxy_identity(
    client_host: Optional[str],
    login_header: Optional[str],
    policy: AuthPolicy,
) -> tuple[Optional[Principal], Optional[AuthFailure]]:
    if not _is_trusted_proxy(client_host, policy.trusted_proxy_ips):
        return None, AuthFailure(
            403,
            "trusted_proxy_required",
            "Requests must arrive through the loopback Serve proxy.",
        )
    if policy.configuration_error:
        return None, AuthFailure(503, "auth_not_configured", "Owner authentication is not configured.")
    if not login_header:
        return None, AuthFailure(401, "identity_required", "A Tailscale user identity is required.")
    if not hmac.compare_digest(login_header, policy.owner_login):
        return None, AuthFailure(403, "owner_required", "The authenticated user is not the owner.")
    return Principal(login_header), None


def validate_origin_and_csrf(
    origin: Optional[str],
    csrf_header: Optional[str],
    csrf_cookie: Optional[str],
    session: OwnerSession,
    policy: AuthPolicy,
) -> Optional[AuthFailure]:
    if not origin or origin not in policy.allowed_origins:
        return AuthFailure(403, "origin_denied", "The request Origin is not allowed.")
    if not csrf_header or not csrf_cookie:
        return AuthFailure(403, "csrf_failed", "A valid CSRF nonce is required.")
    if not (
        hmac.compare_digest(csrf_header, csrf_cookie)
        and hmac.compare_digest(csrf_header, session.csrf_nonce)
    ):
        return AuthFailure(403, "csrf_failed", "A valid CSRF nonce is required.")
    return None


async def admit_websocket(websocket: WebSocket, store: SessionStore) -> bool:
    policy = AuthPolicy.from_env()
    principal, failure = authenticate_proxy_identity(
        websocket.client.host if websocket.client else None,
        websocket.headers.get(TAILSCALE_LOGIN_HEADER),
        policy,
    )
    if not failure and principal is None:
        failure = AuthFailure(
            401,
            "identity_invalid",
            "The owner identity could not be resolved.",
        )
    if not failure:
        origin = websocket.headers.get("origin")
        if not origin or origin not in policy.allowed_origins:
            failure = AuthFailure(403, "origin_denied", "The WebSocket Origin is not allowed.")
    if not failure:
        session, session_state = store.resolve(
            websocket.cookies.get(SESSION_COOKIE), principal
        )
        if session is None:
            code = "session_expired" if session_state == "expired" else "session_required"
            failure = AuthFailure(401, code, "An owner session is required.")
    if failure:
        await websocket.accept()
        await websocket.send_json({"type": "error", **failure.payload()})
        await websocket.close(code=1008, reason=failure.code)
        return False
    websocket.state.principal = principal
    websocket.state.owner_session = session
    return True


def auth_failure_response(failure: AuthFailure) -> JSONResponse:
    return JSONResponse(
        status_code=failure.status_code,
        content={"detail": failure.payload()},
        headers={"Cache-Control": "no-store"},
    )


def _secure_response(response: Response, policy: AuthPolicy) -> Response:
    websocket_origins = " ".join(
        sorted(
            origin.replace("https://", "wss://", 1)
            for origin in policy.allowed_origins
            if origin.startswith("https://")
        )
    )
    # Embed only the official Hermes dashboard on the same approved tailnet host.
    # The dashboard keeps its own authentication, cookies, websocket and session state.
    hermes_frames = " ".join(sorted({
        f"https://{urlsplit(origin).hostname}:19443"
        for origin in policy.allowed_origins
        if origin.startswith("https://")
        and (urlsplit(origin).hostname or "").endswith(".ts.net")
    }))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; "
        f"frame-src 'self'{(' ' + hermes_frames) if hermes_frames else ''}; "
        "img-src 'self' data:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"connect-src 'self'{(' ' + websocket_origins) if websocket_origins else ''}",
    )
    return response


def _set_session_cookies(
    response: Response,
    token: str,
    session: OwnerSession,
    ttl_seconds: int,
) -> None:
    common = {
        "max_age": ttl_seconds,
        "secure": True,
        "samesite": "strict",
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, session.csrf_nonce, httponly=False, **common)


def _is_trusted_proxy(
    client_host: Optional[str], trusted_proxy_ips: frozenset[str]
) -> bool:
    if client_host == "testclient":
        return True
    if not client_host:
        return False
    try:
        address = ipaddress.ip_address(client_host)
        if address.is_loopback:
            return True
        return any(_address_matches(address, entry) for entry in trusted_proxy_ips)
    except ValueError:
        return client_host == "localhost"


def _address_matches(address: ipaddress.IPv4Address | ipaddress.IPv6Address, entry: str) -> bool:
    try:
        return address in ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return str(address) == entry


def _valid_https_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
