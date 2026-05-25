# Ideation Agent — Design

**Status:** Draft, awaiting implementation
**Date:** 2026-05-24
**Author:** Facundo (with Claude)

## 1. Why

The investigation agent stops at "this is a real pain, here are competitors, here are differentiators in principle." It does not say "here are three specific products you could build, with scope and effort." That cognitive step — the bridge between *interesting opportunity* and *decide whether to spend time on it* — currently happens entirely in the human's head, with no tool support and no captured artifact.

This spec introduces a **second agentic step** that runs on demand after an investigation is promoted. It takes the investigation brief as input and produces a structured ideation artifact: three concept variants, each with competitive landscape, MVP scope, kill criteria, fit-to-builder, and a first validation test.

## 2. Architecture

A new `ideation/` Django app, parallel to `investigations/`. The investigation pipeline is unchanged in PR1 — ideation is additive.

```
[cluster] → [investigation agent] → [brief] → [admin triage] →
                                                  ├─ reject / stale
                                                  └─ promote ─→ [ideation agent triggered] →
                                                                  [ideation produced] → [admin ideation review] →
                                                                                          ├─ accept (label only)
                                                                                          └─ reject
```

The ideation agent reuses the existing agent loop machinery: `AgentRun` / `AgentStep` / `AgentEvent` rows, prompt loader, tool registry, cost tracking, trajectory replay. Only the prompt, tool palette, input snapshot, output schema, and persistence target differ.

**Module boundary** (per AGENTS.md §10): only `ideation/orchestrator.py` writes `Ideation` rows. The ideation agent reads investigation briefs + cluster items and writes nothing else.

### 2.1 Implementation reality: orchestrator coupling

The existing `agents/orchestrator.py` `start_run()` is cluster-centric — it requires a `Cluster` and persists `AgentRun.cluster` FK. The existing `agents/loop.py` is investigation-specific in places (imports `Investigation`, intercepts `record_brief`).

Three options for handling this:

- **A. Generalize orchestrator + loop to be agent-agnostic.** Extract investigation-specific bits into `investigations/`. Largest churn, cleanest end state.
- **B. Keep orchestrator cluster-centric; add a sibling `ideation/orchestrator.py` that calls into shared helpers.** `AgentRun.cluster` for an ideation run points at `investigation.cluster` (cluster is still ground truth, no schema change). Investigation snapshot lives in `config_snapshot["ideation_input"]`. Loop branches on `agent_name`.
- **C. Duplicate orchestrator + loop entirely in `ideation/`.** Fastest, most duplication.

**Decision: B.** No `AgentRun` schema change, no migration on existing data, the cluster FK stays meaningful (every run is grounded in a cluster). Loop branching on `agent_name` is honest about the asymmetry between the two agents.

Concrete shape:
- `agents/orchestrator.py` gains a small generalization: `start_run(cluster_id, agent_name, ..., extra_snapshot=None)` where `extra_snapshot` is merged into `config_snapshot`. Existing callers pass `extra_snapshot=None`.
- `ideation/orchestrator.py` exposes `start_ideation(investigation_id, guidance="")` — it loads the investigation, snapshots the brief, calls `agents.orchestrator.start_run(cluster_id=inv.cluster_id, agent_name="ideation", extra_snapshot={"ideation_input": {brief snapshot, guidance}})`.
- `agents/loop.py` branches on `run.agent_name`: investigation path (existing) vs ideation path (new). The ideation path intercepts `record_ideation` analogously to how `record_brief` is intercepted today.

If the loop branching grows ugly, refactor in a follow-up — but ship B first.

## 3. Data model

New model in `ideation/models.py`:

```python
class IdeationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_REVIEW = "awaiting_review", "Awaiting review"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    STALE = "stale", "Stale"


class Ideation(models.Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    investigation = ForeignKey(Investigation, on_delete=CASCADE, related_name="ideations", db_index=True)

    status = CharField(max_length=24, choices=IdeationStatus.choices, default=IdeationStatus.DRAFT)
    guidance = TextField(blank=True, default="")  # human steering on re-ideate; "" on first run

    output = JSONField(default=dict)
    output_schema_version = CharField(max_length=16, default="1.0")

    primary_run = ForeignKey(AgentRun, null=True, on_delete=SET_NULL, related_name="primary_ideations")

    human_decision = JSONField(null=True, blank=True)  # {"note": str, ...}
    decided_by_user = ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=SET_NULL, related_name="ideations_decided")
    decided_at = DateTimeField(null=True, blank=True)

    created_at = DateTimeField(auto_now_add=True, db_index=True)
    updated_at = DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["investigation", "-created_at"]),
            models.Index(fields=["status"]),
        ]
```

**Cardinality:** one investigation → many ideations. "Latest ideation" is `created_at DESC LIMIT 1`. No `superseded_by` / `parent_ideation` — additive change later if lineage queries become necessary.

**No coupling to investigation state:** accepting an ideation does NOT flip `Investigation.build_status`. Accept/reject is label-only for eval (decision #11).

## 4. Output schema (`output_schema_version: "1.0"`)

```json
{
  "schema_version": "1.0",
  "investigation_id": "<uuid>",
  "guidance": "",
  "generated_at": "<iso8601>",
  "concepts": [
    {
      "name": "AgentCockpit",
      "bet_axis": "minimal_scope",
      "one_liner": "Operator GUI for non-technical AI-agent power users",
      "core_features": ["..."],
      "explicitly_not_included": ["..."],
      "buyer": "Marketing agency founder running OpenClaw in production",
      "rough_pricing_hypothesis": "$49-99/mo per team",
      "competitive_landscape": [
        {
          "name": "Langfuse",
          "url": "https://langfuse.com/",
          "positioning": "Engineering observability for LLM apps",
          "overlap": "Partial — observability layer, but developer-focused",
          "threat_level": "low",
          "evidence": "Pricing page targets dev teams; UI is trace-inspection oriented"
        }
      ],
      "mvp_scope": {
        "build_size": "S",
        "build_estimate_assumptions": "One full-time builder, no prior orchestration work, integrating against one agent framework",
        "minimum_features_for_test": ["..."],
        "explicitly_deferred_to_v2": ["..."]
      },
      "first_validation_test": "Build the approval-queue-only version against OpenClaw, give it to the original HN poster, ask if they'd pay $49/mo as-is",
      "kill_criteria": [
        "Original poster declines at $19/mo",
        "We find 3+ existing tools doing exactly this in the first 4 hours of search"
      ],
      "fit_to_builder": {
        "distribution_fit": "HN + niche subreddits reach this buyer; no paid ads needed for first 20 users",
        "skill_fit": "Solo full-stack build, no ML infra needed",
        "capital_fit": "$0 marketing budget viable; hosting <$50/mo"
      }
    }
    // exactly 3 concepts, three distinct bet_axis values
  ],
  "ideation_notes": "All three concepts assume the operator is technical enough to run agents but not technical enough to integrate them well. If that segment is smaller than the HN post suggests, none of these survive — distribution risk dominates execution risk."
}
```

**Validation:** a Pydantic model in `ideation/schemas.py` mirrors this and is used to (a) generate the `record_ideation` tool's input schema and (b) validate at persistence time. Mirrors how `investigations/schemas.py` works for briefs.

**`build_size` values:** `"S"` (weekend / a few days), `"M"` (a couple of weeks), `"L"` (a month-plus). The real content is in `build_estimate_assumptions`, per decision #2.

## 5. Prompt & tools

### 5.1 Prompt files

`prompts/ideation/system.md` and `prompts/ideation/procedural.md` — matching investigation's two-file pattern. Frontmatter on each:

```yaml
---
schema_version: "1.0"
description: <one line>
---
```

The agent loader (`agents.prompts.get_prompts_for_agent`) discovers files under `prompts/<agent_name>/` automatically — no code change needed for prompt discovery.

### 5.2 Bet axis menu (in `procedural.md`)

Three concepts per ideation, three distinct axes from this menu:

- `aggressive_scope` — full control plane
- `minimal_scope` — single-feature wedge
- `different_buyer` — same pain, different decision-maker
- `open_source_vs_saas` — distribution model bet
- `self_serve_vs_sales_led` — motion bet
- `horizontal_vs_vertical` — generic platform vs niche
- `consumer_vs_team` — single-user vs collaborative
- `tool_vs_workflow` — point tool vs end-to-end

The menu is in the prompt, not in code — it's a learnable artifact, edit-and-commit per the prompts-in-git pattern. The Pydantic schema's `bet_axis` field is `str` (not Enum) so adding axes doesn't require a code change. Validation that the three axes are distinct happens in the schema validator.

### 5.3 Tool palette

Add `"ideation"` entry to `AGENT_TOOLSETS` in `agents/tools/__init__.py`:

```python
"ideation": [
    "query_cluster",            # source material per decision #3
    "web_search",
    "fetch_url",
    "search_hacker_news",
    "fetch_hn_item",
    "query_known_competitors",
    "query_trustmrr",
    "record_ideation",          # NEW — terminal tool, analogous to record_brief
],
```

All tools other than `record_ideation` already exist in the registry. Only `record_ideation` is new (new entry in `agents/tools/stubs.py` registering the `Ideation` Pydantic schema as input, intercepted by the loop similarly to `record_brief`).

**No specialized tools in v1.** `check_github_repo` and `check_pricing_page` are explicitly deferred — `fetch_url` covers both for now.

### 5.4 Model setting

Add `MODEL_IDEATION = env("MODEL_IDEATION", default="claude-sonnet-4-6")` in `config/settings.py`. Extend `_model_selection()` in `agents/orchestrator.py` with the third branch. (Mirror investigation's model in the default — same generative capacity makes sense; tune separately later if signal warrants.)

### 5.5 No budget enforcement in v1

Use the default `BudgetConfig`. Watch costs on `/admin/agents/cost-dashboard/` for the first ~20 runs (decision #9). Add a per-agent budget setting in a follow-up if costs surprise.

## 6. Trigger & review surface

### 6.1 Triggers

**Promote → ideate (initial run):**
- Modify the existing investigation admin promote action so that, in addition to setting `status=PROMOTED`, it calls `ideation.orchestrator.start_ideation(investigation_id=..., guidance="")`. Single action — promotion and ideation are conceptually one step.
- A `draft` `Ideation` row is created synchronously inside the admin action; the Celery task fills `output` and flips to `awaiting_review`.

**Re-ideate (subsequent runs):**
- Investigation detail page gains a "Re-ideate with guidance" button + textarea (optional `guidance` string).
- Submitting calls `start_ideation(investigation_id=..., guidance=text)`.

### 6.2 Admin review surface — `/admin/ideation/ideation/<id>/`

- Header: investigation link, status, created_at, `guidance` if any, primary_run link (trajectory replay)
- Three concept cards rendering the full schema (competitive landscape table, kill criteria list, fit-to-builder block, mvp scope expanded)
- Accept / Reject buttons + free-text `human_decision.note` textarea (label-only per decision #11)
- Re-ideate button + guidance textarea (mirrors the one on the investigation page)

A list view at `/admin/ideation/ideation/` with filters on status, created_at, and a search on investigation title.

## 7. Migration sequencing

### PR1 (additive, ships first)

- New `ideation/` app (`models.py`, `schemas.py`, `orchestrator.py`, `admin.py`, `apps.py`, `tasks.py`, `migrations/`, `templates/`)
- `Ideation` model + initial migration
- `prompts/ideation/system.md` + `prompts/ideation/procedural.md`
- New tool `record_ideation` registered in `agents/tools/stubs.py`
- `AGENT_TOOLSETS["ideation"]` entry
- `MODEL_IDEATION` setting + `_model_selection` branch
- `start_run` generalized to accept `extra_snapshot` (existing callers pass `None`)
- `agents/loop.py` branches on `agent_name`: investigation path unchanged; new ideation path intercepts `record_ideation`, persists to `Ideation.output`
- Admin: list, detail, accept/reject, re-ideate actions
- Promote button on investigation admin enqueues ideation alongside setting `PROMOTED`
- Tests: orchestrator wiring, prompt frontmatter validates, output schema validates (three distinct axes enforced), record_ideation persistence, admin actions, Celery task happy-path
- `make discipline`, `make typecheck`, `make validate-prompts`, `make check-migrations` clean
- Coverage ratchet bumps per existing rules (no manual lowering)

### PR2 (cleanup, ships after ~1 week of real ideation use, per decision #10)

- Investigation prompt drops `recommended_next_step` and `differentiators` (per decision #1, deferred to PR2 per decision #10)
- Investigation brief schema bumps from `"1.0"` to `"1.1"` — those fields disappear from validation
- Brief admin templates drop the fields from display
- Decision on existing rows: keep historical briefs as-is (schema_version recorded per row enables trajectory replay against the old shape); only new briefs use 1.1
- Tests updated

If after a week of ideations you still want those investigation fields for triage, **PR2 does not ship** — decision #1 reverses cheaply by simply not opening the second PR.

## 8. Evaluation

Per decision rationale (see brainstorm conversation), ideation eval is rubric-based, not metric-based:

- **Action signal:** did any concept get accepted (binary per ideation)?
- **Competitive recall:** when a human reviewer adds a competitor manually, that's a recall miss to track over time.
- **Build size calibration:** backfill judgment after the fact, as builds happen.
- **Scope discipline:** are `explicitly_not_included` / `explicitly_deferred_to_v2` real tradeoffs or strawmen?
- **Variance:** if every ideation produces three concepts that converge to the same shape (e.g., always three SaaS at $49/mo), that's a lack-of-variance failure mode worth flagging.

All captured in `Ideation.human_decision` as free-text notes for now; structure later if the rubric stabilizes.

## 9. Out of scope for v1

- `check_github_repo` / `check_pricing_page` specialized tools — defer until `fetch_url` proves insufficient
- `parent_ideation` lineage FK — defer until lineage queries are needed
- Coupling accept → investigation `build_status` — defer until acceptance behavior is observed
- Per-agent budget enforcement — defer until cost dashboard shows a problem
- Automatic re-ideation (e.g., scheduled, or triggered by cluster changes) — manual-only in v1
- A separate eval dashboard for ideations — use the existing trajectory + cost dashboards

## 10. Open implementation questions

These do not block PR1 but warrant a note:

1. **Loop branching vs separate loop file.** If `agents/loop.py` grows visibly ugly with the `agent_name` branch, extract investigation-specific code into `investigations/loop.py` and ideation-specific into `ideation/loop.py` with shared helpers in `agents/loop_helpers.py`. Decide after PR1 lands.
2. **`record_ideation` interception pattern.** Currently `record_brief` is intercepted by name string in the loop. A small refactor would let any tool declare itself "terminal" and have the loop intercept generically. Worth doing if a third terminal tool ever appears; YAGNI until then.
3. **Investigation `cluster_snapshot` vs ideation `ideation_input`.** Both are "the immutable input to this run." A future refactor could unify them under a generic `run_input` snapshot. Not now.
