"""Custom DRF exception handler.

Translates the small set of domain exceptions raised by orchestrators
into specific HTTP status codes. Everything else falls back to DRF's
default handler (which already does the right thing for ValidationError,
NotFound, PermissionDenied, AuthenticationFailed, etc.).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from investigations.orchestrator import (
    InvestigationNotFound,
    InvestigationNotInState,
)


def api_exception_handler(exc, context):
    if isinstance(exc, InvestigationNotInState):
        return Response(
            {
                "detail": str(exc),
                "current_status": exc.current_status,
                "expected_status": exc.expected_status,
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, InvestigationNotFound):
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return drf_exception_handler(exc, context)
