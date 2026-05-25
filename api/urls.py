"""API URL routing.

Mounted at /api/v1/ from config/urls.py. Action endpoints (promote/reject/
stale) are wired through ``InvestigationActionViewSet.as_view({...})``
rather than DefaultRouter so the URL surface stays readable as a flat
list.
"""

from __future__ import annotations

from django.urls import path

from api import views

urlpatterns = [
    # Ingestion
    path("ingestion/runs/", views.IngestionRunsView.as_view(), name="api-ingestion-runs"),
    path(
        "ingestion/backfills/",
        views.IngestionBackfillsView.as_view(),
        name="api-ingestion-backfills",
    ),
    path("ingestion/items/", views.IngestionItemsView.as_view(), name="api-ingestion-items"),
    path(
        "ingestion/checkpoints/",
        views.IngestionCheckpointsView.as_view(),
        name="api-ingestion-checkpoints",
    ),
    # Clusters
    path("clusters/", views.ClustersView.as_view(), name="api-clusters"),
    # Investigations
    path("investigations/", views.InvestigationsView.as_view(), name="api-investigations"),
    path(
        "investigations/runs/",
        views.InvestigationRunsView.as_view(),
        name="api-investigation-runs",
    ),
    path(
        "investigations/<uuid:pk>/",
        views.InvestigationDetailView.as_view(),
        name="api-investigation-detail",
    ),
    path(
        "investigations/<uuid:pk>/promote/",
        views.InvestigationActionViewSet.as_view({"post": "promote"}),
        name="api-investigation-promote",
    ),
    path(
        "investigations/<uuid:pk>/reject/",
        views.InvestigationActionViewSet.as_view({"post": "reject"}),
        name="api-investigation-reject",
    ),
    path(
        "investigations/<uuid:pk>/stale/",
        views.InvestigationActionViewSet.as_view({"post": "stale"}),
        name="api-investigation-stale",
    ),
    # Ideations
    path("ideations/", views.IdeationsView.as_view(), name="api-ideations"),
    path("ideations/<uuid:pk>/", views.IdeationDetailView.as_view(), name="api-ideation-detail"),
]
