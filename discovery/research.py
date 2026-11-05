"""Fenced call accounting and normalized evidence persistence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from pydantic import Field

from discovery.connectors.base import ConnectorError, ResearchPage
from discovery.connectors.registry import get_connector
from discovery.errors import DiscoveryError, DiscoveryStop, OutcomeUnknown
from discovery.models import Discovery, DiscoveryTask, Evidence, ResearchOperation
from discovery.orchestrator import LostLease, check_lease
from discovery.schemas import Contract, DiscoveryConfig
from discovery.source_types import classify


class ResearchInput(Contract):
    connector: str
    action: Literal["search", "fetch"]
    query: str = ""
    target: str = ""
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Connector filters (see each connector's supported_filters). Search: "
            "country (ISO 3166-1 alpha-2, lower case, e.g. ar), language (ISO 639-1, "
            "e.g. es) and recency (day, week, month or year) on duckduckgo, bing "
            "(no recency) and search_auto; include_domains/exclude_domains on web_search."
        ),
    )
    cursor: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


def lease_token(task: DiscoveryTask) -> UUID:
    if task.lease_token is None:
        raise LostLease("Task is not leased")
    return task.lease_token


_BACKEND_SUFFIX = re.compile(r":backend:[^:]+$")


def research_slot(operation_key: str) -> str:
    """The quota slot of an operation: search_auto backends share their parent's."""
    return _BACKEND_SUFFIX.sub("", operation_key)


def _research_slots(task: DiscoveryTask) -> set[str]:
    # Local cooldown denials never reached a source. Retain their audit rows,
    # but do not let repeated denied calls exhaust the actual research budget.
    return {
        research_slot(key)
        for key in task.operations.filter(kind="research")
        .exclude(response__contains={"request_performed": False})
        .exclude(
            response__contains={
                "error_reason": "Shared connector rate limit; retry on a later call"
            }
        )
        .values_list("operation_key", flat=True)
    }


def research_calls_used(task: DiscoveryTask) -> int:
    """Research slots used this turn; a search_auto fallback pair is one slot."""
    return len(_research_slots(task))


def check_limits(task: DiscoveryTask) -> None:
    check_lease(task.pk, lease_token(task))
    discovery = Discovery.objects.get(pk=task.discovery_id)
    config = DiscoveryConfig.model_validate(discovery.config_snapshot)
    if (
        discovery.started_at
        and (timezone.now() - discovery.started_at).total_seconds() >= config.max_duration_s
    ):
        raise DiscoveryStop("duration_limit")
    if discovery.cost_used_usd >= config.max_cost_usd:
        raise DiscoveryStop("cost_limit")


def begin_operation(
    task: DiscoveryTask,
    key: str,
    kind: str,
    request: dict[str, Any],
    reserved_usd: Decimal,
    connector: str = "",
) -> ResearchOperation:
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=task.discovery_id)
        DiscoveryTask.objects.select_for_update().get(pk=task.pk)
        check_limits(task)
        existing = task.operations.filter(operation_key=key).first()
        if existing:
            if existing.request != request:
                raise DiscoveryError("Operation key reused for a different request")
            return existing
        config = DiscoveryConfig.model_validate(discovery.config_snapshot)
        max_calls = (
            config.max_model_calls_per_turn
            if kind == "model"
            else config.max_research_calls_per_turn
        )
        if kind == "research":
            slots = _research_slots(task)
            # A fallback backend joins its parent's slot rather than taking a new one.
            exhausted = research_slot(key) not in slots and len(slots) >= max_calls
        else:
            exhausted = kind == "model" and task.operations.filter(kind=kind).count() >= max_calls
        if exhausted:
            raise DiscoveryStop("turn_call_limit", f"Maximum {kind} calls reached")
        held = discovery.operations.exclude(state__in=["completed", "failed"]).aggregate(
            value=Sum("reserved_usd")
        )["value"] or Decimal("0")
        if discovery.cost_used_usd + held + reserved_usd > config.max_cost_usd:
            raise DiscoveryStop("cost_limit", "Insufficient remaining budget for the next call")
        operation = ResearchOperation.objects.create(
            discovery=discovery,
            task=task,
            operation_key=key,
            kind=kind,
            connector=connector,
            request=request,
            reserved_usd=reserved_usd,
        )
        turn = task.turn
        if kind == "model":
            turn.model_calls += 1
        elif kind == "research":
            turn.research_calls += 1
        turn.save(update_fields=["model_calls", "research_calls"])
        return operation


def complete_operation(
    task: DiscoveryTask,
    operation: ResearchOperation,
    result: dict[str, Any],
    *,
    enforce_limit: bool = True,
) -> None:
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=task.discovery_id)
        DiscoveryTask.objects.select_for_update().get(pk=task.pk)
        check_lease(task.pk, lease_token(task))
        operation.refresh_from_db()
        if operation.state == "completed":
            return
        amount = Decimal(str(result.get("cost_usd", "0")))
        if not amount.is_finite() or amount < 0:
            raise DiscoveryError("Provider supplied invalid cost accounting")
        operation.state = "completed"
        operation.response = result
        operation.cost_usd = amount
        usage = result.get("usage", {})
        operation.input_tokens = usage.get("prompt_tokens", 0)
        operation.output_tokens = usage.get("completion_tokens", 0)
        operation.cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        operation.completed_at = timezone.now()
        operation.save()
        discovery.cost_used_usd += amount
        discovery.save(update_fields=["cost_used_usd", "updated_at"])
    if enforce_limit and amount > operation.reserved_usd:
        raise DiscoveryStop("cost_limit", "Provider charge exceeded the reserved upper bound")


def _scope() -> str:
    # The public cache must not mix evidence read through different credentials.
    values = (settings.GITHUB_TOKEN, settings.STACKEXCHANGE_KEY, settings.TAVILY_API_KEY)
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:20]


def perform_research(task: DiscoveryTask, key: str, arguments: dict[str, Any]) -> dict[str, Any]:
    inp = ResearchInput.model_validate(arguments)
    if inp.connector != "search_auto":
        return _perform_research(task, key, arguments)
    config = DiscoveryConfig.model_validate(task.discovery.config_snapshot)
    if "search_auto" not in config.connectors or inp.action != "search":
        raise ValueError("Automatic search is not enabled or action is not search")
    get_connector("search_auto").validate_search(inp.query, inp.filters, inp.cursor, inp.limit)
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    applied: list[str] = []
    dropped: list[str] = []
    for backend in ("duckduckgo", "bing"):
        backend_key = f"{key}:backend:{backend}"
        supported = set(get_connector(backend).describe().supported_filters)
        applied = sorted(name for name in inp.filters if name in supported)
        # Recency is the only filter Bing cannot honour; never drop it silently.
        dropped = sorted(name for name in inp.filters if name not in supported)
        result = _perform_research(
            task,
            backend_key,
            {
                **arguments,
                "connector": backend,
                "filters": {name: inp.filters[name] for name in applied},
            },
            routed_backend=True,
        )
        attempts.append(
            {
                "connector": backend,
                "filters_applied": applied,
                "filters_dropped": dropped,
                "status": result["status"],
                "error_reason": result.get("error_reason"),
                "request_performed": result.get(
                    "request_performed", not result.get("cached", False)
                ),
                "cached": result.get("cached", False),
                "cost_usd": result.get("cost_usd", "0"),
            }
        )
        if result["status"] not in {"rate_limited", "timeout", "access_denied", "error"}:
            break
    return {
        **result,
        "schema_version": "1.1",
        "search_attempts": attempts,
        "requested_connector": "search_auto",
        "filters_applied": applied,
        "filters_dropped": dropped,
    }


def _perform_research(
    task: DiscoveryTask,
    key: str,
    arguments: dict[str, Any],
    *,
    routed_backend: bool = False,
) -> dict[str, Any]:
    inp = ResearchInput.model_validate(arguments)
    config = DiscoveryConfig.model_validate(task.discovery.config_snapshot)
    if inp.connector not in config.connectors and not (
        routed_backend
        and "search_auto" in config.connectors
        and inp.connector in {"duckduckgo", "bing"}
    ):
        raise ValueError("Connector is not enabled for this discovery")
    connector = get_connector(inp.connector)
    description = connector.describe()
    if inp.action not in description.capabilities:
        raise ValueError(f"{inp.connector} does not support {inp.action}")
    upper_bound = (
        description.search_cost_upper_bound_usd
        if inp.action == "search"
        else description.fetch_cost_upper_bound_usd
    )
    operation = begin_operation(
        task, key, "research", inp.model_dump(mode="json"), upper_bound, inp.connector
    )
    if operation.state == "completed":
        return operation.response
    if operation.state in {"started", "unknown"}:
        raise OutcomeUnknown("Research operation outcome unknown; reconcile before retrying")
    cache_key = (
        "discovery:research:"
        + hashlib.sha256(
            json.dumps(
                [description.id, description.version, inp.model_dump(mode="json"), _scope()],
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    # Local corpus changes with the DB and is cheap; only immutable captured
    # external results participate in the shared credential-scoped cache.
    cached = cache.get(cache_key) if inp.connector != "local_corpus" else None
    if cached is not None:
        page = ResearchPage.model_validate(cached)
        page.cost_usd = Decimal("0")
        page.cost_is_estimate = False
    else:
        interval = max(0, int(description.minimum_interval_seconds))
        target_scope = (
            (urlsplit(inp.target).hostname or "").lower() if inp.action == "fetch" else ""
        )
        throttle = f"discovery:throttle:{inp.connector}:{target_scope}:{_scope()}"
        if interval and not cache.add(throttle, interval, timeout=interval):
            result: dict[str, Any] = {
                "schema_version": "1.1",
                "status": "rate_limited",
                "request_performed": False,
                "documents": [],
                "retry_after_seconds": cache.get(throttle) or interval,
                "cost_usd": "0",
                "error_reason": "Shared connector rate limit; retry on a later call",
            }
            complete_operation(task, operation, result)
            return result
        check_limits(task)
        operation.state = "started"
        operation.save(update_fields=["state"])
        try:
            page = (
                connector.search(inp.query, inp.filters, inp.cursor, inp.limit)
                if inp.action == "search"
                else connector.fetch(inp.target)
            )
        except (ConnectorError, ValueError, OSError) as exc:
            operation.state = "unknown" if upper_bound else "failed"
            operation.error_summary = type(exc).__name__
            operation.save(update_fields=["state", "error_summary"])
            raise
        if page.status == "rate_limited" and page.retry_after_seconds:
            cooldown = min(max(page.retry_after_seconds, interval), 3600)
            cache.set(throttle, cooldown, timeout=cooldown)
        if page.status == "success" and inp.connector != "local_corpus":
            cache.set(cache_key, page.model_dump(mode="json"), description.cache_ttl_seconds)
    with transaction.atomic():
        Discovery.objects.select_for_update().get(pk=task.discovery_id)
        check_lease(task.pk, lease_token(task))
        documents = []
        for document in page.documents:
            typed = classify(document.connector, document.kind, document.url)
            metadata = {
                **document.metadata,
                "author": document.author,
                "source_type_rule": typed.rule,
                "source_type_rules_version": typed.rules_version,
            }
            origin = str(metadata.get("origin") or urlsplit(document.url).hostname or "")
            evidence = Evidence.objects.create(
                schema_version="1.1",
                discovery_id=task.discovery_id,
                turn=task.turn,
                operation=operation,
                connector=document.connector,
                connector_version=document.connector_version,
                external_id=document.external_id,
                url=document.url,
                title=document.title,
                content=document.text,
                kind=document.kind,
                origin=origin,
                source_type=typed.source_type,
                content_hash=document.content_hash,
                published_at=datetime.fromisoformat(document.published_at)
                if document.published_at
                else None,
                retrieved_at=datetime.fromisoformat(document.retrieved_at),
                metadata=metadata,
            )
            documents.append(
                {
                    **document.model_dump(mode="json"),
                    "evidence_id": str(evidence.pk),
                    "source_type": typed.source_type,
                    "origin": origin,
                    "text": document.text[:16000],
                    "context_truncated": len(document.text) > 16000,
                }
            )
        result = {
            **page.model_dump(mode="json"),
            "documents": documents,
            "cached": cached is not None,
        }
        complete_operation(task, operation, result, enforce_limit=False)
    if page.cost_usd > upper_bound:
        raise DiscoveryStop("cost_limit", "Connector charge exceeded its reservation")
    return result
