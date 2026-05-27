"""Django settings for the pain-mining opportunity agent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-replace-me")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    # Use BasicAppConfig (not the default), so Unfold doesn't hijack
    # ``admin.site`` away from PainMinerAdminConfig's ``default_site``.
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "core.admin_apps.PainMinerAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_celery_beat",
    "django_celery_results",
    "pgvector.django",
    "core",
    "clusters",
    "ingestion",
    "agents",
    "investigations",
    "ideation",
    "notifications",
    "rest_framework",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

UNFOLD = {
    "SITE_TITLE": "Pain-Miner",
    "SITE_HEADER": "Pain-Miner",
    "SITE_SUBHEADER": "Operational dashboards",
    "SITE_URL": "/admin/",
    "THEME": "dark",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "248 250 252",
            "100": "241 245 249",
            "200": "226 232 240",
            "300": "203 213 225",
            "400": "148 163 184",
            "500": "100 116 139",
            "600": "71  85 105",
            "700": "51  65  85",
            "800": "30  41  59",
            "900": "15  23  42",
            "950": "2   6  23",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Dashboards",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Triage queue",
                        "icon": "playlist_play",
                        "link": "/admin/clusters/triage/",
                    },
                    {
                        "title": "Latest investigations",
                        "icon": "search",
                        "link": "/admin/investigations/latest/",
                    },
                    {
                        "title": "Latest ideations",
                        "icon": "lightbulb",
                        "link": "/admin/ideation/latest/",
                    },
                    {
                        "title": "Ingestion ops",
                        "icon": "cloud_download",
                        "link": "/admin/ingestion/operations/",
                    },
                    {
                        "title": "Cost dashboard",
                        "icon": "payments",
                        "link": "/admin/agents/cost-dashboard/",
                    },
                    {"title": "Prompts", "icon": "description", "link": "/admin/agents/prompts/"},
                    {
                        "title": "Filter labeling",
                        "icon": "label",
                        "link": "/admin/ingestion/filter-labeling/",
                    },
                ],
            },
        ],
    },
}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://postgres:postgres@db:5432/painminer",
    ),
}
DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Redis / Celery
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_BROKER_URL = REDIS_URL
# Store task results in Postgres via django-celery-results so the ops admin
# can list finished/in-progress/failed runs. Redis result-backend was fine
# for fire-and-forget but offered no audit trail.
CELERY_RESULT_BACKEND = "django-db"
# Mark tasks as STARTED in the result table the moment the worker picks
# them up — without this, in-progress tasks would simply be missing from
# the listing (only SUCCESS/FAILURE states are stored by default).
CELERY_TASK_TRACK_STARTED = True
# Persist task name + args + kwargs so the ops admin can show "backfill
# github 30d" rather than a bare task id.
CELERY_RESULT_EXTENDED = True
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TIMEZONE = "UTC"

# ---------------------------------------------------------------------------
# REST API — DRF config. Auth is API-key only (see api.auth); session/basic
# auth would let the browser piggyback on a logged-in admin session and
# defeat the point of having a key. Permission is enforced per-view via
# IsAuthenticated; no global IsAuthenticated default because the schema
# view (if added later) needs to stay open.
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.auth.ApiKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
}

# ---------------------------------------------------------------------------
# Anthropic / model config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
MODEL_FILTER = env("MODEL_FILTER", default="claude-haiku-4-5")
MODEL_INVESTIGATION = env("MODEL_INVESTIGATION", default="claude-sonnet-4-6")
MODEL_IDEATION = env("MODEL_IDEATION", default="claude-sonnet-4-6")

# Optional cheap-tier fallback via OpenRouter. ``core.llm.call_cheap_model``
# picks Anthropic when ANTHROPIC_API_KEY is set; otherwise falls back to
# OpenRouter (default model: DeepSeek-V3, ~4x cheaper than Haiku at the
# volumes the classifier/summarizer/judge produce). Only the cheap tier
# routes through here — the investigation/ideation agent loop stays on
# Anthropic for the tool-use + prompt-caching semantics.
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_MODEL_FILTER = env("OPENROUTER_MODEL_FILTER", default="deepseek/deepseek-chat")
# Explicit override for cheap-tier provider selection. Default ``auto``:
# Anthropic wins when both keys are set (matches the agent loop, which
# always uses Anthropic, so you don't end up with "cheap tier works but
# loop doesn't"). Set to ``openrouter`` to route this tier through OR
# while keeping Anthropic for the agent loop — the realistic config for
# cutting the cheap-tier bill without breaking anything else. Allowed:
# ``auto`` | ``anthropic`` | ``openrouter``.
CHEAP_LLM_PROVIDER = env("CHEAP_LLM_PROVIDER", default="auto")

# ---------------------------------------------------------------------------
# Embeddings — Voyage AI. voyage-3.5 outputs 1024-dim vectors natively,
# matching the pgvector columns in clusters.models (EMBEDDING_DIM).
# ---------------------------------------------------------------------------
VOYAGE_API_KEY = env("VOYAGE_API_KEY", default="")
EMBEDDING_MODEL = env("EMBEDDING_MODEL", default="voyage-3.5")

# ---------------------------------------------------------------------------
# External research APIs used by investigation tools.
# All three are blank-safe: the agent only sees a tool in its toolset if it
# has a working impl. Missing keys raise loud at first call (see stubs.py)
# rather than silently degrading.
# ---------------------------------------------------------------------------
TAVILY_API_KEY = env("TAVILY_API_KEY", default="")
GITHUB_TOKEN = env("GITHUB_TOKEN", default="")
STACKEXCHANGE_KEY = env("STACKEXCHANGE_KEY", default="")

# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
# On the first ingestion run for a source (no checkpoint yet), how far back to
# reach. Subsequent runs only pull items newer than the stored checkpoint.
INGEST_HN_INITIAL_DAYS = env.int("INGEST_HN_INITIAL_DAYS", default=7)
INGEST_GITHUB_INITIAL_DAYS = env.int("INGEST_GITHUB_INITIAL_DAYS", default=2)
# GitHub Search query for the ingestion adapter. Bias toward feature
# requests ("I wish X did Y") because they read as unmet needs. Override
# via env to widen or narrow the funnel — e.g. add ``reactions:>5`` to
# only pull issues with social proof, or ``label:bug`` for bug reports.
INGEST_GITHUB_QUERY = env(
    "INGEST_GITHUB_QUERY",
    default="label:enhancement is:open",
)
# Stack Exchange — site name (one of stackoverflow, softwareengineering,
# serverfault, askubuntu, superuser, etc.). ``softwareengineering`` tends
# to have more design + tooling discussion (i.e. opportunity-flavored)
# than mainline stackoverflow which leans how-do-I.
INGEST_STACKEXCHANGE_SITE = env("INGEST_STACKEXCHANGE_SITE", default="stackoverflow")
INGEST_STACKEXCHANGE_INITIAL_DAYS = env.int("INGEST_STACKEXCHANGE_INITIAL_DAYS", default=2)
# Optional tag filter — semicolon-separated. Example: "python;django".
# Empty means "no tag filter" (all recent questions on the site).
INGEST_STACKEXCHANGE_TAGS = env("INGEST_STACKEXCHANGE_TAGS", default="")

# ---------------------------------------------------------------------------
# Default budgets for agent runs
# ---------------------------------------------------------------------------
DEFAULT_BUDGET_MAX_STEPS = env.int("DEFAULT_BUDGET_MAX_STEPS", default=30)
DEFAULT_BUDGET_MAX_COST_USD = Decimal(env("DEFAULT_BUDGET_MAX_COST_USD", default="0.50"))
DEFAULT_BUDGET_MAX_DURATION_S = env.int("DEFAULT_BUDGET_MAX_DURATION_S", default=300)

# ---------------------------------------------------------------------------
# Clustering thresholds — see docs/clustering.md (TODO).
# Documented inline because the values are load-bearing for system behavior.
# ---------------------------------------------------------------------------
# Online-stage cosine similarity threshold for joining the nearest cluster.
# Calibrated against the voyage-4 embeddings actually in use: 0.65 over-
# merged distinct AI-topic items into mega-buckets; 0.70 still produced a
# 21-item "AI in business" conflation; 0.72 broke that into coherent
# thematic groups (RAG, code review, QA, agent workflows) with no
# spurious merges. See the re_cluster_items command + the session that
# tuned this in 2026-05 if you change it.
CLUSTER_JOIN_THRESHOLD = env.float("CLUSTER_JOIN_THRESHOLD", default=0.72)
# Pairwise centroid similarity above which we consider clusters merge candidates.
CLUSTER_MERGE_THRESHOLD = env.float("CLUSTER_MERGE_THRESHOLD", default=0.82)
# Margin by which "other cluster" must beat current cluster to trigger reassign.
CLUSTER_REASSIGN_MARGIN = env.float("CLUSTER_REASSIGN_MARGIN", default=0.05)
# Recency window for candidate clusters during online assignment.
CLUSTER_RECENCY_DAYS = env.int("CLUSTER_RECENCY_DAYS", default=90)
# Minimum member count for a cluster to be a split candidate.
SPLIT_SIZE_THRESHOLD = env.int("SPLIT_SIZE_THRESHOLD", default=30)
# Mean pairwise distance among members above which split is investigated.
SPLIT_VARIANCE_THRESHOLD = env.float("SPLIT_VARIANCE_THRESHOLD", default=0.4)

# ---------------------------------------------------------------------------
# Approvals + email
# ---------------------------------------------------------------------------
APPROVER = env("APPROVER", default="notifications.approvers.DjangoAdminApprover")
EMAIL_DIGEST_RECIPIENT = env("EMAIL_DIGEST_RECIPIENT", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="painminer@localhost")
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# ---------------------------------------------------------------------------
# Logging — structured trajectory writes go to the database, not stdout.
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
