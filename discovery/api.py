"""Authenticated transport for discovery; orchestration owns all state changes."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from pydantic import ValidationError as SchemaValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from discovery.connectors.base import ConnectorConfigurationError
from discovery.connectors.registry import describe_connectors
from discovery.models import Discovery, DiscoveryMessage, Evidence
from discovery.orchestrator import DiscoveryConflict, cancel_discovery, start_discovery
from discovery.schemas import VerdictInput
from discovery.verdicts import VerdictNotAllowed, record_verdict


class DiscoveryStartSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=["1.0"], default="1.0")
    objective = serializers.CharField(max_length=12000)
    constraints = serializers.CharField(
        max_length=12000, required=False, default="", allow_blank=True
    )
    config = serializers.JSONField(required=False, default=dict)
    idempotency_key = serializers.CharField(max_length=200, required=False)

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise ValidationError({"unknown_fields": sorted(unknown)})
        return super().to_internal_value(data)

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise ValidationError("config must be an object")
        return value


def discovery_view(discovery: Discovery) -> dict:
    judge_required = bool(discovery.config_snapshot.get("judge_enabled", False))
    latest_proposal = discovery.proposals.order_by("-turn__number").first()
    latest_validation = (
        discovery.validations.filter(proposal=latest_proposal).order_by("-turn__number").first()
        if latest_proposal
        else None
    )
    judgments = [
        {
            "id": str(row.pk),
            "turn_number": row.turn.number,
            "proposal_id": str(row.proposal_id),
            "validation_id": str(row.validation_id),
            "verdict": row.verdict,
            "content": row.content,
        }
        for row in discovery.judgments.select_related("turn").order_by("turn__number")
    ]
    latest_judgment = judgments[-1] if judgments else None
    human_verdict = discovery.verdicts.select_related("recorded_by").last()
    judge_approved = bool(
        latest_proposal
        and latest_validation
        and latest_validation.verdict == "accept"
        and latest_judgment
        and latest_judgment["proposal_id"] == str(latest_proposal.pk)
        and latest_judgment["validation_id"] == str(latest_validation.pk)
        and latest_judgment["verdict"] == "approve"
        and latest_judgment["content"].get("worth_pursuing")
    )
    return {
        "schema_version": "1.2",
        "id": str(discovery.id),
        "objective": discovery.objective,
        "constraints": discovery.constraints,
        "status": discovery.status,
        "outcome": discovery.outcome,
        "stop_reason": discovery.stop_reason,
        "benchmark_tag": discovery.benchmark_tag,
        "benchmark_objective_id": discovery.benchmark_objective_id,
        "turns_completed": discovery.turns_completed,
        "cost_used_usd": str(discovery.cost_used_usd),
        "created_at": discovery.created_at,
        "started_at": discovery.started_at,
        "ended_at": discovery.ended_at,
        "config": discovery.config_snapshot,
        "execution": {
            "runtime": discovery.config_snapshot.get("runtime", "openrouter_batch"),
            "finder": {
                "provider": discovery.config_snapshot.get("finder_provider", "openrouter"),
                "model": discovery.config_snapshot.get("finder_model"),
            },
            "validator": {
                "provider": discovery.config_snapshot.get("validator_provider", "openrouter"),
                "model": discovery.config_snapshot.get("validator_model"),
            },
            "judge": {
                "enabled": judge_required,
                "provider": discovery.config_snapshot.get("judge_provider")
                if judge_required
                else None,
                "model": discovery.config_snapshot.get("judge_model") if judge_required else None,
            },
        },
        "judge_required": judge_required,
        "judge_approved": judge_approved,
        "latest_judgment": latest_judgment,
        "judgments": judgments,
        "human_verdict": human_verdict.as_dict() if human_verdict else None,
        "verdict_count": discovery.verdicts.count(),
        "error_summary": discovery.error_summary,
        "report_ready": bool(discovery.report_markdown),
        "report_url": f"/api/v1/discoveries/{discovery.id}/report/",
    }


class PaginationSerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)
    offset = serializers.IntegerField(min_value=0, default=0)


def page(request, queryset):
    pagination = PaginationSerializer(data=request.query_params)
    pagination.is_valid(raise_exception=True)
    parsed = pagination.validated_data
    count = queryset.count()
    offset, limit = parsed["offset"], parsed["limit"]
    return {
        "schema_version": "1.0",
        "count": count,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < count else None,
    }, queryset[offset : offset + limit]


def report_response(discovery: Discovery) -> HttpResponse:
    response = HttpResponse(discovery.report_markdown, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="discovery-{discovery.id}.md"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


class DiscoveriesView(APIView):
    def get(self, request):
        queryset = Discovery.objects.order_by("-created_at")
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        envelope, rows = page(request, queryset)
        envelope["results"] = [discovery_view(row) for row in rows]
        return Response(envelope)

    def post(self, request):
        serializer = DiscoveryStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        key = request.headers.get("Idempotency-Key") or data.get("idempotency_key")
        if not key:
            raise ValidationError(
                {"idempotency_key": "Provide Idempotency-Key or idempotency_key."}
            )
        if len(key) > 200:
            raise ValidationError({"idempotency_key": "Must be at most 200 characters."})
        try:
            discovery = start_discovery(
                data["objective"],
                config=data["config"],
                constraints=data["constraints"],
                idempotency_key=key,
            )
        except DiscoveryConflict as exc:  # allow: suppress-exception
            # Idempotency conflicts are an expected transport-level 409.
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except SchemaValidationError as exc:
            raise ValidationError({"config": str(exc)}) from exc
        except (ValueError, ConnectorConfigurationError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(discovery_view(discovery), status=status.HTTP_202_ACCEPTED)


class DiscoveryDetailView(APIView):
    def get(self, request, discovery_id):
        return Response(discovery_view(get_object_or_404(Discovery, pk=discovery_id)))


class DiscoveryMessagesView(APIView):
    def get(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        envelope, rows = page(
            request,
            DiscoveryMessage.objects.filter(discovery=discovery).order_by("created_at", "id"),
        )
        envelope["results"] = list(rows.values())
        return Response(envelope)


class DiscoveryEvidenceView(APIView):
    def get(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        envelope, rows = page(
            request, Evidence.objects.filter(discovery=discovery).order_by("retrieved_at", "id")
        )
        envelope["results"] = list(rows.values())
        return Response(envelope)


class DiscoveryReportView(APIView):
    def get(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        if not discovery.report_markdown:
            return Response(
                {"detail": "The report is not ready.", "status": discovery.status},
                status=status.HTTP_409_CONFLICT,
            )
        return report_response(discovery)


class DiscoveryCancelView(APIView):
    def post(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        return Response(discovery_view(cancel_discovery(discovery.id)))


class DiscoveryVerdictView(APIView):
    def post(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        try:
            data = VerdictInput.model_validate(request.data)
        except SchemaValidationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        try:
            row = record_verdict(discovery, data.verdict, data.notes, user=None, via="api")
        except VerdictNotAllowed as exc:  # allow: suppress-exception
            # A non-terminal discovery is an expected transport-level 409.
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(row.as_dict(), status=status.HTTP_201_CREATED)


class ConnectorsView(APIView):
    def get(self, request):
        return Response({"schema_version": "1.0", "results": describe_connectors()})
