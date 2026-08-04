# Backend image. Built remotely by `fly deploy`, so Docker isn't needed locally.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies against a placeholder package so this layer is cached and
# only re-runs when pyproject.toml changes. The install is editable, so it
# resolves to /app/app at import time — copying the real source below is enough,
# with no second install.
#
# psycopg[binary] ships wheels, so no compiler is needed in this image.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --upgrade pip \
    && pip install -e . \
    && rm -rf app

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000

# One worker on purpose: APScheduler runs in-process (see app/main.py), so a
# second worker would duplicate every sync and every daily brief. Scaling past
# one process means moving the scheduler out first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
