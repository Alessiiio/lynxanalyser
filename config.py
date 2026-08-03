from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

VIRUSTOTAL_API_KEY: str = os.getenv("VIRUSTOTAL_API_KEY", "")
GOOGLE_SAFEBROWSING_API_KEY: str = os.getenv("GOOGLE_SAFEBROWSING_API_KEY", "")
GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "")
URLSCAN_API_KEY: str = os.getenv("URLSCAN_API_KEY", "")
ZEFIX_USERNAME: str = os.getenv("ZEFIX_USERNAME", "")
ZEFIX_PASSWORD: str = os.getenv("ZEFIX_PASSWORD", "")

# Person→Mandate lookup only (not firm search). Resolve hits via Zefix.
MONEYHOUSE_PERSON_SEARCH: bool = os.getenv(
    "MONEYHOUSE_PERSON_SEARCH", "1"
).strip().lower() in {"1", "true", "yes", ""}

# IOSCO I-SCAN international warnings (separate from FINMA check)
# Request key: api@iosco.org — https://www.iosco.org/i-scan/
IOSCO_ISCAN_API_KEY: str = os.getenv("IOSCO_ISCAN_API_KEY", "")

# LLM content analysis — set one of these:
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")  # Anthropic API
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "")  # e.g. http://localhost:11434
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma4:12b")

# SQLite database for scan history (local file, not committed to git)
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./fraud_checks.db")

# Goldlist of known-legitimate domains (manual calibration pipeline)
GOLDLIST_PATH: str = os.getenv("GOLDLIST_PATH", "./data/goldlist.txt")

# Analyst-confirmed fraudulent domains (JSON map domain -> metadata)
BLOCKLIST_PATH: str = os.getenv("BLOCKLIST_PATH", "./data/blocklist.json")

# Scan cache TTL in seconds (0 = disabled)
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))

# API rate limit per client IP per minute (0 = disabled)
RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# Stricter limit for /api/login (brute-force protection)
LOGIN_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "8"))

# Runtime
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION: bool = ENVIRONMENT in {"production", "prod", "launch"}
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
# Public hostname for Caddy / TrustedHost (comma-separated). Empty = middleware off.
DOMAIN: str = os.getenv("DOMAIN", "").strip()

# Set HTTPS_ONLY=1 when served behind TLS (required for secure cookies on the public internet).
# Without DOMAIN, assume plain HTTP (local) — Secure cookies would break login/session.
_https_env = os.getenv("HTTPS_ONLY", "0").strip().lower() in {"1", "true", "yes"}
HTTPS_ONLY: bool = bool(_https_env and DOMAIN)
_raw_hosts = os.getenv("ALLOWED_HOSTS", "").strip()
if _raw_hosts:
    ALLOWED_HOSTS: list[str] = [h.strip() for h in _raw_hosts.split(",") if h.strip()]
elif DOMAIN:
    ALLOWED_HOSTS = [DOMAIN, "localhost", "127.0.0.1"]
else:
    ALLOWED_HOSTS = []

# Comma-separated IPs Caddy/nginx may forward (never use * on a publicly exposed app port).
_raw_fwd = os.getenv("FORWARDED_ALLOW_IPS", "").strip()
if _raw_fwd:
    FORWARDED_ALLOW_IPS: str | None = _raw_fwd
elif IS_PRODUCTION:
    FORWARDED_ALLOW_IPS = "127.0.0.1"
else:
    FORWARDED_ALLOW_IPS = None

# Team login (signed session cookie) — MUST be unique in production
SESSION_SECRET: str = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError(
            "SESSION_SECRET fehlt — setze einen langen Zufallswert in .env vor dem Launch"
        )
    SESSION_SECRET = "dev-only-insecure-session-secret"

# Bootstrap users (created when users table is empty; passwords reset when FORCE_RESET_SEED_PASSWORDS=1)
SEED_ADMIN_PASSWORD: str = os.getenv("SEED_ADMIN_PASSWORD", "")
SEED_CASE_MANAGER_PASSWORD: str = os.getenv(
    "SEED_CASE_MANAGER_PASSWORD",
    os.getenv("SEED_ANALYST_PASSWORD", ""),  # legacy alias
)
SEED_COMPLIANCE_PASSWORD: str = os.getenv("SEED_COMPLIANCE_PASSWORD", "")
SEED_ALESSIO_PASSWORD: str = os.getenv("SEED_ALESSIO_PASSWORD", "")
FORCE_RESET_SEED_PASSWORDS: bool = os.getenv(
    "FORCE_RESET_SEED_PASSWORDS", "0"
).strip().lower() in {"1", "true", "yes"}

if IS_PRODUCTION:
    for name, value in (
        ("SEED_ADMIN_PASSWORD", SEED_ADMIN_PASSWORD),
        ("SEED_CASE_MANAGER_PASSWORD", SEED_CASE_MANAGER_PASSWORD),
        ("SEED_COMPLIANCE_PASSWORD", SEED_COMPLIANCE_PASSWORD),
    ):
        if not value or value in {
            "admin",
            "analyst",
            "case_manager",
            "compliance",
            "password",
            "changeme",
        }:
            raise RuntimeError(
                f"{name} fehlt oder ist unsicher — setze ein starkes Passwort in .env"
            )
        if len(value) < 12:
            raise RuntimeError(f"{name} muss mindestens 12 Zeichen haben")
