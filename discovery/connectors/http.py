"""One bounded JSON transport for connectors that call fixed provider API hosts.

Arbitrary URLs never come through here: ``fetch_public_url`` with its public-IP
checks remains the only way to retrieve a URL chosen by a role. A provider's
behaviour (transport failure, denial, rate limit, redirect, non-JSON or
oversized body, unexpected status) is returned as a typed ``ResearchPage``;
nothing here raises for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from discovery.connectors.base import ResearchPage
from discovery.connectors.web import _TIMEOUT_SECONDS, _USER_AGENT, _status_page

MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class JsonOutcome:
    """Parsed JSON from a 2xx response, or the typed page describing why there is none."""

    data: Any = None
    error: ResearchPage | None = None


def _request(
    method: str,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: float,
    data: dict[str, str] | None = None,
    json_body: Any = None,
) -> JsonOutcome:
    try:
        with httpx.stream(
            method,
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json", **(headers or {})},
            data=data,
            json=json_body,
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status = response.status_code
            expected = _status_page(status, response.headers.get("Retry-After"))
            if expected is not None:
                reason = f"{source}: {expected.error_reason}"
                return JsonOutcome(error=expected.model_copy(update={"error_reason": reason}))
            if not 200 <= status < 300:
                return JsonOutcome(
                    error=ResearchPage(
                        status="error", error_reason=f"{source} returned HTTP {status}"
                    )
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    return JsonOutcome(
                        error=ResearchPage(
                            status="error",
                            error_reason=f"{source} response exceeds the 2 MB limit",
                        )
                    )
    except httpx.TimeoutException:  # allow: suppress-exception — visible provider outcome
        return JsonOutcome(
            error=ResearchPage(status="timeout", error_reason=f"{source} request timed out")
        )
    except httpx.RequestError as exc:  # allow: suppress-exception — visible provider outcome
        return JsonOutcome(
            error=ResearchPage(
                status="error",
                error_reason=f"{source} transport failed ({type(exc).__name__})",
            )
        )
    try:
        parsed = json.loads(bytes(body))
    except ValueError:  # allow: suppress-exception — provider sent a non-JSON body
        return JsonOutcome(
            error=ResearchPage(status="error", error_reason=f"{source} did not return JSON")
        )
    return JsonOutcome(data=parsed)


def get_json(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> JsonOutcome:
    return _request("GET", url, source=source, params=params, headers=headers, timeout=timeout)


def post_json(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> JsonOutcome:
    """POST a form (``data``) or JSON body (``json_body``) and parse a JSON reply."""
    return _request(
        "POST",
        url,
        source=source,
        params=params,
        headers=headers,
        timeout=timeout,
        data=data,
        json_body=json_body,
    )
