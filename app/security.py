"""Production-facing HTTP hardening (headers, trusted host, origin checks)."""

from __future__ import annotations

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        return response


class MutatingOriginMiddleware(BaseHTTPMiddleware):
    """
    Reject cross-site mutating API calls that carry a session cookie.
    Same-origin browser fetches and server-to-server tools without Origin pass.
    """

    _METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in self._METHODS and request.url.path.startswith("/api/"):
            if request.url.path in {
                "/api/login",
                "/api/login/2fa",
                "/api/2fa/enroll/start",
                "/api/2fa/enroll/confirm",
                "/api/2fa/enroll/cancel",
            }:
                return await call_next(request)
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            host = request.headers.get("host", "")
            if origin or referer:
                check = origin or referer
                parsed = urlparse(check)
                incoming = parsed.netloc.lower()
                expected = host.lower()
                if not incoming or incoming != expected:
                    return JSONResponse(
                        {"detail": "Ungültiger Origin"},
                        status_code=403,
                    )
        return await call_next(request)
