"""Run-scoped Redis cache for tool calls.

Each agent run gets its own namespace; the run cache is cleared when the run
ends. Cross-run persistent caching for ``fetch_url``, ``fetch_hn_item``, and
``query_trustmrr`` is a v2 task — see ``NEXT_STEPS.md``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import redis
from django.conf import settings

DEFAULT_TTL_SECONDS = 60 * 60  # 1-hour safety net


class RunScopedCache:
    def __init__(self, run_id: UUID, redis_client: redis.Redis | None = None) -> None:
        self.run_id = str(run_id)
        self._r = redis_client or redis.Redis.from_url(settings.REDIS_URL)
        self._prefix = f"run:{self.run_id}:tool:"

    def _key(self, tool_name: str, input_hash: str) -> str:
        return f"{self._prefix}{tool_name}:{input_hash}"

    def get(self, tool_name: str, input_hash: str) -> dict[str, Any] | None:
        raw = self._r.get(self._key(tool_name, input_hash))
        if raw is None:
            return None
        # We wrote this value with ``json.dumps`` in ``set`` — if it doesn't
        # round-trip, something foreign is in our namespace and we must not
        # paper over it. Let the decode error propagate so the run aborts.
        return json.loads(raw)

    def set(
        self,
        tool_name: str,
        input_hash: str,
        value: dict[str, Any],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._r.set(
            self._key(tool_name, input_hash),
            json.dumps(value),
            ex=ttl_seconds,
        )

    def clear(self) -> None:
        # SCAN to avoid blocking; delete batches.
        cursor = 0
        while True:
            cursor, keys = self._r.scan(cursor=cursor, match=f"{self._prefix}*", count=200)
            if keys:
                self._r.delete(*keys)
            if cursor == 0:
                return
