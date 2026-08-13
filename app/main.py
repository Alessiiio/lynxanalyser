"""Lynx — FastAPI entrypoint (wiring only)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

import config
from app.checker import ALL_CHECKS
from app.checks.llm_content_check import log_ollama_startup_status
from app.database import init_db
from app.hr_network.scheduler import shutdown_monitoring_scheduler, start_monitoring_scheduler
from app.routes import auth, cases, checker, compliance, lists, network, swiss_banks, admin, product
from app.routes.deps import load_user_from_session
from app.security import MutatingOriginMiddleware, SecurityHeadersMiddleware

PUBLIC_PREFIXES = ("/static",)
PUBLIC_PATHS = {
    "/login",
    "/api/login",
    "/api/login/2fa",
    "/api/auth/pending",
    "/api/2fa/enroll/start",
    "/api/2fa/enroll/confirm",
    "/api/2fa/enroll/cancel",
    "/enroll-2fa",
    "/api/health",
    "/logout",
    "/favicon.ico",
}
PUBLIC_EXACT_METHODS = {("/login", "POST")}


class RequireLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or (path, request.method) in PUBLIC_EXACT_METHODS or any(
            path.startswith(p) for p in PUBLIC_PREFIXES
        ):
            return await call_next(request)
        user = await load_user_from_session(request)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)
        request.state.user = user
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await log_ollama_startup_status()
    start_monitoring_scheduler()
    yield
    shutdown_monitoring_scheduler()


_fastapi_kwargs = {
    "title": "Lynx",
    "version": "1.1.0",
    "lifespan": lifespan,
}
if config.IS_PRODUCTION:
    _fastapi_kwargs.update(docs_url=None, redoc_url=None, openapi_url=None)

app = FastAPI(**_fastapi_kwargs)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(product.router)
app.include_router(network.router)
app.include_router(cases.router)
app.include_router(checker.router)
app.include_router(lists.router)
app.include_router(compliance.router)
app.include_router(swiss_banks.router)


@app.get("/api/health")
async def health():
    """Public liveness probe — no internal config leakage."""
    return {"status": "ok", "version": app.version}


@app.get("/api/health/detail")
async def health_detail(request: Request):
    """Authenticated diagnostics for operators."""
    user = await load_user_from_session(request)
    if not user or user.role != "admin":
        return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
    from app.notify_email import smtp_configured

    return {
        "status": "ok",
        "environment": config.ENVIRONMENT,
        "checks": [
            {
                "name": c.name,
                "display_name": c.display_name,
                "max_score": c.max_score,
                "enabled": _check_enabled(c.name),
            }
            for c in ALL_CHECKS
        ],
        "cache_ttl_seconds": config.CACHE_TTL_SECONDS,
        "rate_limit_per_minute": config.RATE_LIMIT_PER_MINUTE,
        "https_only": config.HTTPS_ONLY,
        "watchlist_email_configured": smtp_configured(),
        "person_monitoring_cron": "daily 04:15",
        "watchlist_scan_batch": config.WATCHLIST_SCAN_BATCH,
        "watchlist_scan_delay_sec": config.WATCHLIST_SCAN_DELAY_SEC,
        "watchlist_scan_manual_limit": config.WATCHLIST_SCAN_MANUAL_LIMIT,
        "watchlist_scan_high_priority_cap": config.WATCHLIST_SCAN_HIGH_PRIORITY_CAP,
    }


def _check_enabled(name: str) -> bool:
    if name == "virustotal":
        return bool(config.VIRUSTOTAL_API_KEY)
    if name == "safebrowsing":
        return bool(config.GOOGLE_SAFEBROWSING_API_KEY)
    if name == "google_reviews":
        return bool(config.GOOGLE_PLACES_API_KEY)
    return True


# Middleware order: last added = outermost.
app.add_middleware(RequireLoginMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="lynx_session",
    max_age=60 * 60 * 8,
    same_site="lax",
    https_only=config.HTTPS_ONLY,
)
app.add_middleware(MutatingOriginMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
if config.ALLOWED_HOSTS:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=config.ALLOWED_HOSTS,
    )
