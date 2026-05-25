"""DRF authentication via ``Authorization: Bearer <key>``.

Loud failures (AGENTS.md §1): malformed headers, unknown keys, and revoked
keys all raise ``AuthenticationFailed``. There is no permissive "maybe
valid" path. ``last_used_at`` is updated best-effort; a DB error on the
update is allowed to propagate (it indicates a real problem, not a
suppressible one).
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from api.models import ApiKey, hash_key


class ApiKeyAuthentication(BaseAuthentication):
    keyword = b"Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request)
        if not header:
            # No header at all = "not authenticated by this method". DRF will
            # then look at the next auth class or fall through to anonymous.
            return None
        parts = header.split()
        if parts[0] != self.keyword:
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Malformed Authorization header.")
        raw_key = parts[1].decode("utf-8")
        digest = hash_key(raw_key)
        try:
            key = ApiKey.objects.select_related("user").get(key_hash=digest)
        except ApiKey.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid API key.") from exc
        if key.revoked_at is not None:
            raise AuthenticationFailed("API key revoked.")
        if not key.user.is_active:
            raise AuthenticationFailed("User inactive.")
        # Best-effort touch — single UPDATE, no surrounding try/except so a
        # DB problem propagates instead of being silently swallowed.
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return (key.user, key)

    def authenticate_header(self, request: object) -> str:
        return 'Bearer realm="api"'
