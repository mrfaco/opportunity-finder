"""API authentication tokens.

One ``ApiKey`` per (user, label). The raw key is shown once at creation
and never stored — only its SHA-256 hash. ``prefix`` (the first 8 chars
of the raw key) is kept in plain text so the admin can show "which key
is which" without recovering secrets.

Module boundary (per AGENTS.md §10): this app owns auth. Other apps must
not import ``ApiKey`` directly — auth happens through the DRF auth class.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import models

# Length of the random portion of the key body (the part after "opp_").
# 32 base32 chars = 160 bits of entropy — plenty.
_KEY_BODY_LENGTH = 32
_KEY_PREFIX_TAG = "opp_"
_PREFIX_DISPLAY_LENGTH = 8


def hash_key(raw: str) -> str:
    """SHA-256 hex digest of a raw key. Used at creation + every lookup."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_raw_key() -> str:
    """Generate a fresh key in the form ``opp_<32 url-safe chars>``."""
    body = secrets.token_urlsafe(_KEY_BODY_LENGTH)[:_KEY_BODY_LENGTH]
    return f"{_KEY_PREFIX_TAG}{body}"


class ApiKey(models.Model):
    """Bearer token bound to a Django user.

    Lookup path on every authenticated request: hash the bearer value,
    look up the row, reject if missing / revoked. ``last_used_at`` is
    touched best-effort on a successful lookup.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )

    # SHA-256 hex digest (64 chars). Unique so we can short-circuit lookup.
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # First 8 chars of the raw key. Cosmetic — lets the admin show
    # "opp_aB7c…" without keeping the secret around.
    prefix = models.CharField(max_length=_PREFIX_DISPLAY_LENGTH)

    label = models.CharField(max_length=64, help_text="Human note, e.g. 'laptop'.")

    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __str__(self) -> str:
        state = "active" if self.is_active else "revoked"
        return f"{self.label} ({self.prefix}…, {state})"

    @classmethod
    def create_for_user(cls, *, user: AbstractBaseUser, label: str) -> tuple["ApiKey", str]:
        """Mint a new key. Returns ``(row, raw_key)``.

        The raw key is the **only** time we ever see the plaintext. The caller
        is responsible for showing it to the operator and then discarding it.
        """
        raw = generate_raw_key()
        row = cls.objects.create(
            user=user,
            key_hash=hash_key(raw),
            prefix=raw[:_PREFIX_DISPLAY_LENGTH],
            label=label,
        )
        return row, raw
