import uvicorn

import config

if __name__ == "__main__":
    # Keep a single worker: SQLite + in-process rate limits are not multi-worker safe.
    # timeout_keep_alive must stay below reverse-proxy idle; long L5 work is
    # request duration (Caddy response_header_timeout), not keep-alive.
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=not config.IS_PRODUCTION,
        proxy_headers=True,
        forwarded_allow_ips=config.FORWARDED_ALLOW_IPS,
        timeout_keep_alive=75,
    )
