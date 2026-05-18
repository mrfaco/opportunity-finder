#!/usr/bin/env bash
# Run mypy through Docker if available (same env as CI), else locally.
set -euo pipefail

if command -v docker >/dev/null 2>&1 && docker compose config --services >/dev/null 2>&1; then
    exec docker compose run --rm web mypy .
fi

exec mypy .
