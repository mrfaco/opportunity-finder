"""DRF views for the public API.

Thin transport layer (AGENTS.md §10): every state-changing call delegates
to an existing orchestrator function. The views are responsible for:

* Parsing + validating input via serializers.
* Calling the orchestrator.
* Returning HTTP responses with sensible status codes.

They do not implement business logic, write ``AgentRun`` rows, call
adapters, or duplicate any classification/clustering work. If a future
endpoint needs new logic, it goes into the appropriate app's orchestrator
module, not here.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers import (
    BackfillRequestSerializer,
    ClusterMergeProposalDetailSerializer,
    ClusterMergeProposalListSerializer,
    ClusterSerializer,
    IdeationDetailSerializer,
    IdeationListSerializer,
    IngestedItemSerializer,
    IngestionCheckpointSerializer,
    IngestRunRequestSerializer,
    InvestigationDetailSerializer,
    InvestigationListSerializer,
    InvestigationRunQueuedSerializer,
    InvestigationStartRequestSerializer,
    MergeProposalRejectRequestSerializer,
    RejectRequestSerializer,
    StaleRequestSerializer,
    TaskQueuedResponseSerializer,
    TaskRunSerializer,
)
from clusters import orchestrator as cluster_orchestrator
from clusters import tasks as cluster_tasks
from clusters.models import Cluster, ClusterItem, ClusterMergeProposal, ClusterStatus
from core.pdf import render_pdf
from ideation.models import Ideation
from ingestion import tasks as ingestion_tasks
from ingestion.models import IngestionCheckpoint
from investigations.models import Investigation
from investigations.orchestrator import (
    mark_stale_one,
    promote_investigation,
    reject_investigation,
    start_investigation,
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _clamp_limit(raw: str | None) -> int:
    """Parse a ``?limit=`` query param. Loud-fail on garbage (ValueError → 400)."""
    if raw is None:
        return _DEFAULT_LIMIT
    value = int(raw)  # invalid → ValueError → DRF returns 400 via default handler
    if value < 1:
        raise ValueError("limit must be >= 1")
    return min(value, _MAX_LIMIT)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class IngestionRunsView(APIView):
    """POST only: trigger an incremental ingest. Read history via /task-runs/."""

    def post(self, request):
        ser = IngestRunRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        async_result = ingestion_tasks.ingest_source.delay(ser.validated_data["source"])
        out = TaskQueuedResponseSerializer({"task_id": str(async_result.id), "status": "queued"})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class TaskRunsView(APIView):
    """GET: list recent Celery task results across the system.

    Filter via ``task_prefix`` to scope (e.g. ``ingestion.tasks.``,
    ``clusters.tasks.``). The history is whatever django-celery-results
    has recorded; without a result backend running this returns empty.
    """

    def get(self, request):
        from django_celery_results.models import TaskResult  # noqa: PLC0415

        limit = _clamp_limit(request.query_params.get("limit"))
        prefix = request.query_params.get("task_prefix")
        contains = request.query_params.get("args_contains")
        qs = TaskResult.objects.all()
        if prefix:
            qs = qs.filter(task_name__startswith=prefix)
        if contains:
            # Args are stored as a repr-ish string; substring match is
            # the most useful filter without doing args-by-position parsing.
            qs = qs.filter(task_args__contains=contains)
        rows = list(qs.order_by("-date_created")[:limit])
        data = [
            {
                "task_id": r.task_id,
                "task_name": r.task_name,
                "status": r.status,
                "date_created": r.date_created,
                "date_done": r.date_done,
                "task_args": r.task_args or "",
                "task_kwargs": r.task_kwargs or "",
                "traceback": r.traceback,
            }
            for r in rows
        ]
        return Response(TaskRunSerializer(data, many=True).data)


class IngestionBackfillsView(APIView):
    """POST only: trigger a backfill of N days from a source."""

    def post(self, request):
        ser = BackfillRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        async_result = ingestion_tasks.backfill_source_task.delay(
            ser.validated_data["source"], ser.validated_data["days"]
        )
        out = TaskQueuedResponseSerializer({"task_id": str(async_result.id), "status": "queued"})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class IngestionItemsView(APIView):
    """GET: recent ingested cluster-items (most-recent-first by assigned_at)."""

    def get(self, request):
        limit = _clamp_limit(request.query_params.get("limit"))
        source = request.query_params.get("source")
        qs = ClusterItem.objects.select_related("cluster").order_by("-assigned_at")
        if source:
            qs = qs.filter(source=source)
        rows = list(qs[:limit])
        data = [
            {
                "id": r.id,
                "cluster_id": r.cluster_id,
                "source": r.source,
                "source_item_id": r.source_item_id,
                "title": r.title,
                "url": r.url,
                "posted_at": r.posted_at,
                "assigned_at": r.assigned_at,
                "snippet": r.snippet,
                "classifier_verdict": r.classifier_verdict,
                "classifier_confidence": r.classifier_confidence,
            }
            for r in rows
        ]
        return Response(IngestedItemSerializer(data, many=True).data)


class IngestionCheckpointsView(APIView):
    """GET only: per-source watermark state. Useful for 'is ingest stuck?'."""

    def get(self, request):
        rows = list(IngestionCheckpoint.objects.all().order_by("source"))
        return Response(IngestionCheckpointSerializer(rows, many=True).data)


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------


class RefinementRunsView(APIView):
    """POST only: enqueue refine_clusters_nightly.

    The task recomputes centroids, reassigns orphans, queues + judges
    merge proposals, and regenerates titles for drifted clusters. Polls
    via GET /api/v1/task-runs/?task_prefix=clusters.tasks.
    """

    def post(self, request):
        async_result = cluster_tasks.refine_clusters_nightly.delay()
        out = TaskQueuedResponseSerializer({"task_id": str(async_result.id), "status": "queued"})
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class ClusterMergeProposalsView(APIView):
    """GET: list merge proposals.

    Filters: ``status`` (e.g. pending_review), ``judge_verdict`` (``true`` /
    ``false``), ``min_judge_confidence``. The skill workflow is "show me
    the obvious-yes pending proposals" — i.e. status=pending_review +
    judge_verdict=true sorted by confidence.
    """

    def get(self, request):
        limit = _clamp_limit(request.query_params.get("limit"))
        qs = ClusterMergeProposal.objects.select_related("cluster_a", "cluster_b").order_by(
            "-created_at"
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        judge_verdict = request.query_params.get("judge_verdict")
        if judge_verdict is not None:
            # Accept "true"/"false"/"null" textually. "null" surfaces rows
            # the judge couldn't evaluate (shouldn't happen post-judge, but
            # also covers pre-judge rows from before the wiring landed).
            jv = judge_verdict.lower()
            if jv == "true":
                qs = qs.filter(llm_judge_verdict=True)
            elif jv == "false":
                qs = qs.filter(llm_judge_verdict=False)
            elif jv == "null":
                qs = qs.filter(llm_judge_verdict__isnull=True)

        min_conf = request.query_params.get("min_judge_confidence")
        if min_conf is not None:
            qs = qs.filter(llm_judge_confidence__gte=float(min_conf))

        rows = list(qs[:limit])
        return Response(ClusterMergeProposalListSerializer(rows, many=True).data)


class ClusterMergeProposalDetailView(APIView):
    """GET: full proposal with judge reasoning + both clusters' summaries."""

    def get(self, request, pk):
        try:
            proposal = ClusterMergeProposal.objects.select_related("cluster_a", "cluster_b").get(
                pk=pk
            )
        except ClusterMergeProposal.DoesNotExist:
            return Response(
                {"detail": f"Merge proposal {pk} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ClusterMergeProposalDetailSerializer(proposal).data)


class ClusterMergeProposalActionViewSet(viewsets.ViewSet):
    """Action endpoints — apply / reject. Delegates to clusters.orchestrator."""

    def get_queryset(self):
        return ClusterMergeProposal.objects.all()

    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        proposal = cluster_orchestrator.apply_merge_proposal(proposal_id=pk, user=request.user)
        proposal.refresh_from_db()
        return Response(ClusterMergeProposalDetailSerializer(proposal).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        ser = MergeProposalRejectRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        proposal = cluster_orchestrator.reject_merge_proposal(
            proposal_id=pk,
            user=request.user,
            review_notes=ser.validated_data["review_notes"],
        )
        proposal.refresh_from_db()
        return Response(ClusterMergeProposalDetailSerializer(proposal).data)


class ClustersView(APIView):
    """GET: list clusters. Filters: ``min_size``, ``status``, ``has_pending``.

    ``has_pending=false`` is the common case — "clusters that haven't been
    investigated yet" — so we expose it as a boolean filter rather than
    making the caller construct two-arg filters.
    """

    def get(self, request):
        limit = _clamp_limit(request.query_params.get("limit"))
        qs = Cluster.active.all()

        min_size = request.query_params.get("min_size")
        if min_size is not None:
            qs = qs.filter(size__gte=int(min_size))

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        has_pending = request.query_params.get("has_pending")
        if has_pending is not None:
            # Has at least one non-finalized investigation? The triage skill
            # passes has_pending=false to find candidates worth investigating.
            wants_pending = has_pending.lower() in ("1", "true", "yes")
            if wants_pending:
                qs = qs.filter(investigations__finalized_at__isnull=True).distinct()
            else:
                qs = qs.exclude(investigations__finalized_at__isnull=True)

        qs = qs.order_by("-last_seen_at")[:limit]
        return Response(ClusterSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# Investigations
# ---------------------------------------------------------------------------


class InvestigationsView(APIView):
    """GET only: list investigations with optional status filter."""

    def get(self, request):
        limit = _clamp_limit(request.query_params.get("limit"))
        qs = Investigation.objects.select_related("cluster", "primary_run").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        rows = list(qs[:limit])
        return Response(InvestigationListSerializer(rows, many=True).data)


class InvestigationRunsView(APIView):
    """POST only: queue an investigation run for a cluster."""

    def post(self, request):
        ser = InvestigationStartRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cluster_id = ser.validated_data["cluster_id"]
        try:
            cluster = Cluster.objects.get(pk=cluster_id)
        except Cluster.DoesNotExist:
            return Response(
                {"detail": f"Cluster {cluster_id} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if cluster.status in (ClusterStatus.MERGED_INTO, ClusterStatus.SPLIT):
            return Response(
                {
                    "detail": f"Cluster {cluster_id} is {cluster.status} and not investigable.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        run_id = start_investigation(cluster_id=cluster_id, trigger="api")
        out = InvestigationRunQueuedSerializer(
            {"run_id": run_id, "cluster_id": cluster_id, "status": "queued"}
        )
        return Response(out.data, status=status.HTTP_202_ACCEPTED)


class InvestigationDetailView(APIView):
    """GET one investigation (full brief + cluster snapshot)."""

    def get(self, request, pk):
        try:
            inv = Investigation.objects.get(pk=pk)
        except Investigation.DoesNotExist:
            return Response(
                {"detail": f"Investigation {pk} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(InvestigationDetailSerializer(inv).data)


class InvestigationPdfView(APIView):
    """GET a PDF render of one investigation's brief."""

    def get(self, request, pk):
        try:
            inv = Investigation.objects.select_related("cluster").get(pk=pk)
        except Investigation.DoesNotExist:
            return Response(
                {"detail": f"Investigation {pk} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        pdf_bytes = render_pdf(
            "pdf/investigation.html",
            {"investigation": inv, "brief": inv.brief or {}, "cluster": inv.cluster},
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="investigation-{inv.id}.pdf"'
        return response


class InvestigationActionViewSet(viewsets.ViewSet):
    """Action endpoints — promote / reject / stale.

    Routed manually (no ModelViewSet) because each action has different
    body shape + response shape. Using a ViewSet only to group them.
    """

    def get_queryset(self):
        return Investigation.objects.all()

    @action(detail=True, methods=["post"], url_path="promote")
    def promote(self, request, pk=None):
        ideation_id, ideation_run_id = promote_investigation(investigation_id=pk, user=request.user)
        # Refresh the investigation so we return its new state to the caller.
        inv = Investigation.objects.get(pk=pk)
        return Response(
            {
                "investigation": InvestigationDetailSerializer(inv).data,
                "ideation_id": str(ideation_id),
                "ideation_run_id": str(ideation_run_id),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        ser = RejectRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reject_investigation(
            investigation_id=pk, user=request.user, reason=ser.validated_data["reason"]
        )
        inv = Investigation.objects.get(pk=pk)
        return Response(InvestigationDetailSerializer(inv).data)

    @action(detail=True, methods=["post"], url_path="stale")
    def stale(self, request, pk=None):
        ser = StaleRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        mark_stale_one(investigation_id=pk, stale_reason=ser.validated_data["stale_reason"])
        inv = Investigation.objects.get(pk=pk)
        return Response(InvestigationDetailSerializer(inv).data)


# ---------------------------------------------------------------------------
# Ideations
# ---------------------------------------------------------------------------


class IdeationsView(APIView):
    """GET only: list ideations with optional status filter."""

    def get(self, request):
        limit = _clamp_limit(request.query_params.get("limit"))
        qs = Ideation.objects.select_related("investigation").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        rows = list(qs[:limit])
        return Response(IdeationListSerializer(rows, many=True).data)


class IdeationDetailView(APIView):
    """GET one ideation (full output payload)."""

    def get(self, request, pk):
        try:
            ideation = Ideation.objects.get(pk=pk)
        except Ideation.DoesNotExist:
            return Response(
                {"detail": f"Ideation {pk} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(IdeationDetailSerializer(ideation).data)


class IdeationPdfView(APIView):
    """GET a PDF render of one ideation's concept set."""

    def get(self, request, pk):
        try:
            ideation = Ideation.objects.select_related("investigation").get(pk=pk)
        except Ideation.DoesNotExist:
            return Response(
                {"detail": f"Ideation {pk} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        inv = ideation.investigation
        brief = (inv.brief or {}) if inv else {}
        pdf_bytes = render_pdf(
            "pdf/ideation.html",
            {
                "ideation": ideation,
                "output": ideation.output or {},
                "investigation_headline": brief.get("headline"),
            },
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="ideation-{ideation.id}.pdf"'
        return response
