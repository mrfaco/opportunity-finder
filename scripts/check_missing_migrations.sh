#!/usr/bin/env bash
# Fail if model state diverges from migration files.
#
# Runs ``manage.py makemigrations --check --dry-run``. Uses the docker
# compose ``web`` container when available (same env as CI); otherwise
# falls back to the local interpreter — which works as long as the
# project's deps are importable.
set -euo pipefail

if command -v docker >/dev/null 2>&1 && docker compose config --services >/dev/null 2>&1; then
    docker compose up -d db >/dev/null 2>&1 || true
    until docker compose exec -T db pg_isready -U postgres -d painminer >/dev/null 2>&1; do
        sleep 1
    done
    exec docker compose run --rm web python manage.py makemigrations --check --dry-run
fi

exec python manage.py makemigrations --check --dry-run
