from django.urls import path

from discovery import api

urlpatterns = [
    path("discoveries/", api.DiscoveriesView.as_view(), name="api-discoveries"),
    path(
        "discoveries/<uuid:discovery_id>/", api.DiscoveryDetailView.as_view(), name="api-discovery"
    ),
    path(
        "discoveries/<uuid:discovery_id>/messages/",
        api.DiscoveryMessagesView.as_view(),
        name="api-discovery-messages",
    ),
    path(
        "discoveries/<uuid:discovery_id>/evidence/",
        api.DiscoveryEvidenceView.as_view(),
        name="api-discovery-evidence",
    ),
    path(
        "discoveries/<uuid:discovery_id>/report/",
        api.DiscoveryReportView.as_view(),
        name="api-discovery-report",
    ),
    path(
        "discoveries/<uuid:discovery_id>/cancel/",
        api.DiscoveryCancelView.as_view(),
        name="api-discovery-cancel",
    ),
    path("connectors/", api.ConnectorsView.as_view(), name="api-connectors"),
]
