FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
        # WeasyPrint backends — Pango for text shaping, Cairo for the
        # actual rasterization/PDF write. Without these the python
        # wheel imports fine but raises at first render call.
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first for layer caching.
# We copy README.md alongside pyproject.toml because hatchling references it
# in [project.readme]; without it the build backend errors before we even
# resolve dependencies.
COPY pyproject.toml README.md ./
COPY uv.lock* ./
RUN uv pip install --system --no-cache ".[dev]"

# Copy source
COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
