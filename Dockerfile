FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin lynx

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && playwright install-deps chromium \
    && playwright install chromium \
    && mkdir -p /ms-playwright \
    && chown -R lynx:lynx /ms-playwright

COPY --chown=lynx:lynx app ./app
COPY --chown=lynx:lynx static ./static
COPY --chown=lynx:lynx scripts ./scripts
COPY --chown=lynx:lynx data ./data
COPY --chown=lynx:lynx config.py run.py CHANGELOG.md ./

RUN mkdir -p /app/data /app/case_reports /app/compliance_reports \
    && chown -R lynx:lynx /app

USER lynx

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "run.py"]
