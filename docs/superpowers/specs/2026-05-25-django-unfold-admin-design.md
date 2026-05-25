# Django Unfold admin — design

**Status:** approved (brainstorm), pending plan
**Date:** 2026-05-25
**Driver:** Replace the vanilla Django admin chrome with [django-unfold](https://unfoldadmin.com/), and use that migration as the lever for a targeted UX pass (clickable rows, legible agent output, status badges, sticky headers, empty states, wrapped truncation). Validated iteratively with Playwright screenshots.

## Goals

1. Adopt Unfold as the admin chrome with **dark default + light toggle** and a "Pain-Miner" brand palette.
2. All 13 custom admin templates render inside Unfold's layout, not vanilla `admin/base_site.html`.
3. Concrete UX wins land in the same change:
   - Whole-row click navigates to the change form on every list page that has one.
   - Agent trajectory `tool_input` / `tool_output_summary` render in a styled, pretty-printed monospace code block.
   - Status fields render as colored pill badges (`failed`, `completed`, `running`, `pending`, etc.).
   - Long titles/URLs wrap with `truncate` + native `title` tooltip showing the full string.
   - Triage and other large list pages get sticky table headers + Unfold dense mode.
   - Empty queues show a real empty state, not a "0 results" stub.
   - Action buttons use Unfold's button component (`unfold/components/button.html`).
4. Existing admin tests (`test_cluster_triage.py`, `test_ingestion_operations.py`, `test_investigations_latest.py`, `test_ideation.py`) keep passing without modification beyond unavoidable template-path changes.
5. Playwright validation pass: a manual screenshot loop covering the 10 key pages, run as we iterate.

## Non-goals

- No layout restructuring of the custom dashboards (cards/sections/etc.). Same content, better chrome.
- No new functionality (no new admin views, no new model fields, no new endpoints).
- No CSS extraction into a separate static bundle. Inline Tailwind utility classes via Unfold's components are sufficient.
- No `pytest-playwright` regression suite. Manual screenshot iteration only.
- No migration of `django-celery-beat`'s admin (vendor-owned; it'll inherit Unfold chrome automatically since it uses `admin.site`).

## Architecture

### Package + settings

Add to `pyproject.toml` `dependencies` (runtime, not dev):

```toml
"django-unfold>=0.69,<0.70",
```

Pin a minor range — Unfold's minor bumps occasionally rename `UNFOLD` config keys, and we want lockfile predictability.

In `config/settings.py`:

```python
INSTALLED_APPS = [
    "unfold",                       # MUST precede the AdminConfig
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "core.admin_apps.PainMinerAdminConfig",
    # ... existing apps ...
]

UNFOLD = {
    "SITE_TITLE": "Pain-Miner",
    "SITE_HEADER": "Pain-Miner",
    "SITE_SUBHEADER": "Operational dashboards",
    "SITE_URL": "/admin/",
    "THEME": "dark",                # dark by default; user can toggle to light (toggle persists in localStorage)
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {                # slate
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
                    {"title": "Triage queue",            "icon": "playlist_play",      "link": "/admin/clusters/triage/"},
                    {"title": "Latest investigations",   "icon": "search",             "link": "/admin/investigations/latest/"},
                    {"title": "Latest ideations",        "icon": "lightbulb",          "link": "/admin/ideation/latest/"},
                    {"title": "Ingestion ops",           "icon": "cloud_download",     "link": "/admin/ingestion/operations/"},
                    {"title": "Cost dashboard",          "icon": "payments",           "link": "/admin/agents/cost-dashboard/"},
                    {"title": "Prompts",                 "icon": "description",        "link": "/admin/agents/prompts/"},
                    {"title": "Filter labeling",         "icon": "label",              "link": "/admin/ingestion/filter-labeling/"},
                ],
            },
        ],
    },
}
```

Note: Unfold's sidebar `navigation` config replaces the synthetic "Dashboards" section currently injected by `PainMinerAdminSite.get_app_list()`. That injection is **deleted** from `admin_site.py` — Unfold renders the navigation list directly, and we get Material Symbol icons for free.

### Admin site swap

`core/admin_site.py` — change the base class only:

```python
from unfold.sites import UnfoldAdminSite

class PainMinerAdminSite(UnfoldAdminSite):
    # site_header / site_title moved to UNFOLD dict; remove duplicates
    index_title = "Operational dashboards"

    def index(self, request, extra_context=None):
        # unchanged — keeps landing on the triage queue
        ...

    def get_urls(self):
        # unchanged — custom URL routes preserved verbatim
        ...

    # get_app_list override DELETED — Unfold sidebar nav replaces it
```

### ModelAdmin swap

For every file in `{agents,clusters,ideation,ingestion,investigations}/admin.py`:

```python
# before
from django.contrib import admin
class FooAdmin(admin.ModelAdmin): ...
class FooInline(admin.TabularInline): ...

# after
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline, StackedInline
class FooAdmin(ModelAdmin): ...
class FooInline(TabularInline): ...
```

`@admin.register(Foo)` and all attributes (`list_display`, `list_filter`, `readonly_fields`, custom view methods, `get_urls`, etc.) are preserved. Unfold reads them via the same API.

### Custom template migration

All 13 custom templates change their `extends` and adopt Unfold's component fragments. Migration table:

| Template | Base change | Notable interior changes |
|---|---|---|
| `clusters/templates/admin/clusters/triage.html` | `unfold/layouts/base_simple.html` | Use `unfold/helpers/table` table classes; status → `_badge.html`; sticky header; clickable row; empty state |
| `clusters/templates/admin/clusters/cluster/change_list.html` | (already extends `admin/change_list.html` — keep, inherits Unfold automatically) | None |
| `agents/templates/admin/agents/trajectory.html` | `unfold/layouts/base_simple.html` | Wrap `<pre>` in `_codeblock.html`; pretty-print JSON via `core/templatetags/admin_ux.py:json_pretty`; status badge; budget bars use Unfold progress component |
| `agents/templates/admin/agents/cost_dashboard.html` | `unfold/layouts/base_simple.html` | Section headings → Unfold card pattern; tables → Unfold table classes |
| `agents/templates/admin/agents/prompt_inspector.html` | `unfold/layouts/base_simple.html` | Markdown body wrapped in `prose` container; metadata → small badges |
| `ingestion/templates/admin/ingestion/operations.html` | `unfold/layouts/base_simple.html` | Action buttons → `_button.html`; last-run status → badge; empty state |
| `ingestion/templates/admin/ingestion/filter_labeling.html` | `unfold/layouts/base_simple.html` | Form controls → Unfold form classes |
| `ingestion/templates/admin/ingestion/filter_eval_history.html` | `unfold/layouts/base_simple.html` | Same as triage table treatment |
| `investigations/templates/admin/investigations/latest.html` | `unfold/layouts/base_simple.html` | Clickable rows + badges + empty state |
| `investigations/templates/admin/investigations/investigation/review.html` | `unfold/layouts/base_simple.html` | Brief body → `prose`; verdict → badge |
| `ideation/templates/admin/ideation/latest.html` | `unfold/layouts/base_simple.html` | Clickable rows + badges + empty state |
| `ideation/templates/admin/ideation/ideation/review.html` | `unfold/layouts/base_simple.html` | Brief body → `prose`; verdict → badge |
| `ideation/templates/admin/ideation/ideation/re_ideate.html` | `unfold/layouts/base_simple.html` | Form controls → Unfold form classes |

### Shared UX components

Add three reusable template partials under `core/templates/admin/_ux/`:

- `_badge.html` — colored pill. Takes `{label}` and `{tone}` (`success` | `danger` | `warning` | `info` | `neutral`). Mapped to Tailwind classes via Unfold's color palette.
- `_codeblock.html` — wraps content in a styled `<pre>` with monospace, comfortable line-height, dark surface in dark mode, contained horizontal scroll, max-height with scroll inside.
- `_empty_state.html` — centered icon + heading + body + optional CTA link. Used wherever a queue might be empty.

Add **one** template tag library `core/templatetags/admin_ux.py`:

- `{% load admin_ux %}` `{{ value|json_pretty }}` — JSON-detect + pretty-print, falls back to `value` if not JSON.
- `{{ status|status_tone }}` — maps status strings (`completed`, `failed`, `running`, ...) to a `tone` for `_badge.html`.

Clickable rows are handled with **one** tiny inline pattern, not a JS library: each `<tr>` gets `data-href="..."` and a 5-line script in `unfold/layouts/base.html`'s `extra_scripts` block selects `tr[data-href]` and binds click → `window.location`. Re-used everywhere by passing `data-href` from the template. Document the pattern in `core/templates/admin/_ux/_clickable_rows.html` (a single `{% include %}`-able `<script>` tag, included once per page that has clickable rows).

### Discipline & boundaries

- **Module boundaries (AGENTS.md §10) untouched.** Only `admin.py` and `templates/admin/**` change. No new `AgentRun`/`AgentStep`/`AgentEvent` writes. No clustering changes. No adapter changes.
- **Imports stay top-of-file & absolute (`TID252`, `PLC0415`).** The two existing `noqa: PLC0415` deferred imports in `admin_site.py` stay justified (circular admin registration).
- **No new prompts.** Prompts dir untouched.
- **Exception discipline.** No new try/except. Unfold integration is pure presentation.
- **Coverage ratchet.** Migration is presentation-layer; coverage should not drop. If the ratchet rises naturally because admin views now hit more lines, we accept that. We do **not** add tests purely to satisfy the ratchet — the Playwright loop is the validation path.

## Data flow

No change. All custom views still call the same `ModelAdmin.*_view` methods and pass the same context dicts to templates. Only the templates' rendering and the chrome around them change.

## Error handling

No new error paths introduced. The `core/templatetags/admin_ux.py:json_pretty` filter must:

- Return the original value unchanged if it's not a `str`, or if `json.loads()` raises.
- Never raise; admin pages must never 500 because a `tool_input` field has malformed JSON.

This is the **only** new code path needing defensive behavior, and the failure mode is "fall through to raw text" — exactly what the current `<pre>{{ value }}</pre>` does today.

## Testing

### Automated (existing)

Run `make test` after each significant batch. The four admin-view tests already cover the page-render path:

- `tests/test_cluster_triage.py`
- `tests/test_ingestion_operations.py`
- `tests/test_investigations_latest.py`
- `tests/test_ideation.py`

They assert HTTP 200 + presence of key strings in the response body. They should pass unchanged. If a test asserts on a string that we restructure (e.g. a heading wording change), update the test to assert on the new wording.

### Manual Playwright loop

After each batch of template changes (roughly: chrome → list pages → custom dashboards → trajectory deep-polish), drive Playwright to:

1. Navigate to `http://localhost:8000/admin/login/` and authenticate using a known superuser (we'll use the existing `facundo` user; password supplied via env at run time, not committed).
2. Visit each target page, take a viewport screenshot, and snapshot console messages.
3. Inspect screenshots inline. Fix issues. Re-run.

Target pages (10):

| # | URL | What we're checking |
|---|---|---|
| 1 | `/admin/` (lands on triage) | Sidebar nav, header, brand, dark mode, sticky table header on scroll |
| 2 | `/admin/clusters/cluster/` | List page Unfold chrome, clickable rows, status badges |
| 3 | `/admin/clusters/cluster/<id>/change/` | Change form Unfold widgets, inline (ClusterItem) styling |
| 4 | `/admin/agents/agentrun/` | List + filter sidebar, status badges |
| 5 | `/admin/agents/run/<uuid>/trajectory/` | Code block legibility (pretty JSON), step badges, budget bars |
| 6 | `/admin/agents/cost-dashboard/` | Card sections, tables, no overflow |
| 7 | `/admin/ingestion/operations/` | Button styling, last-run badges, empty state for unrun sources |
| 8 | `/admin/investigations/latest/` | Clickable rows, verdict badges, empty state if zero |
| 9 | `/admin/ideation/latest/` | Same as latest investigations |
| 10 | `/admin/clusters/clustermergeproposal/` | Generic list page sanity check (no custom template) |

Stop conditions: no console errors on any page, no horizontal overflow, badges visible at intended colors in both themes, clickable rows work, trajectory code blocks readable.

## Rollout

Single PR, single branch. The change is presentation-only and reversible (revert one PR to return to vanilla admin). No flag, no staged rollout.

## Open questions deferred to plan-writing

- Exact Unfold version pin — check the latest stable on PyPI when the plan is written; the range above is a placeholder.
- Whether `unfold.contrib.import_export` is worth adding — only if we adopt `django-import-export` later; skip for now.
- Final Material Symbol icon names — the values listed are sensible defaults; we'll confirm each renders during the Playwright sweep.
- Whether celery-beat's admin templates render correctly under Unfold — verify during Playwright; if they break, add a targeted template override rather than rolling back the migration.

These are implementation-detail choices, not design choices, and belong in the plan.
