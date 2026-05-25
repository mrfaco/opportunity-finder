# Django Unfold Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vanilla Django admin chrome with django-unfold, migrate all 13 custom templates onto Unfold's layout, and land targeted UX wins (clickable rows, legible agent output, status badges, sticky headers, empty states, wrapped truncation). Validate iteratively with manual Playwright screenshots.

**Architecture:** Single-PR presentation-layer migration. `PainMinerAdminSite` subclasses `unfold.sites.UnfoldAdminSite`; every `ModelAdmin` subclass swaps to `unfold.admin.ModelAdmin`; the synthetic Dashboards section in `get_app_list` is replaced by Unfold's `SIDEBAR.navigation` config. Custom templates extend `unfold/layouts/base_simple.html` and use three shared partials (`_badge.html`, `_codeblock.html`, `_empty_state.html`) plus one `admin_ux` templatetag library (`json_pretty`, `status_tone`). Clickable rows handled via a `data-href` + 5-line inline JS pattern.

**Tech Stack:** Django 5.1 · Python 3.13 · django-unfold (~0.69) · Tailwind utility classes via Unfold's components · Playwright (manual loop, no pytest-playwright) · existing pytest suite

**Spec:** `docs/superpowers/specs/2026-05-25-django-unfold-admin-design.md`

---

## File Map

### Created

- `core/templates/admin/_ux/_badge.html` — colored pill component
- `core/templates/admin/_ux/_codeblock.html` — pretty code block wrapper
- `core/templates/admin/_ux/_empty_state.html` — centered empty state
- `core/templates/admin/_ux/_clickable_rows.html` — 5-line `<script>` snippet binding `tr[data-href]` click
- `core/templatetags/__init__.py` — package marker
- `core/templatetags/admin_ux.py` — `json_pretty` + `status_tone` filters
- `tests/test_admin_ux_templatetags.py` — unit tests for the filters

### Modified

- `pyproject.toml` — add `django-unfold` to runtime deps
- `config/settings.py` — register Unfold apps + add `UNFOLD = {...}` dict
- `core/admin_site.py` — switch base class, drop `get_app_list` override
- `agents/admin.py` — swap `admin.ModelAdmin` → `unfold.admin.ModelAdmin`
- `clusters/admin.py` — swap `admin.ModelAdmin` / `admin.TabularInline` → Unfold variants
- `ideation/admin.py` — swap `admin.ModelAdmin` → `unfold.admin.ModelAdmin`
- `ingestion/admin.py` — swap `admin.ModelAdmin` → `unfold.admin.ModelAdmin`
- `investigations/admin.py` — swap `admin.ModelAdmin` → `unfold.admin.ModelAdmin`
- `agents/templates/admin/agents/cost_dashboard.html` — Unfold base + cards
- `agents/templates/admin/agents/prompt_inspector.html` — Unfold base + prose
- `agents/templates/admin/agents/trajectory.html` — Unfold base + codeblock + badges
- `clusters/templates/admin/clusters/triage.html` — Unfold base + clickable rows + badges + empty state + sticky header
- `ingestion/templates/admin/ingestion/operations.html` — Unfold base + buttons + badges + empty state
- `ingestion/templates/admin/ingestion/filter_labeling.html` — Unfold base + form classes
- `ingestion/templates/admin/ingestion/filter_eval_history.html` — Unfold base + table + badges
- `investigations/templates/admin/investigations/latest.html` — Unfold base + clickable rows + badges + empty state
- `investigations/templates/admin/investigations/investigation/review.html` — Unfold base + prose + badge
- `ideation/templates/admin/ideation/latest.html` — Unfold base + clickable rows + badges + empty state
- `ideation/templates/admin/ideation/ideation/review.html` — Unfold base + prose + badge
- `ideation/templates/admin/ideation/ideation/re_ideate.html` — Unfold base + form classes

### Unchanged

- `core/admin.py` (one-liner, no behavior)
- `core/admin_apps.py` (no behavior change)
- `clusters/templates/admin/clusters/cluster/change_list.html` (already extends standard `admin/change_list.html`, inherits Unfold automatically)

---

## Task 1 — Install django-unfold and wire INSTALLED_APPS

**Files:**
- Modify: `pyproject.toml`
- Modify: `config/settings.py`

- [ ] **Step 1.1 — Find the current Unfold version on PyPI**

Run: `docker compose run --rm web pip index versions django-unfold 2>&1 | head -5`
Note the latest stable version (e.g. `0.69.0`). Use that exact major.minor for the pin in step 1.2.

- [ ] **Step 1.2 — Add `django-unfold` to `pyproject.toml` runtime dependencies**

Edit `pyproject.toml`. Locate the `dependencies = [...]` block (currently ends with `"numpy>=1.26",`). Add immediately before the closing `]`:

```toml
    "django-unfold>=0.69,<0.70",
```

Use the actual minor you found in 1.1 — if PyPI says `0.71.x` is latest, use `"django-unfold>=0.71,<0.72"`.

- [ ] **Step 1.3 — Rebuild the web image so the new dep is installed**

Run: `make up && docker compose build web && docker compose up -d web celery_worker celery_beat`
Expected: build succeeds; `docker compose ps` shows web up.

Verify: `docker compose exec -T web python -c "import unfold; print(unfold.__version__)"`
Expected: prints the version installed.

- [ ] **Step 1.4 — Register Unfold apps in `INSTALLED_APPS`**

Edit `config/settings.py`. Find:

```python
INSTALLED_APPS = [
    "core.admin_apps.PainMinerAdminConfig",
    "django.contrib.auth",
```

Replace with:

```python
INSTALLED_APPS = [
    # Use BasicAppConfig (not the default), so Unfold doesn't hijack
    # admin.site away from PainMinerAdminConfig's default_site.
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "core.admin_apps.PainMinerAdminConfig",
    "django.contrib.auth",
```

The three `unfold*` entries MUST precede `core.admin_apps.PainMinerAdminConfig` so Unfold's templates win the search path against Django's.

**Important:** use `unfold.apps.BasicAppConfig`, NOT the bare `"unfold"`. The default Unfold AppConfig (`DefaultAppConfig.ready()`) directly reassigns `admin.site = UnfoldAdminSite()`, which bypasses `PainMinerAdminConfig.default_site` and drops every custom URL. `BasicAppConfig` skips that reassignment, so our `PainMinerAdminSite` (subclass of `UnfoldAdminSite` per Task 2) stays as `admin.site`.

- [ ] **Step 1.5 — Add the `UNFOLD` configuration dict**

Edit `config/settings.py`. Append (placement: after `MIDDLEWARE = [...]`, before `ROOT_URLCONF`):

```python
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
            "50":  "248 250 252",
            "100": "241 245 249",
            "200": "226 232 240",
            "300": "203 213 225",
            "400": "148 163 184",
            "500": "100 116 139",
            "600":  "71  85 105",
            "700":  "51  65  85",
            "800":  "30  41  59",
            "900":  "15  23  42",
            "950":   "2   6  23",
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
                    {"title": "Triage queue",          "icon": "playlist_play",  "link": "/admin/clusters/triage/"},
                    {"title": "Latest investigations", "icon": "search",         "link": "/admin/investigations/latest/"},
                    {"title": "Latest ideations",      "icon": "lightbulb",      "link": "/admin/ideation/latest/"},
                    {"title": "Ingestion ops",         "icon": "cloud_download", "link": "/admin/ingestion/operations/"},
                    {"title": "Cost dashboard",        "icon": "payments",       "link": "/admin/agents/cost-dashboard/"},
                    {"title": "Prompts",               "icon": "description",    "link": "/admin/agents/prompts/"},
                    {"title": "Filter labeling",       "icon": "label",          "link": "/admin/ingestion/filter-labeling/"},
                ],
            },
        ],
    },
}
```

- [ ] **Step 1.6 — Smoke-test the server still starts**

Run: `docker compose restart web && sleep 3 && curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:8000/admin/login/`
Expected: `200`.

If it's not 200, read `docker compose logs --tail=80 web` and fix before continuing.

- [ ] **Step 1.7 — Commit**

```bash
git add pyproject.toml config/settings.py
git commit -m "feat(admin): install django-unfold and register apps"
```

---

## Task 2 — Swap `PainMinerAdminSite` to `UnfoldAdminSite`

**Files:**
- Modify: `core/admin_site.py`

- [ ] **Step 2.1 — Read the current file**

Run: `cat core/admin_site.py | head -50` to refresh your memory on what's there. We're keeping `index()` and `get_urls()` verbatim; deleting `get_app_list()`; changing the base class.

- [ ] **Step 2.2 — Replace the base class import and the class declaration**

Edit `core/admin_site.py`. Replace:

```python
from django.contrib.admin import AdminSite
from django.urls import path


class PainMinerAdminSite(AdminSite):
    site_header = "Pain-Miner Admin"
    site_title = "Pain-Miner"
    index_title = "Operational dashboards"
```

with:

```python
from django.urls import path
from unfold.sites import UnfoldAdminSite


class PainMinerAdminSite(UnfoldAdminSite):
    # site_header / site_title come from settings.UNFOLD
    index_title = "Operational dashboards"
```

- [ ] **Step 2.3 — Delete the `get_app_list` override**

Edit `core/admin_site.py`. Delete the entire `def get_app_list(self, request, app_label=None):` method (including its docstring and body, ending at the `return [dashboards, *app_list]` line). The Unfold sidebar config now provides this navigation.

- [ ] **Step 2.4 — Run the existing admin test that asserts on the sidebar**

Run: `make test -- tests/test_cluster_triage.py::test_sidebar_includes_triage_shortcut -v`
Expected: PASS — Unfold renders the configured sidebar nav with "Triage queue" + `/admin/clusters/triage/`.

If it fails because Unfold renders the link/text differently, update the assertion to match the actual HTML (string "Triage queue" should still be present; href to `/admin/clusters/triage/` should still be present).

- [ ] **Step 2.5 — Run the full admin test suite to confirm no regression**

Run: `make test -- tests/test_cluster_triage.py tests/test_ingestion_operations.py tests/test_investigations_latest.py tests/test_ideation.py -v`
Expected: all pass.

- [ ] **Step 2.6 — Commit**

```bash
git add core/admin_site.py
git commit -m "feat(admin): switch PainMinerAdminSite to UnfoldAdminSite"
```

---

## Task 3 — Migrate every `ModelAdmin` to `unfold.admin.ModelAdmin`

Each app's `admin.py` gets the same surgical change. Test after the batch.

**Files:**
- Modify: `agents/admin.py`
- Modify: `clusters/admin.py`
- Modify: `ideation/admin.py`
- Modify: `ingestion/admin.py`
- Modify: `investigations/admin.py`

- [ ] **Step 3.1 — `agents/admin.py`**

Add import after the existing `from django.contrib import admin` line:

```python
from unfold.admin import ModelAdmin as UnfoldModelAdmin
```

Find the class declaration `class AgentRunAdmin(admin.ModelAdmin):` and change it to `class AgentRunAdmin(UnfoldModelAdmin):`. Leave `@admin.register(AgentRun)` and all attributes unchanged.

Repeat for every other `admin.ModelAdmin` subclass in the file (use `grep -n "admin.ModelAdmin" agents/admin.py` to enumerate).

- [ ] **Step 3.2 — `clusters/admin.py`**

Add import:

```python
from unfold.admin import ModelAdmin as UnfoldModelAdmin, TabularInline as UnfoldTabularInline
```

Change every `class XAdmin(admin.ModelAdmin):` to `class XAdmin(UnfoldModelAdmin):` and every `class YInline(admin.TabularInline):` to `class YInline(UnfoldTabularInline):`. Enumerate with `grep -n "admin\.\(ModelAdmin\|TabularInline\|StackedInline\)" clusters/admin.py`.

- [ ] **Step 3.3 — `ideation/admin.py`**

Same pattern as 3.1. Add the Unfold import; swap base classes. Use `grep -n "admin.ModelAdmin\|admin.TabularInline\|admin.StackedInline" ideation/admin.py` to enumerate.

- [ ] **Step 3.4 — `ingestion/admin.py`**

Same pattern.

- [ ] **Step 3.5 — `investigations/admin.py`**

Same pattern.

- [ ] **Step 3.6 — Run lint + typecheck to catch import / signature issues**

Run: `make lint && make typecheck`
Expected: both pass. If typecheck complains about `unfold.admin` lacking stubs, add `[[tool.mypy.overrides]] module = "unfold.*"  ignore_missing_imports = true` to `pyproject.toml` (single block, append to existing mypy config).

- [ ] **Step 3.7 — Run the admin test suite**

Run: `make test -- tests/test_cluster_triage.py tests/test_ingestion_operations.py tests/test_investigations_latest.py tests/test_ideation.py -v`
Expected: all pass.

- [ ] **Step 3.8 — Commit**

```bash
git add agents/admin.py clusters/admin.py ideation/admin.py ingestion/admin.py investigations/admin.py pyproject.toml
git commit -m "feat(admin): migrate ModelAdmin classes to unfold.admin.ModelAdmin"
```

---

## Task 4 — Add the shared UX template-tag library

**Files:**
- Create: `core/templatetags/__init__.py`
- Create: `core/templatetags/admin_ux.py`
- Create: `tests/test_admin_ux_templatetags.py`

- [ ] **Step 4.1 — Create the package marker**

Create `core/templatetags/__init__.py` with content:

```python
```

(Empty file — Django requires the directory to be a package.)

- [ ] **Step 4.2 — Write the failing test**

Create `tests/test_admin_ux_templatetags.py`:

```python
"""Unit tests for the admin_ux template tag library."""

from __future__ import annotations

import json

import pytest

from core.templatetags.admin_ux import json_pretty, status_tone


class TestJsonPretty:
    def test_dict_is_pretty_printed(self):
        raw = json.dumps({"b": 2, "a": 1})
        out = json_pretty(raw)
        # Indented JSON should have a newline after the opening brace.
        assert out.startswith("{\n")
        assert '"a": 1' in out
        assert '"b": 2' in out

    def test_list_is_pretty_printed(self):
        raw = json.dumps([1, 2, 3])
        out = json_pretty(raw)
        assert out.startswith("[\n")
        assert "  1," in out

    def test_non_json_string_passes_through(self):
        out = json_pretty("not json {at all")
        assert out == "not json {at all"

    def test_empty_string_passes_through(self):
        assert json_pretty("") == ""

    def test_none_passes_through(self):
        assert json_pretty(None) is None

    def test_non_string_passes_through(self):
        # int isn't a JSON string we should re-parse; return unchanged.
        assert json_pretty(42) == 42


class TestStatusTone:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("completed", "success"),
            ("success", "success"),
            ("approved", "success"),
            ("failed", "danger"),
            ("error", "danger"),
            ("rejected", "danger"),
            ("running", "info"),
            ("in_progress", "info"),
            ("pending", "warning"),
            ("queued", "warning"),
            ("draft", "neutral"),
            ("unknown_value", "neutral"),
            ("", "neutral"),
            (None, "neutral"),
        ],
    )
    def test_known_and_unknown_statuses(self, status, expected):
        assert status_tone(status) == expected

    def test_case_insensitive(self):
        assert status_tone("COMPLETED") == "success"
        assert status_tone("Failed") == "danger"
```

- [ ] **Step 4.3 — Run the test to verify it fails**

Run: `make test -- tests/test_admin_ux_templatetags.py -v`
Expected: FAIL with `ImportError` on `core.templatetags.admin_ux`.

- [ ] **Step 4.4 — Write the templatetag library**

Create `core/templatetags/admin_ux.py`:

```python
"""Template tags shared across the admin UX.

- ``json_pretty``: if the value is a JSON string, pretty-print it; otherwise
  return it unchanged. Must never raise — admin pages cannot 500 because a
  ``tool_input`` field happens to be malformed JSON.
- ``status_tone``: map a status string (case-insensitive) to a badge tone
  consumed by ``admin/_ux/_badge.html``.
"""

from __future__ import annotations

import json

from django import template

register = template.Library()


_TONE_BY_STATUS = {
    # success
    "completed": "success",
    "success": "success",
    "approved": "success",
    "promoted": "success",
    # danger
    "failed": "danger",
    "error": "danger",
    "rejected": "danger",
    "discarded": "danger",
    # info
    "running": "info",
    "in_progress": "info",
    "investigating": "info",
    # warning
    "pending": "warning",
    "queued": "warning",
    "review_pending": "warning",
}


@register.filter(name="json_pretty")
def json_pretty(value):
    """Pretty-print a JSON string; pass non-JSON through unchanged.

    Never raises — admin pages must keep rendering even with malformed input.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)


@register.filter(name="status_tone")
def status_tone(value) -> str:
    """Map a status string to a badge tone. Defaults to ``"neutral"``."""
    if not value:
        return "neutral"
    return _TONE_BY_STATUS.get(str(value).lower(), "neutral")
```

- [ ] **Step 4.5 — Run the test to verify it passes**

Run: `make test -- tests/test_admin_ux_templatetags.py -v`
Expected: PASS (all parametrized cases green).

- [ ] **Step 4.6 — Commit**

```bash
git add core/templatetags/ tests/test_admin_ux_templatetags.py
git commit -m "feat(admin): add admin_ux template tags (json_pretty, status_tone)"
```

---

## Task 5 — Add the shared UX template partials

**Files:**
- Create: `core/templates/admin/_ux/_badge.html`
- Create: `core/templates/admin/_ux/_codeblock.html`
- Create: `core/templates/admin/_ux/_empty_state.html`
- Create: `core/templates/admin/_ux/_clickable_rows.html`

- [ ] **Step 5.1 — Create `_badge.html`**

Create `core/templates/admin/_ux/_badge.html`:

```django
{% comment %}
Colored pill badge.

Args:
  label: text to display
  tone: success | danger | warning | info | neutral

Usage:
  {% include "admin/_ux/_badge.html" with label=run.status tone=run.status|status_tone %}
{% endcomment %}
{% spaceless %}
{% if tone == "success" %}
  <span class="inline-flex items-center rounded-md bg-green-50 dark:bg-green-500/10 px-2 py-1 text-xs font-medium text-green-700 dark:text-green-300 ring-1 ring-inset ring-green-600/20 dark:ring-green-500/30">{{ label }}</span>
{% elif tone == "danger" %}
  <span class="inline-flex items-center rounded-md bg-red-50 dark:bg-red-500/10 px-2 py-1 text-xs font-medium text-red-700 dark:text-red-300 ring-1 ring-inset ring-red-600/20 dark:ring-red-500/30">{{ label }}</span>
{% elif tone == "warning" %}
  <span class="inline-flex items-center rounded-md bg-amber-50 dark:bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-300 ring-1 ring-inset ring-amber-600/20 dark:ring-amber-500/30">{{ label }}</span>
{% elif tone == "info" %}
  <span class="inline-flex items-center rounded-md bg-blue-50 dark:bg-blue-500/10 px-2 py-1 text-xs font-medium text-blue-700 dark:text-blue-300 ring-1 ring-inset ring-blue-600/20 dark:ring-blue-500/30">{{ label }}</span>
{% else %}
  <span class="inline-flex items-center rounded-md bg-slate-100 dark:bg-slate-500/10 px-2 py-1 text-xs font-medium text-slate-700 dark:text-slate-300 ring-1 ring-inset ring-slate-500/20 dark:ring-slate-500/30">{{ label }}</span>
{% endif %}
{% endspaceless %}
```

- [ ] **Step 5.2 — Create `_codeblock.html`**

Create `core/templates/admin/_ux/_codeblock.html`:

```django
{% comment %}
Pretty code block. Used to render JSON/text dumps legibly.

Args:
  content: string content (raw or pre-formatted)
  language (optional): label shown in the top-right (e.g. "json", "text")
  max_height (optional): pixel height before vertical scroll (default: 480)

Usage:
  {% load admin_ux %}
  {% include "admin/_ux/_codeblock.html" with content=step.tool_input|json_pretty language="json" %}
{% endcomment %}
<div class="relative my-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
  {% if language %}
    <div class="absolute top-2 right-3 text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 select-none">{{ language }}</div>
  {% endif %}
  <pre class="overflow-auto p-4 text-xs leading-relaxed font-mono text-slate-800 dark:text-slate-100" style="max-height: {{ max_height|default:'480' }}px;"><code>{{ content }}</code></pre>
</div>
```

- [ ] **Step 5.3 — Create `_empty_state.html`**

Create `core/templates/admin/_ux/_empty_state.html`:

```django
{% comment %}
Centered empty state for queues and tables with zero rows.

Args:
  title: heading line (e.g. "No clusters to triage")
  body: secondary description
  cta_label (optional): link text
  cta_href (optional): link target

Usage:
  {% include "admin/_ux/_empty_state.html" with title="No clusters to triage" body="The queue is empty." cta_label="Show all" cta_href="?show=all" %}
{% endcomment %}
<div class="flex flex-col items-center justify-center py-12 px-4 text-center">
  <div class="rounded-full bg-slate-100 dark:bg-slate-800 p-3 mb-4">
    <svg class="h-6 w-6 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
    </svg>
  </div>
  <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ title }}</h3>
  {% if body %}<p class="mt-1 text-sm text-slate-500 dark:text-slate-400 max-w-md">{{ body }}</p>{% endif %}
  {% if cta_href and cta_label %}
    <a href="{{ cta_href }}" class="mt-4 text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline">{{ cta_label }} →</a>
  {% endif %}
</div>
```

- [ ] **Step 5.4 — Create `_clickable_rows.html`**

Create `core/templates/admin/_ux/_clickable_rows.html`:

```django
{% comment %}
Single-file include for the data-href click pattern.

Drop ``{% include "admin/_ux/_clickable_rows.html" %}`` once per page that
has rows like ``<tr data-href="/admin/foo/123/change/">``. Click on a row
navigates; clicks on actual ``<a>``/``<button>`` elements inside the row
still take precedence.

Re-including is harmless: the listener is bound on a custom data attribute
and the same rows would only re-bind.
{% endcomment %}
<script>
  (function(){
    document.querySelectorAll('tr[data-href]').forEach(function(tr){
      if (tr.dataset.clickableBound) return;
      tr.dataset.clickableBound = '1';
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', function(ev){
        // Don't hijack clicks on real interactive elements inside the row.
        if (ev.target.closest('a,button,input,select,label,form')) return;
        window.location = tr.dataset.href;
      });
    });
  })();
</script>
```

- [ ] **Step 5.5 — Verify the partials load via Django's template engine**

Run:

```bash
docker compose exec -T web python manage.py shell -c "
from django.template.loader import get_template
for p in [
    'admin/_ux/_badge.html',
    'admin/_ux/_codeblock.html',
    'admin/_ux/_empty_state.html',
    'admin/_ux/_clickable_rows.html',
]:
    t = get_template(p)
    print('ok', p)
"
```

Expected: `ok` four times.

- [ ] **Step 5.6 — Commit**

```bash
git add core/templates/admin/_ux/
git commit -m "feat(admin): add shared UX partials (badge, codeblock, empty state, clickable rows)"
```

---

## Task 6 — Migrate `triage.html` (template-migration pattern)

This task establishes the migration pattern. Subsequent template tasks repeat it concisely.

**Files:**
- Modify: `clusters/templates/admin/clusters/triage.html`

- [ ] **Step 6.1 — Replace the template content**

Overwrite `clusters/templates/admin/clusters/triage.html` with:

```django
{% extends "unfold/layouts/base_simple.html" %}
{% load admin_ux %}

{% block breadcrumbs %}{% endblock %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-1">Cluster triage</h1>
<p class="text-sm text-slate-600 dark:text-slate-400 mb-6">
  Ranked by <code class="font-mono text-xs">log2(1 + size) × avg_confidence × exp(-days_since_last_seen / 7)</code>.
  Top {{ rows|length|default:0 }} candidates shown.
  {% if show_all %}
    <a class="text-blue-600 dark:text-blue-400 hover:underline" href="?">Hide investigated</a>
  {% else %}
    <a class="text-blue-600 dark:text-blue-400 hover:underline" href="?show=all">Show all (including investigated)</a>
  {% endif %}
</p>

{% if rows %}
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800 sticky top-0 z-10">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Title</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Size</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Avg conf</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Max conf</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Last seen</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Status</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Priority</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Action</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for row in rows %}
          <tr data-href="/admin/clusters/cluster/{{ row.cluster.id }}/change/"
              class="hover:bg-slate-50 dark:hover:bg-slate-800 {% if row.highlight %}bg-amber-50 dark:bg-amber-500/10{% endif %}">
            <td class="px-4 py-2 text-sm">
              <span class="text-slate-900 dark:text-slate-100" title="{{ row.title }}">{{ row.title|truncatechars:90 }}</span>
            </td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.size }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.avg_conf|floatformat:2 }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.max_conf|floatformat:2 }}</td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300">
              {% if row.days_since is None %}never{% else %}{{ row.days_since|floatformat:1 }}d ago{% endif %}
            </td>
            <td class="px-4 py-2 text-sm">
              {% include "admin/_ux/_badge.html" with label=row.status tone=row.status|status_tone %}
            </td>
            <td class="px-4 py-2 text-sm tabular-nums font-semibold text-slate-900 dark:text-slate-100">{{ row.priority|floatformat:3 }}</td>
            <td class="px-4 py-2 text-sm">
              <form method="post" action="/admin/clusters/cluster/{{ row.cluster.id }}/investigate/" class="inline">
                {% csrf_token %}
                <button type="submit" class="inline-flex items-center rounded-md bg-blue-600 hover:bg-blue-700 px-2.5 py-1 text-xs font-medium text-white shadow-sm">Investigate</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% include "admin/_ux/_clickable_rows.html" %}
{% else %}
  {% if show_all %}
    {% include "admin/_ux/_empty_state.html" with title="No clusters to triage" body="There are no clusters at all yet — ingestion may not have run." %}
  {% else %}
    {% include "admin/_ux/_empty_state.html" with title="No clusters to triage" body="Every candidate has been investigated already." cta_label="Show all" cta_href="?show=all" %}
  {% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 6.2 — Run the triage tests**

Run: `make test -- tests/test_cluster_triage.py -v`
Expected: all pass. Existing assertions (`"Cluster triage"` in body, cluster titles in body, `/admin/clusters/cluster/<id>/investigate/` form action, ordering) still hold.

If `test_admin_root_serves_triage` or `test_triage_renders` fails on a string assertion, fix the template so the asserted substring is present in the rendered HTML — do **not** weaken the test.

- [ ] **Step 6.3 — Smoke screenshot**

Run the page in a browser at `http://localhost:8000/admin/clusters/triage/` and confirm visually: dark chrome, sticky header on scroll, clickable rows, status badge column, priority right-aligned.

Capture a screenshot to `/tmp/triage.png` via Playwright (we'll do a structured sweep in Task 11; this is just a sanity-check stop).

- [ ] **Step 6.4 — Commit**

```bash
git add clusters/templates/admin/clusters/triage.html
git commit -m "feat(admin): migrate triage queue to Unfold layout + UX wins"
```

---

## Task 7 — Migrate trajectory + cost dashboard + prompt inspector

**Files:**
- Modify: `agents/templates/admin/agents/trajectory.html`
- Modify: `agents/templates/admin/agents/cost_dashboard.html`
- Modify: `agents/templates/admin/agents/prompt_inspector.html`

- [ ] **Step 7.1 — Replace `trajectory.html`**

Overwrite `agents/templates/admin/agents/trajectory.html` with:

```django
{% extends "unfold/layouts/base_simple.html" %}
{% load admin_ux %}

{% block content %}
<div class="mb-6 space-y-2">
  <h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100">Trajectory · run <span class="font-mono text-base">{{ run.id|slice:":8" }}</span></h1>
  <div class="flex flex-wrap items-center gap-3 text-sm">
    {% include "admin/_ux/_badge.html" with label=run.status tone=run.status|status_tone %}
    {% if run.termination_reason %}
      <span class="text-slate-500 dark:text-slate-400">Termination: <code class="font-mono text-xs">{{ run.termination_reason }}</code></span>
    {% endif %}
  </div>
  <dl class="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3">
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Steps</dt>
      <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ run.steps_used }} / {{ run.budget_max_steps }}</dd>
    </div>
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Cost</dt>
      <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">${{ run.cost_used_usd }} / ${{ run.budget_max_cost_usd }}</dd>
    </div>
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Duration</dt>
      <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ run.duration_used_s }}s / {{ run.budget_max_duration_s }}s</dd>
    </div>
  </dl>
</div>

{% if note %}
  <div class="mb-6 rounded-md bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">{{ note }}</div>
{% endif %}

<h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Steps</h2>
<ol class="space-y-3">
  {% for step in steps %}
    <li class="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
      <details>
        <summary class="cursor-pointer select-none px-4 py-3 flex flex-wrap items-center gap-2 text-sm">
          <span class="font-semibold text-slate-900 dark:text-slate-100">#{{ step.step_number }}</span>
          <span class="text-slate-600 dark:text-slate-400">{{ step.step_type }}</span>
          {% if step.tool_name %}<code class="font-mono text-xs bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded">{{ step.tool_name }}</code>{% endif %}
          {% if step.tool_status %}{% include "admin/_ux/_badge.html" with label=step.tool_status tone=step.tool_status|status_tone %}{% endif %}
          <span class="text-slate-500 dark:text-slate-400 tabular-nums">${{ step.cost_usd }}</span>
          <span class="text-slate-500 dark:text-slate-400 tabular-nums">{{ step.input_tokens }}+{{ step.output_tokens }} tok</span>
          {% if step.was_cached %}{% include "admin/_ux/_badge.html" with label="cached" tone="info" %}{% endif %}
        </summary>
        <div class="px-4 pb-4 space-y-3">
          {% if step.tool_input %}
            <div>
              <h4 class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">Input</h4>
              {% include "admin/_ux/_codeblock.html" with content=step.tool_input|json_pretty language="json" %}
            </div>
          {% endif %}
          {% if step.tool_output_summary %}
            <div>
              <h4 class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">Output summary</h4>
              {% include "admin/_ux/_codeblock.html" with content=step.tool_output_summary|json_pretty language="json" %}
            </div>
          {% endif %}
        </div>
      </details>
    </li>
  {% empty %}
    {% include "admin/_ux/_empty_state.html" with title="No steps recorded yet" body="The run has not produced any steps." %}
  {% endfor %}
</ol>

<h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mt-8 mb-3">Events (raw)</h2>
<details class="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
  <summary class="cursor-pointer select-none px-4 py-3 text-sm text-slate-700 dark:text-slate-300">{{ events.count }} event{{ events.count|pluralize }}</summary>
  <ol class="px-4 pb-4 text-xs font-mono text-slate-600 dark:text-slate-400 space-y-1">
    {% for ev in events %}
      <li><code>{{ ev.event_type }}</code> @ {{ ev.recorded_at }}{% if ev.tool_name %} · {{ ev.tool_name }}{% endif %}</li>
    {% endfor %}
  </ol>
</details>
{% endblock %}
```

- [ ] **Step 7.2 — Replace `cost_dashboard.html`**

Overwrite `agents/templates/admin/agents/cost_dashboard.html` with:

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-6">Cost dashboard</h1>

<section class="mb-8">
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Today · {{ today.date }}</h2>
  <dl class="grid grid-cols-1 sm:grid-cols-3 gap-3">
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Filter classifications</dt>
      <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ today.filter_classifications }} <span class="text-slate-500 dark:text-slate-400">(${{ today.filter_cost_usd }})</span></dd>
    </div>
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Agent runs</dt>
      <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ today.agent_runs }} <span class="text-slate-500 dark:text-slate-400">(${{ today.agent_run_cost_usd }})</span></dd>
    </div>
    <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3 bg-slate-50 dark:bg-slate-800">
      <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Total spend today</dt>
      <dd class="mt-1 text-base font-semibold tabular-nums text-slate-900 dark:text-slate-100">${{ today.total_cost_usd }}</dd>
    </div>
  </dl>
</section>

<section class="mb-8">
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Last 7 days · total ${{ totals_7d }}</h2>
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Date</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Classifications</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Filter $</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Runs</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Run $</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Total $</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for d in last_7 %}
          <tr>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300">{{ d.date }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ d.filter_classifications }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ d.filter_cost_usd }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ d.agent_runs }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ d.agent_run_cost_usd }}</td>
            <td class="px-4 py-2 text-sm tabular-nums font-semibold text-slate-900 dark:text-slate-100">${{ d.total_cost_usd }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="mb-8">
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Per-model breakdown</h2>
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Model</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Steps</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cost</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for row in per_model %}
          <tr>
            <td class="px-4 py-2 text-sm"><code class="font-mono text-xs">{{ row.model }}</code></td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.steps }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ row.cost|default:"0" }}</td>
          </tr>
        {% empty %}
          <tr><td colspan="3" class="px-4 py-6 text-center text-sm text-slate-500 dark:text-slate-400">No steps yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="mb-8">
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Top 5 most expensive runs (last 7 days)</h2>
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Run</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cluster</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cost</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Steps</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Started</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for run in top_runs %}
          <tr data-href="/admin/agents/agentrun/{{ run.id }}/change/" class="hover:bg-slate-50 dark:hover:bg-slate-800">
            <td class="px-4 py-2 text-sm"><a href="/admin/agents/agentrun/{{ run.id }}/change/" class="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline">{{ run.id|slice:":8" }}</a></td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300">{{ run.cluster }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ run.cost_used_usd }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ run.steps_used }}</td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300">{{ run.started_at }}</td>
          </tr>
        {% empty %}
          <tr><td colspan="5" class="px-4 py-6 text-center text-sm text-slate-500 dark:text-slate-400">No runs yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% include "admin/_ux/_clickable_rows.html" %}
</section>
{% endblock %}
```

- [ ] **Step 7.3 — Replace `prompt_inspector.html`**

Overwrite `agents/templates/admin/agents/prompt_inspector.html` with:

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-2">Prompts (read-only)</h1>

{% if note %}
  <div class="mb-6 rounded-md bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">{{ note }}</div>
{% endif %}

{% for entry in prompts %}
  <section class="my-6 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100">{{ entry.prompt.agent_name }} / {{ entry.prompt.kind }}</h2>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">
      <code class="font-mono text-xs">{{ entry.prompt.path }}</code> ·
      hash <code class="font-mono text-xs">{{ entry.prompt.hash|slice:":12" }}</code> ·
      {{ entry.runs_used }} run{{ entry.runs_used|pluralize }} used this version
    </p>
    {% if entry.prompt.frontmatter %}
      <details class="mt-3">
        <summary class="cursor-pointer text-sm text-slate-700 dark:text-slate-300">Frontmatter</summary>
        <pre class="mt-2 p-3 rounded bg-slate-50 dark:bg-slate-800 text-xs font-mono text-slate-800 dark:text-slate-100 overflow-auto">{{ entry.prompt.frontmatter }}</pre>
      </details>
    {% endif %}
    <article class="prose dark:prose-invert max-w-none mt-4 pt-3 border-t border-dashed border-slate-300 dark:border-slate-700">
      {{ entry.html|safe }}
    </article>
  </section>
{% empty %}
  {% include "admin/_ux/_empty_state.html" with title="No prompts on disk yet" body="Add prompt files under prompts/ to make them appear here." %}
{% endfor %}
{% endblock %}
```

Note: I removed the previous `Step 7.4` numbering because we merged the prompt-inspector into 7.3. Step 7.5/7.6 below renumber to 7.4/7.5.

- [ ] **Step 7.4 — Run the agent-admin tests if present, otherwise smoke via curl**

Run: `make test -- tests/ -v -k "trajectory or cost or prompt"` (skips silently if no matches).

Smoke each page (any non-500 response proves the templates compile):

```bash
curl -sf -o /dev/null -w "trajectory %{http_code}\n" "http://localhost:8000/admin/agents/run/$(docker compose exec -T web python -c 'from agents.models import AgentRun; print(AgentRun.objects.first().id)' | tr -d '\r')/trajectory/"
curl -sf -o /dev/null -w "cost %{http_code}\n" http://localhost:8000/admin/agents/cost-dashboard/
curl -sf -o /dev/null -w "prompts %{http_code}\n" http://localhost:8000/admin/agents/prompts/
```

Expected: each returns either `200` or `302` (login redirect). Anything `500` means a template syntax error — read `docker compose logs --tail=40 web` and fix.

- [ ] **Step 7.5 — Commit**

```bash
git add agents/templates/admin/agents/
git commit -m "feat(admin): migrate trajectory/cost-dashboard/prompt-inspector to Unfold"
```

---

## Task 8 — Migrate the "latest" views and review forms (investigations + ideation)

**Files:**
- Modify: `investigations/templates/admin/investigations/latest.html`
- Modify: `investigations/templates/admin/investigations/investigation/review.html`
- Modify: `ideation/templates/admin/ideation/latest.html`
- Modify: `ideation/templates/admin/ideation/ideation/review.html`
- Modify: `ideation/templates/admin/ideation/ideation/re_ideate.html`

- [ ] **Step 8.1 — Overwrite `investigations/templates/admin/investigations/latest.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}
{% load admin_ux %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-1">Latest investigations</h1>
<p class="text-sm text-slate-600 dark:text-slate-400 mb-6">
  Newest first, up to {{ limit }} rows. Filter:
  <a class="text-blue-600 dark:text-blue-400 hover:underline" href="?">all</a>
  {% for value, label in status_choices %}
    <span class="text-slate-400 dark:text-slate-600">|</span>
    <a class="text-blue-600 dark:text-blue-400 hover:underline {% if current_status == value %}font-semibold underline{% endif %}" href="?status={{ value }}">{{ label }}</a>
  {% endfor %}
</p>

{% if rows %}
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800 sticky top-0 z-10">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Created</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Headline</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Conf</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cluster</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Status</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cost</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Steps</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for row in rows %}
          <tr data-href="/admin/investigations/investigation/{{ row.investigation.id }}/change/" class="hover:bg-slate-50 dark:hover:bg-slate-800">
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 whitespace-nowrap">{{ row.created_at|date:"Y-m-d H:i" }}</td>
            <td class="px-4 py-2 text-sm">
              <a href="/admin/investigations/investigation/{{ row.investigation.id }}/change/" class="font-semibold text-slate-900 dark:text-slate-100 hover:underline" title="{{ row.headline }}">{{ row.headline|truncatechars:90 }}</a>
              {% if row.problem %}<div class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{{ row.problem }}{% if row.problem|length >= 160 %}…{% endif %}</div>{% endif %}
              {% if row.target_user %}<div class="text-xs text-slate-500 dark:text-slate-500 mt-0.5">→ {{ row.target_user }}</div>{% endif %}
            </td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{% if row.confidence is not None %}{{ row.confidence|floatformat:2 }}{% else %}—{% endif %}</td>
            <td class="px-4 py-2 text-sm"><a href="/admin/clusters/cluster/{{ row.cluster.id }}/change/" class="text-blue-600 dark:text-blue-400 hover:underline" title="{{ row.cluster.title|default:row.cluster.id }}">{{ row.cluster.title|default:row.cluster.id|truncatechars:40 }}</a></td>
            <td class="px-4 py-2 text-sm">{% include "admin/_ux/_badge.html" with label=row.status tone=row.status|status_tone %}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ row.cost_usd|default:"0" }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.steps }}</td>
            <td class="px-4 py-2 text-sm whitespace-nowrap">
              <a href="/admin/agents/run/{{ row.run.id }}/trajectory/" class="text-blue-600 dark:text-blue-400 hover:underline mr-2">trajectory</a>
              {% if row.status == awaiting_review_status %}
                <form method="post" action="/admin/investigations/{{ row.investigation.id }}/promote/" class="inline">
                  {% csrf_token %}
                  <button type="submit" class="inline-flex items-center rounded-md bg-blue-600 hover:bg-blue-700 px-2.5 py-1 text-xs font-medium text-white shadow-sm">Promote</button>
                </form>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% include "admin/_ux/_clickable_rows.html" %}
{% else %}
  {% include "admin/_ux/_empty_state.html" with title="No investigations yet" body="Pick a cluster from the triage queue and click Investigate to start one." cta_label="Open triage queue" cta_href="/admin/clusters/triage/" %}
{% endif %}
{% endblock %}
```

- [ ] **Step 8.2 — Overwrite `investigations/templates/admin/investigations/investigation/review.html`**

```django
{% extends "admin/change_form.html" %}
{% load admin_ux %}

{% block content %}
{% if investigation %}
<div class="mb-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
  <section class="lg:col-span-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2">Brief</h2>
    {% if brief.headline %}<h3 class="text-xl font-semibold text-slate-900 dark:text-slate-100 mb-3">{{ brief.headline }}</h3>{% endif %}
    <article class="prose dark:prose-invert max-w-none text-sm">
      {% if brief.problem_statement %}<p><strong>Problem:</strong> {{ brief.problem_statement }}</p>{% endif %}
      {% if brief.target_user %}<p><strong>Target user:</strong> {{ brief.target_user }}</p>{% endif %}
      {% if brief.evidence_summary %}<p><strong>Evidence summary:</strong> {{ brief.evidence_summary }}</p>{% endif %}
      {% if brief.competitors %}
        <h4>Competitors</h4>
        <ul>{% for c in brief.competitors %}<li><a href="{{ c.url|default:'#' }}">{{ c.name }}</a> — {{ c.revenue_signal|default:"-" }}</li>{% endfor %}</ul>
      {% endif %}
      {% if brief.differentiators %}<h4>Differentiators</h4><ul>{% for d in brief.differentiators %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}
      {% if brief.risks %}<h4>Risks</h4><ul>{% for r in brief.risks %}<li>{{ r }}</li>{% endfor %}</ul>{% endif %}
      {% if brief.recommended_next_step %}<p><strong>Recommended next step:</strong> {{ brief.recommended_next_step }}</p>{% endif %}
      {% if brief.confidence %}<p><strong>Agent confidence:</strong> {% include "admin/_ux/_badge.html" with label=brief.confidence tone=brief.confidence|status_tone %}</p>{% endif %}
    </article>
  </section>
  <aside class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 p-5 text-sm">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">Context</h2>
    <p class="mb-1"><strong>Cluster:</strong> {{ cluster.title|default:cluster.id }}</p>
    <p class="mb-1"><strong>Size:</strong> {{ cluster.size }} items</p>
    <p class="mb-1"><strong>Sources:</strong> {{ cluster.sources|join:", " }}</p>
    <p class="mb-3"><strong>Classifier score:</strong> {{ cluster.classifier_score }}</p>
    <hr class="my-3 border-slate-300 dark:border-slate-700">
    <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-2">Agent run</h3>
    <p class="mb-1"><a href="/admin/agents/agentrun/{{ primary_run.id }}/change/" class="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline">{{ primary_run.id|slice:":8" }}</a></p>
    <p class="mb-1 tabular-nums">Cost: ${{ primary_run.cost_used_usd }} · {{ primary_run.steps_used }} steps</p>
    <p><a href="/admin/agents/run/{{ primary_run.id }}/trajectory/" class="text-blue-600 dark:text-blue-400 hover:underline">View trajectory →</a></p>
  </aside>
</div>
{% endif %}
{{ block.super }}
{% endblock %}
```

- [ ] **Step 8.3 — Overwrite `ideation/templates/admin/ideation/latest.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}
{% load admin_ux %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-1">Latest ideations</h1>
<p class="text-sm text-slate-600 dark:text-slate-400 mb-6">
  Newest first, up to {{ limit }} rows. Filter:
  <a class="text-blue-600 dark:text-blue-400 hover:underline" href="?">all</a>
  {% for value, label in status_choices %}
    <span class="text-slate-400 dark:text-slate-600">|</span>
    <a class="text-blue-600 dark:text-blue-400 hover:underline {% if current_status == value %}font-semibold underline{% endif %}" href="?status={{ value }}">{{ label }}</a>
  {% endfor %}
</p>

{% if rows %}
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800 sticky top-0 z-10">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Created</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Investigation</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Status</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Guidance</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Concepts</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cost</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Steps</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for row in rows %}
          <tr data-href="/admin/ideation/ideation/{{ row.ideation.id }}/change/" class="hover:bg-slate-50 dark:hover:bg-slate-800">
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 whitespace-nowrap">{{ row.created_at|date:"Y-m-d H:i" }}</td>
            <td class="px-4 py-2 text-sm">
              {% if row.investigation %}
                <a href="/admin/investigations/investigation/{{ row.investigation.id }}/change/" class="font-semibold text-slate-900 dark:text-slate-100 hover:underline" title="{{ row.headline }}">{{ row.headline|truncatechars:90 }}</a>
              {% else %}
                <em class="text-slate-400">(no investigation)</em>
              {% endif %}
            </td>
            <td class="px-4 py-2 text-sm">{% include "admin/_ux/_badge.html" with label=row.status tone=row.status|status_tone %}</td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 max-w-[240px]">
              {% if row.guidance %}<span title="{{ row.guidance }}">{{ row.guidance|truncatechars:120 }}</span>{% else %}<span class="text-slate-400">—</span>{% endif %}
            </td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 max-w-[280px]">
              {% if row.concept_names %}{{ row.concept_names|join:" · " }}{% else %}<span class="text-slate-400">(none yet)</span>{% endif %}
            </td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{% if row.cost_usd is not None %}${{ row.cost_usd }}{% else %}—{% endif %}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{% if row.steps is not None %}{{ row.steps }}{% else %}—{% endif %}</td>
            <td class="px-4 py-2 text-sm whitespace-nowrap">
              <a href="/admin/ideation/ideation/{{ row.ideation.id }}/change/" class="text-blue-600 dark:text-blue-400 hover:underline mr-2">detail</a>
              {% if row.run %}<a href="/admin/agents/run/{{ row.run.id }}/trajectory/" class="text-blue-600 dark:text-blue-400 hover:underline">trajectory</a>{% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% include "admin/_ux/_clickable_rows.html" %}
{% else %}
  {% include "admin/_ux/_empty_state.html" with title="No ideations yet" body="Promote an investigation to kick off ideation." cta_label="Open latest investigations" cta_href="/admin/investigations/latest/" %}
{% endif %}
{% endblock %}
```

- [ ] **Step 8.4 — Overwrite `ideation/templates/admin/ideation/ideation/review.html`**

```django
{% extends "admin/change_form.html" %}
{% load admin_ux %}

{% block content %}
{% if ideation %}
<div class="mb-6 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
  <p class="mb-1 text-sm">
    <strong class="text-slate-900 dark:text-slate-100">Investigation:</strong>
    <a href="/admin/investigations/investigation/{{ investigation.id }}/change/" class="text-blue-600 dark:text-blue-400 hover:underline">
      <span class="font-mono text-xs">{{ investigation.id|slice:":8" }}</span> — {{ investigation.brief.headline|default:"(no headline)" }}
    </a>
  </p>
  <p class="mb-1 text-sm flex items-center gap-2">
    <strong class="text-slate-900 dark:text-slate-100">Status:</strong>
    {% include "admin/_ux/_badge.html" with label=ideation.status tone=ideation.status|status_tone %}
    <span class="text-slate-500 dark:text-slate-400">· created {{ ideation.created_at }}</span>
  </p>
  {% if ideation.guidance %}
    <p class="mb-1 text-sm text-slate-700 dark:text-slate-300"><strong>Guidance:</strong> {{ ideation.guidance }}</p>
  {% endif %}
  {% if primary_run %}
    <p class="mb-3 text-sm text-slate-700 dark:text-slate-300">
      Run <a href="/admin/agents/agentrun/{{ primary_run.id }}/change/" class="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline">{{ primary_run.id|slice:":8" }}</a>
      <span class="tabular-nums">· ${{ primary_run.cost_used_usd }} · {{ primary_run.steps_used }} steps</span>
      · <a href="/admin/agents/run/{{ primary_run.id }}/trajectory/" class="text-blue-600 dark:text-blue-400 hover:underline">trajectory</a>
    </p>
  {% endif %}
  <p>
    <a href="{{ re_ideate_url }}" class="inline-flex items-center rounded-md bg-blue-600 hover:bg-blue-700 px-3 py-1.5 text-sm font-medium text-white shadow-sm">Re-ideate with guidance →</a>
  </p>
</div>

{% if concepts %}
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    {% for c in concepts %}
      <section class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-5">
        <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1">{{ c.name }}</h2>
        <p class="text-xs text-slate-500 dark:text-slate-400 mb-2"><strong>bet axis:</strong> {{ c.bet_axis }}</p>
        <p class="italic text-slate-700 dark:text-slate-300 mb-4">{{ c.one_liner }}</p>

        <article class="prose dark:prose-invert prose-sm max-w-none">
          <h4>Core features</h4>
          <ul>{% for f in c.core_features %}<li>{{ f }}</li>{% endfor %}</ul>

          <h4>Explicitly NOT included</h4>
          <ul>{% for f in c.explicitly_not_included %}<li>{{ f }}</li>{% endfor %}</ul>

          <p><strong>Buyer:</strong> {{ c.buyer }}</p>
          <p><strong>Pricing hypothesis:</strong> {{ c.rough_pricing_hypothesis }}</p>

          <h4>Competitive landscape</h4>
        </article>
        <div class="overflow-x-auto rounded border border-slate-200 dark:border-slate-700 my-3">
          <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700 text-sm">
            <thead class="bg-slate-100 dark:bg-slate-800">
              <tr>
                <th class="px-3 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Name</th>
                <th class="px-3 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Threat</th>
                <th class="px-3 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Overlap / evidence</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-200 dark:divide-slate-800">
              {% for comp in c.competitive_landscape %}
                <tr>
                  <td class="px-3 py-1.5">{% if comp.url %}<a href="{{ comp.url }}" class="text-blue-600 dark:text-blue-400 hover:underline">{{ comp.name }}</a>{% else %}{{ comp.name }}{% endif %}</td>
                  <td class="px-3 py-1.5">{% include "admin/_ux/_badge.html" with label=comp.threat_level tone=comp.threat_level|status_tone %}</td>
                  <td class="px-3 py-1.5 text-slate-700 dark:text-slate-300">{{ comp.overlap }} — {{ comp.evidence }}</td>
                </tr>
              {% empty %}
                <tr><td colspan="3" class="px-3 py-3 text-center text-slate-500 dark:text-slate-400">No competitors listed.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <article class="prose dark:prose-invert prose-sm max-w-none">
          <h4>MVP scope · {{ c.mvp_scope.build_size }}</h4>
          <p class="text-sm">{{ c.mvp_scope.build_estimate_assumptions }}</p>
          <p><strong>Minimum features for test:</strong></p>
          <ul>{% for f in c.mvp_scope.minimum_features_for_test %}<li>{{ f }}</li>{% endfor %}</ul>
          <p><strong>Deferred to v2:</strong></p>
          <ul>{% for f in c.mvp_scope.explicitly_deferred_to_v2 %}<li>{{ f }}</li>{% endfor %}</ul>

          <p><strong>First validation test:</strong> {{ c.first_validation_test }}</p>

          <h4>Kill criteria</h4>
          <ul>{% for k in c.kill_criteria %}<li>{{ k }}</li>{% endfor %}</ul>

          <h4>Fit to builder</h4>
          <p><strong>Distribution:</strong> {{ c.fit_to_builder.distribution_fit }}</p>
          <p><strong>Skill:</strong> {{ c.fit_to_builder.skill_fit }}</p>
          <p><strong>Capital:</strong> {{ c.fit_to_builder.capital_fit }}</p>
        </article>
      </section>
    {% endfor %}
  </div>
{% else %}
  <div class="rounded-md bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
    No concepts yet — the agent run is still draft or has not produced output.
  </div>
{% endif %}

{% if ideation_notes %}
  <section class="mt-6 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 p-4">
    <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-2">Ideation notes</h3>
    <p class="text-sm text-slate-700 dark:text-slate-300">{{ ideation_notes }}</p>
  </section>
{% endif %}
{% endif %}

{{ block.super }}
{% endblock %}
```

- [ ] **Step 8.5 — Overwrite `ideation/templates/admin/ideation/ideation/re_ideate.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-2">Re-ideate investigation</h1>
<p class="text-sm text-slate-700 dark:text-slate-300 mb-1">
  <strong class="text-slate-900 dark:text-slate-100">Investigation:</strong>
  <a href="/admin/investigations/investigation/{{ investigation.id }}/change/" class="text-blue-600 dark:text-blue-400 hover:underline">
    <span class="font-mono text-xs">{{ investigation.id|slice:":8" }}</span> — {{ brief.headline|default:"(no headline)" }}
  </a>
</p>
{% if brief.problem_statement %}<p class="italic text-sm text-slate-600 dark:text-slate-400 mb-6">{{ brief.problem_statement }}</p>{% endif %}

<form method="post" class="mt-4 max-w-2xl">
  {% csrf_token %}
  <div class="mb-4">
    <label for="guidance" class="block text-sm font-semibold text-slate-900 dark:text-slate-100 mb-1">Guidance (optional)</label>
    <textarea id="guidance" name="guidance" rows="5"
              class="block w-full rounded-md border-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 shadow-sm text-sm font-mono px-3 py-2"
              placeholder="e.g. 'try a smaller wedge', 'consider an open-source play', 'reframe around the engineer as buyer'"></textarea>
  </div>
  <div class="flex items-center gap-3">
    <button type="submit" class="inline-flex items-center rounded-md bg-blue-600 hover:bg-blue-700 px-3 py-1.5 text-sm font-medium text-white shadow-sm">Run ideation →</button>
    <a href="/admin/ideation/ideation/" class="text-sm text-slate-600 dark:text-slate-400 hover:underline">Cancel</a>
  </div>
</form>

<p class="mt-8 text-xs text-slate-500 dark:text-slate-400 max-w-2xl">
  Submitting creates a new <em>draft</em> ideation and enqueues the agent run.
  The ideation will flip to <em>awaiting review</em> when the agent finishes.
</p>
{% endblock %}
```

- [ ] **Step 8.6 — Run the relevant test suites**

Run: `make test -- tests/test_investigations_latest.py tests/test_ideation.py -v`
Expected: all pass.

- [ ] **Step 8.7 — Commit**

```bash
git add investigations/templates/admin/ ideation/templates/admin/
git commit -m "feat(admin): migrate latest/review/re-ideate views to Unfold"
```

---

## Task 9 — Migrate ingestion templates

**Files:**
- Modify: `ingestion/templates/admin/ingestion/operations.html`
- Modify: `ingestion/templates/admin/ingestion/filter_labeling.html`
- Modify: `ingestion/templates/admin/ingestion/filter_eval_history.html`

- [ ] **Step 9.1 — Overwrite `ingestion/templates/admin/ingestion/operations.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-1">Ingestion operations</h1>
<p class="text-sm text-slate-600 dark:text-slate-400 mb-6">
  Per-source checkpoint state and one-click triggers. Actions are enqueued via
  Celery; the worker logs progress to <code class="font-mono text-xs">docker compose logs celery_worker</code>.
</p>

{% if rows %}
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Source</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Last item posted</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Last run</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Items seen</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Opportunities</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for row in rows %}
          <tr>
            <td class="px-4 py-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{{ row.source }}</td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 whitespace-nowrap">{{ row.last_item_posted_at|default:"—" }}</td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 whitespace-nowrap">{{ row.last_run_at|default:"never" }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.items_seen }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ row.opportunities_found }}</td>
            <td class="px-4 py-2 text-sm whitespace-nowrap">
              <form method="post" action="/admin/ingestion/operations/{{ row.source }}/ingest/" class="inline mr-3">
                {% csrf_token %}
                <button type="submit" class="inline-flex items-center rounded-md bg-blue-600 hover:bg-blue-700 px-2.5 py-1 text-xs font-medium text-white shadow-sm">Run incremental</button>
              </form>
              <form method="post" action="/admin/ingestion/operations/{{ row.source }}/backfill/" class="inline-flex items-center gap-2">
                {% csrf_token %}
                <label class="text-xs text-slate-600 dark:text-slate-400">Backfill
                  <input type="number" name="days" value="30" min="1" max="365"
                         class="ml-1 w-16 rounded-md border-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 text-xs px-2 py-0.5">
                  days
                </label>
                <button type="submit" class="inline-flex items-center rounded-md bg-slate-600 hover:bg-slate-700 px-2.5 py-1 text-xs font-medium text-white shadow-sm">Go</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
{% else %}
  {% include "admin/_ux/_empty_state.html" with title="No source adapters registered" body="Register an adapter under ingestion/adapters/ to see it here." %}
{% endif %}

<h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mt-10 mb-3">Notes</h2>
<ul class="space-y-2 text-sm text-slate-600 dark:text-slate-400">
  <li><strong class="text-slate-900 dark:text-slate-100">Incremental</strong> pulls items posted after the source's checkpoint. Hourly Celery Beat already runs this automatically; the button is for ad-hoc kicks.</li>
  <li><strong class="text-slate-900 dark:text-slate-100">Backfill</strong> pulls a fresh N-day window and dedups against existing <code class="font-mono text-xs">ClusterItem</code> rows so reruns are safe. Costs roughly $0.002/item (~$1.00 per 30 days of Ask HN).</li>
</ul>
{% endblock %}
```

- [ ] **Step 9.2 — Overwrite `ingestion/templates/admin/ingestion/filter_labeling.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-2">Filter eval — labeling</h1>
<p class="text-sm text-slate-700 dark:text-slate-300 mb-6">
  <strong>{{ labeled_count }}</strong> items labeled ·
  <strong>{{ pending_count }}</strong> production classifications awaiting human review.
</p>

<div class="rounded-lg border border-dashed border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-900 p-5">
  <p class="font-semibold text-slate-900 dark:text-slate-100 mb-2">Placeholder UI</p>
  <p class="text-sm text-slate-700 dark:text-slate-300 mb-3">{{ todo }}</p>
  <p class="text-sm text-slate-700 dark:text-slate-300 mb-1">Intended UX:</p>
  <ul class="list-disc pl-6 space-y-1 text-sm text-slate-600 dark:text-slate-400">
    <li>One classification card at a time, source + content visible.</li>
    <li>Keyboard shortcuts — <kbd class="px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-xs font-mono">Y</kbd> yes · <kbd class="px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-xs font-mono">N</kbd> no · <kbd class="px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-xs font-mono">A</kbd> ambiguous · <kbd class="px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-xs font-mono">?</kbd> adversarial.</li>
    <li>Model verdict + reason hidden behind a "show model" toggle, off by default during fresh labeling, on for disagreement review.</li>
    <li>Optional reasoning note (single text field).</li>
    <li>Progress counter persistent at the top.</li>
    <li>Submit auto-advances to the next pending classification.</li>
  </ul>
</div>
{% endblock %}
```

- [ ] **Step 9.3 — Overwrite `ingestion/templates/admin/ingestion/filter_eval_history.html`**

```django
{% extends "unfold/layouts/base_simple.html" %}

{% block content %}
<h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-4">Filter eval — history</h1>

{% if latest %}
  <section class="mb-8 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2">Latest run · {{ latest.run_at }}</h2>
    <p class="text-sm text-slate-600 dark:text-slate-400 mb-3">
      Model: <code class="font-mono text-xs">{{ latest.model }}</code> ·
      Prompt: <code class="font-mono text-xs">{{ latest.prompt_hash|slice:":12" }}</code> ·
      Items: {{ latest.eval_set_size }}
    </p>
    <dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
      <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
        <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Precision</dt>
        <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ latest.precision|floatformat:3 }}</dd>
      </div>
      <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
        <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Recall</dt>
        <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ latest.recall|floatformat:3 }}</dd>
      </div>
      <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
        <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">F1</dt>
        <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">{{ latest.f1|floatformat:3 }}</dd>
      </div>
      <div class="rounded-md border border-slate-200 dark:border-slate-700 p-3">
        <dt class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Total cost</dt>
        <dd class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">${{ latest.total_cost_usd }}</dd>
      </div>
    </dl>
    <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-1">Per-tier breakdown</h3>
    <pre class="p-3 rounded bg-slate-50 dark:bg-slate-800 text-xs font-mono text-slate-800 dark:text-slate-100 overflow-auto">{{ latest.metrics_by_tier }}</pre>
  </section>
{% else %}
  <div class="mb-8 rounded-md bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
    No eval runs yet. Trigger one via the <code class="font-mono text-xs">run_filter_eval</code> Celery task once the classifier is wired up.
  </div>
{% endif %}

<h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">All runs</h2>
{% if runs %}
  <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
    <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
      <thead class="bg-slate-50 dark:bg-slate-800">
        <tr>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Run at</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Prompt hash</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Model</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Precision</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Recall</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">F1</th>
          <th class="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Cost</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
        {% for run in runs %}
          <tr>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300 whitespace-nowrap">{{ run.run_at }}</td>
            <td class="px-4 py-2 text-sm"><code class="font-mono text-xs">{{ run.prompt_hash|slice:":12" }}</code></td>
            <td class="px-4 py-2 text-sm text-slate-700 dark:text-slate-300">{{ run.model }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ run.precision|floatformat:3 }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ run.recall|floatformat:3 }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">{{ run.f1|floatformat:3 }}</td>
            <td class="px-4 py-2 text-sm tabular-nums text-slate-700 dark:text-slate-300">${{ run.total_cost_usd }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
{% else %}
  {% include "admin/_ux/_empty_state.html" with title="No eval runs" body="Trigger a filter eval via the Celery task." %}
{% endif %}
{% endblock %}
```

- [ ] **Step 9.5 — Run ingestion ops tests**

Run: `make test -- tests/test_ingestion_operations.py -v`
Expected: all pass.

- [ ] **Step 9.6 — Commit**

```bash
git add ingestion/templates/admin/
git commit -m "feat(admin): migrate ingestion ops/labeling/eval-history to Unfold"
```

---

## Task 10 — Run the full quality gate

**Files:** none (verification only)

- [ ] **Step 10.1 — Lint**

Run: `make lint`
Expected: pass.

- [ ] **Step 10.2 — Format check**

Run: `make format`
Expected: zero reformats (anything ruff fixes must be committed before continuing).

- [ ] **Step 10.3 — Typecheck**

Run: `make typecheck`
Expected: pass.

- [ ] **Step 10.4 — Discipline checks**

Run: `make discipline && make validate-prompts && make check-migrations`
Expected: all pass. `check-migrations` should report no pending migrations (we didn't change models).

- [ ] **Step 10.5 — Full test suite**

Run: `make test`
Expected: all pass.

- [ ] **Step 10.6 — Coverage stays at or above the gate**

Run: `make coverage`
Expected: passes the `--cov-fail-under` gate. Do **not** call `make coverage-ratchet`; we want the existing gate respected, not raised.

- [ ] **Step 10.7 — Commit any fixup changes from lint/format**

If steps 10.1–10.4 produced auto-fixes:

```bash
git add -A
git commit -m "chore(admin): lint/format fixups after Unfold migration"
```

If there are no fixups, skip the commit.

---

## Task 11 — Playwright validation sweep

**Files:** none persistent; uses Playwright MCP tools to drive a browser.

**Auth note:** the loop needs a superuser session. The existing DB has three superusers. Get one's username from the shell:

```bash
docker compose exec -T web python -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).first().username)"
```

For password, ask the human running this loop — never embed a password in the plan or commit it.

- [ ] **Step 11.1 — Open the login page and authenticate**

Use `mcp__plugin_playwright_playwright__browser_navigate` to `http://localhost:8000/admin/login/`.
Use `mcp__plugin_playwright_playwright__browser_fill_form` to fill username + password.
Use `mcp__plugin_playwright_playwright__browser_click` on the submit button.
Verify with `mcp__plugin_playwright_playwright__browser_snapshot` that the URL is now `/admin/` and shows the triage queue.

- [ ] **Step 11.2 — Screenshot each target page**

Drive Playwright through these URLs, taking a viewport screenshot of each:

| # | URL | File |
|---|---|---|
| 1 | `/admin/` | `/tmp/admin-01-index.png` |
| 2 | `/admin/clusters/cluster/` | `/tmp/admin-02-clusters-list.png` |
| 3 | `/admin/clusters/cluster/<first_id>/change/` | `/tmp/admin-03-cluster-detail.png` |
| 4 | `/admin/agents/agentrun/` | `/tmp/admin-04-runs-list.png` |
| 5 | `/admin/agents/run/<first_run_id>/trajectory/` | `/tmp/admin-05-trajectory.png` |
| 6 | `/admin/agents/cost-dashboard/` | `/tmp/admin-06-cost.png` |
| 7 | `/admin/ingestion/operations/` | `/tmp/admin-07-ingestion-ops.png` |
| 8 | `/admin/investigations/latest/` | `/tmp/admin-08-investigations-latest.png` |
| 9 | `/admin/ideation/latest/` | `/tmp/admin-09-ideation-latest.png` |
| 10 | `/admin/clusters/clustermergeproposal/` | `/tmp/admin-10-merge-proposals.png` |

After each navigate, capture console messages with `mcp__plugin_playwright_playwright__browser_console_messages`. Any `error`-level message is a failure to fix before continuing.

Get `<first_id>` and `<first_run_id>` from shell:

```bash
docker compose exec -T web python -c "from clusters.models import Cluster; from agents.models import AgentRun; print('cluster', Cluster.objects.first().id); print('run', AgentRun.objects.first().id)"
```

- [ ] **Step 11.3 — Inspect screenshots inline**

Use Read on each `/tmp/admin-NN-*.png` file. Visual checklist per page:

- Sidebar visible, "Triage queue" / "Latest investigations" / etc. listed under "Dashboards".
- Header reads "Pain-Miner — Operational dashboards".
- Dark mode by default; theme toggle visible in top bar.
- List pages: rows hover-highlight, no horizontal scroll, status badges visible.
- Trajectory: code blocks rendered with monospace, JSON pretty-printed, dark surface.
- Empty states (where applicable): centered card with title + body, no "0 results" stub.

- [ ] **Step 11.4 — Iterate**

For each visual defect: identify the template/partial responsible, edit, restart the web container (`docker compose restart web` — not needed for template-only changes since Django auto-reloads in dev, but useful if you edited Python), reload the page in Playwright, re-screenshot.

Repeat until the checklist in 11.3 passes for all 10 pages with zero console errors.

- [ ] **Step 11.5 — Commit any template fixups from the sweep**

```bash
git add -A
git commit -m "fix(admin): Playwright-driven UX fixups"
```

If no fixups were needed, skip the commit.

---

## Task 12 — Final review

**Files:** none (verification only)

- [ ] **Step 12.1 — Full test + coverage one more time**

Run: `make test && make coverage`
Expected: pass.

- [ ] **Step 12.2 — Git history sanity check**

Run: `git log --oneline main..HEAD`
Expected: 8–10 commits, one per logical step (install, site swap, modeladmin swap, template tags, partials, triage, agents templates, latest/review templates, ingestion templates, optional fixups).

- [ ] **Step 12.3 — Skim the diff once with fresh eyes**

Run: `git diff main..HEAD --stat`
Look for: any file changed that shouldn't have been (models, migrations, prompts, settings outside of `INSTALLED_APPS`/`UNFOLD`). Any surprise → investigate.

- [ ] **Step 12.4 — Open a PR**

Use `gh pr create` per the standard recipe. Title: `feat(admin): replace vanilla admin with django-unfold + UX wins`. Body summarises the spec.

---

## Verification Summary

When the plan completes:

- ✅ Admin chrome looks modern (Unfold dark theme, sidebar nav, slate palette).
- ✅ All 6 ModelAdmin classes use `unfold.admin.ModelAdmin`.
- ✅ All 13 custom templates extend `unfold/layouts/base_simple.html`.
- ✅ Clickable rows on triage, latest investigations, latest ideations.
- ✅ Trajectory page renders tool I/O as pretty-printed JSON in styled code blocks.
- ✅ Status badges replace plain text on cluster/agentrun/investigation/ideation rows.
- ✅ Long titles wrap with full text in `title` tooltip.
- ✅ Sticky table headers on triage and other large lists.
- ✅ Empty states render where queues are empty.
- ✅ All existing admin tests pass without weakening assertions.
- ✅ Lint, typecheck, discipline, prompts validator, migrations check all pass.
- ✅ Coverage gate holds.
- ✅ Playwright sweep clean across all 10 target pages.
