.PHONY: help up down build migrate makemigrations shell test coverage coverage-ratchet lint format typecheck validate-prompts check-migrations hooks-install hooks-run discipline createsuperuser logs ps clean

# Override with `make COMPOSE=docker-compose up` if you prefer the legacy binary.
COMPOSE ?= docker compose

help:
	@echo "Common targets:"
	@echo "  build           Build the Docker images"
	@echo "  up              Start all services"
	@echo "  down            Stop all services"
	@echo "  migrate         Apply database migrations"
	@echo "  makemigrations  Generate new migrations"
	@echo "  shell           Open a Django shell in the web container"
	@echo "  test            Run the pytest suite"
	@echo "  coverage        Run the pytest suite with coverage report"
	@echo "  coverage-ratchet Raise the coverage gate to the current floor (never lowers)"
	@echo "  lint            Run ruff over the codebase"
	@echo "  format          Auto-format the codebase with ruff"
	@echo "  typecheck       Run mypy over the codebase"
	@echo "  discipline      Run the exception-discipline checker"
	@echo "  validate-prompts Validate every prompts/**/*.md file"
	@echo "  check-migrations Verify models are in sync with migration files"
	@echo "  hooks-install   Install pre-commit + pre-push hooks (once per clone)"
	@echo "  hooks-run       Run all configured hooks against every file"
	@echo "  createsuperuser Create an admin user"
	@echo "  logs            Tail the docker-compose logs"
	@echo "  ps              Show running services"
	@echo "  clean           Remove containers, volumes, and caches"

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) run --rm web python manage.py migrate

makemigrations:
	$(COMPOSE) run --rm web python manage.py makemigrations

shell:
	$(COMPOSE) run --rm web python manage.py shell

test:
	$(COMPOSE) run --rm web pytest -v

coverage:
	$(COMPOSE) run --rm web pytest --cov --cov-report=term-missing --cov-report=xml

coverage-ratchet: coverage
	python3 scripts/coverage_ratchet.py

lint:
	$(COMPOSE) run --rm web ruff check .

format:
	$(COMPOSE) run --rm web ruff format .
	$(COMPOSE) run --rm web ruff check --fix .

discipline:
	python3 scripts/check_exception_discipline.py

typecheck:
	$(COMPOSE) run --rm web mypy .

validate-prompts:
	python3 scripts/validate_prompts.py

check-migrations:
	$(COMPOSE) run --rm web python manage.py makemigrations --check --dry-run

# Pre-commit and the hook runners use the host interpreter — they don't run
# inside the docker container. If a project venv exists at ./venv, use it;
# otherwise use whatever python is on PATH (assume the user has activated
# their own env). `--break-system-packages` keeps this working on PEP 668
# distros where bare `pip install` is blocked.
PYTHON ?= $(shell test -x venv/bin/python && echo venv/bin/python || command -v python3 || echo python)
PRECOMMIT ?= $(shell test -x venv/bin/pre-commit && echo venv/bin/pre-commit || echo pre-commit)

hooks-install:
	$(PYTHON) -m pip install --upgrade pre-commit
	$(PRECOMMIT) install --install-hooks --hook-type pre-commit --hook-type pre-push

hooks-run:
	$(PRECOMMIT) run --all-files --hook-stage pre-commit
	$(PRECOMMIT) run --all-files --hook-stage pre-push

createsuperuser:
	$(COMPOSE) run --rm web python manage.py createsuperuser

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .coverage coverage.xml htmlcov
