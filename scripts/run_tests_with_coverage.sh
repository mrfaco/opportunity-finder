#!/usr/bin/env bash
# Run the test suite through Docker if it's available (so the gate runs in
# the same environment as CI), otherwise fall back to the local interpreter.
# The coverage threshold itself lives in pyproject.toml's pytest addopts so
# there is one source of truth.
set -euo pipefail

if docker compose ps --status running --services 2>/dev/null | grep -q '^db$'; then
    exec docker compose run --rm web pytest
fi

if command -v docker >/dev/null 2>&1 && docker compose config --services >/dev/null 2>&1; then
    docker compose up -d db redis >/dev/null
    until docker compose exec -T db pg_isready -U postgres -d painminer >/dev/null 2>&1; do
        sleep 1
    done
    exec docker compose run --rm web pytest
fi

exec pytest
