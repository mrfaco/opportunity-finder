"""Per-wake OpenRouter Messages gateway, following Video Swarm's launch contract.

Claude receives a short-lived loopback credential. Only this process sees the
provider key. Callbacks own durable admission/accounting and may refuse every
request before it is sent. No billed POST is retried by this gateway.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections.abc import Callable, Mapping
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
UPSTREAM = "https://openrouter.ai/api"
DEFAULT_TOOLS = frozenset(
    {
        "mcp__discovery__research",
        "mcp__discovery__submit_proposal",
        "mcp__discovery__submit_validation",
    }
)


class GatewayError(RuntimeError):
    """A selected provider call failed or cannot be accounted for safely."""


def _count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GatewayError("Provider usage contains an invalid token count")
    return value


def _merge_usage(target: dict[str, Any], update: Any) -> None:
    if not isinstance(update, Mapping):
        return
    for key, value in update.items():
        if value is not None:
            target[key] = value


def observe_response(
    raw: bytes,
    content_type: str,
    *,
    prompt_rate: Decimal,
    completion_rate: Decimal,
    generation_id: str | None = None,
) -> dict[str, Any]:
    """Extract Messages usage while leaving tool calls and reasoning untouched."""
    streamed = "text/event-stream" in content_type.lower()
    documents: list[dict[str, Any]] = []
    if streamed:
        for event in raw.replace(b"\r\n", b"\n").split(b"\n\n"):
            data = b"\n".join(
                line[5:].removeprefix(b" ")
                for line in event.split(b"\n")
                if line.startswith(b"data:")
            )
            if not data.strip() or data.strip() == b"[DONE]":
                continue
            decoded = json.loads(data)
            if not isinstance(decoded, dict):
                raise GatewayError("Provider returned an invalid Messages stream event")
            documents.append(decoded)
    else:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise GatewayError("Provider returned an invalid Messages response")
        documents.append(decoded)
    usage: dict[str, Any] = {}
    terminal = False
    stop_reason: str | None = None
    response_model: str | None = None
    for document in documents:
        kind = document.get("type")
        if kind == "error":
            raise GatewayError("Provider returned an error inside the Messages response")
        message = document.get("message") if kind == "message_start" else document
        if isinstance(message, dict) and message.get("type") == "message":
            generation_id = message.get("id") or generation_id
            response_model = message.get("model") or response_model
            _merge_usage(usage, message.get("usage"))
            if not streamed:
                terminal = True
                stop_reason = message.get("stop_reason")
        if kind == "message_delta":
            _merge_usage(usage, document.get("usage"))
            stop_reason = (document.get("delta") or {}).get("stop_reason") or stop_reason
        if kind == "message_stop":
            terminal = True
    if not terminal:
        raise GatewayError("Provider response ended without a terminal Messages event")
    if "input_tokens" not in usage or "output_tokens" not in usage:
        raise GatewayError("Provider response omitted token accounting")
    ordinary = _count(usage["input_tokens"])
    output = _count(usage["output_tokens"])
    cached = _count(usage.get("cache_read_input_tokens", 0))
    written = _count(usage.get("cache_creation_input_tokens", 0))
    total_input = ordinary + cached + written
    reported_cost = usage.get("cost")
    if reported_cost is None:
        cost = Decimal(total_input) * prompt_rate + Decimal(output) * completion_rate
    else:
        cost = Decimal(str(reported_cost))
    if not cost.is_finite() or cost < 0:
        raise GatewayError("Provider returned invalid cost accounting")
    return {
        "schema_version": "1.0",
        "provider_status": "completed",
        "generation_id": generation_id,
        "response_model": response_model,
        "stop_reason": stop_reason,
        "usage": {
            "prompt_tokens": total_input,
            "completion_tokens": output,
            "prompt_tokens_details": {"cached_tokens": cached},
            "cache_creation_input_tokens": written,
            "anthropic": json.loads(json.dumps(usage, default=str)),
        },
        "cost_usd": str(cost),
        "cost_is_estimate": reported_cost is None,
    }


class Gateway(ThreadingHTTPServer):
    """Loopback-only gateway with one active model request per wake.

    ``before_call(body)`` returns an opaque durable operation handle.
    ``after_call(handle, result)`` commits normalized usage/cost before delivery.
    ``on_error(handle, safe_message, outcome_unknown)`` preserves uncertain calls.
    Callback exceptions are exposed in ``failure`` and stop further requests.
    """

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        key: str,
        model: str,
        before_call: Callable[[dict[str, Any]], Any],
        after_call: Callable[[Any, dict[str, Any]], None],
        on_error: Callable[[Any, str, bool], None],
        *,
        prompt_rate: Decimal,
        completion_rate: Decimal,
        timeout_s: float = 60,
        transport: httpx.BaseTransport | None = None,
        allowed_tools: frozenset[str] = DEFAULT_TOOLS,
        max_output_tokens: int = 6000,
        upstream_provider: str | None = None,
    ) -> None:
        if not key or not model or model.endswith(":batch"):
            raise ValueError("A configured non-batch OpenRouter key/model is required")
        if min(prompt_rate, completion_rate) < 0 or not all(
            rate.is_finite() for rate in (prompt_rate, completion_rate)
        ):
            raise ValueError("Gateway rates must be finite and non-negative")
        self._key = key
        self.model = model
        self.token = "discovery-gateway-" + secrets.token_hex(32)
        self.before_call = before_call
        self.after_call = after_call
        self.on_error = on_error
        self.prompt_rate = prompt_rate
        self.completion_rate = completion_rate
        self.allowed_tools = allowed_tools
        self.max_output_tokens = max_output_tokens
        self.upstream_provider = upstream_provider
        self.failure: Exception | None = None
        self.active = threading.Event()
        self.active.set()
        self._request_lock = threading.Lock()
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10)),
            trust_env=False,
        )
        super().__init__(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def __enter__(self) -> Gateway:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        self.active.clear()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
        self.client.close()
        self._key = ""


class _Handler(BaseHTTPRequestHandler):
    server: Gateway
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return  # Requests can contain credentials and private research; never log them.

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._reply(
            status,
            json.dumps(
                {
                    "type": "error",
                    "error": {"type": "gateway_error", "message": message},
                }
            ).encode(),
        )

    def _document(self) -> tuple[str, dict[str, Any]]:
        authorization = self.headers.get("Authorization", "")
        api_key = self.headers.get("x-api-key", "")
        if not self.server.active.is_set() or not (
            hmac.compare_digest(authorization, f"Bearer {self.server.token}")
            or hmac.compare_digest(api_key, self.server.token)
        ):
            raise PermissionError("Wake credential refused")
        path, _, query = self.path.partition("?")
        if path not in ("/v1/messages", "/v1/messages/count_tokens") or query not in (
            "",
            "beta=true",
        ):
            raise ValueError("Unsupported gateway route")
        size = self.headers.get("Content-Length", "")
        if not size.isdigit() or not 0 < int(size) <= MAX_REQUEST_BYTES:
            raise ValueError("Invalid request size")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked request bodies are not supported")
        self.connection.settimeout(15)
        document = json.loads(self.rfile.read(int(size)))
        if not isinstance(document, dict) or document.get("model") != self.server.model:
            raise ValueError("Request model differs from the selected model")
        if any(
            document.get(name) for name in ("models", "fallbacks", "route", "plugins", "provider")
        ):
            raise ValueError("Caller routing and provider plugins are not permitted")
        if not isinstance(document.get("messages"), list):
            raise ValueError("Messages must be an array")
        if path.endswith("count_tokens"):
            return path, document
        tools = document.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict)
            or tool.get("type", "custom") != "custom"
            or tool.get("name") not in self.server.allowed_tools
            for tool in tools
        ):
            raise ValueError("Only this role's discovery MCP tools are allowed")
        maximum = document.get("max_tokens")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise ValueError("A positive max_tokens is required")
        document["max_tokens"] = min(maximum, self.server.max_output_tokens)
        if not self.server.model.startswith("anthropic/"):
            document.pop("thinking", None)
            output = document.get("output_config", {})
            if not isinstance(output, dict):
                raise ValueError("Invalid output configuration")
            output.pop("effort", None)
            if output:
                document["output_config"] = output
            else:
                document.pop("output_config", None)
            for tool in tools:
                tool.pop("defer_loading", None)
        # Prevent catalog changes from routing the request above its reserved rates.
        document["provider"] = {
            "max_price": {
                "prompt": str(self.server.prompt_rate * 1_000_000),
                "completion": str(self.server.completion_rate * 1_000_000),
            },
            "require_parameters": True,
            "allow_fallbacks": False,
        }
        if self.server.upstream_provider:
            document["provider"]["only"] = [self.server.upstream_provider]
        return path, document

    def do_POST(self) -> None:
        try:
            path, document = self._document()
        except PermissionError as exc:  # allow: suppress-exception
            # Protocol refusals are explicit HTTP errors and no upstream call is made.
            self._error(403, str(exc))
            return
        except (ValueError, OSError) as exc:  # allow: suppress-exception
            # Malformed local requests receive a visible refusal, never a fallback result.
            self._error(400, type(exc).__name__ + ": invalid gateway request")
            return
        if path.endswith("count_tokens"):
            count = len(json.dumps(document, ensure_ascii=False).encode()) + 8192
            self._reply(200, json.dumps({"input_tokens": count}).encode())
            return
        with self.server._request_lock:
            self._forward(path, document)

    def _record_error(self, handle: Any, message: str, unknown: bool) -> None:
        try:
            self.server.on_error(handle, message, unknown)
        except Exception as exc:  # noqa: BLE001  # allow: suppress-exception
            # allow: suppress-exception — HTTP handler cannot raise into the parent thread.
            self.server.failure = exc

    def _forward(self, path: str, document: dict[str, Any]) -> None:
        if self.server.failure is not None or not self.server.active.is_set():
            self._error(503, "Wake gateway is stopped after an earlier failure")
            return
        try:
            handle = self.server.before_call(document)
        except Exception as exc:  # noqa: BLE001  # allow: suppress-exception
            # allow: suppress-exception — explicit refusal, the paid request was not sent.
            self.server.failure = exc
            self._error(403, "Discovery admission refused; inspect its recorded stop reason")
            return
        headers = {
            "Authorization": f"Bearer {self.server._key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "anthropic-version": "2023-06-01",
        }
        beta = self.headers.get("anthropic-beta")
        if beta and len(beta) <= 4096 and all(32 <= ord(c) <= 126 for c in beta):
            headers["anthropic-beta"] = beta
        try:
            with self.server.client.stream(
                "POST", UPSTREAM + path, headers=headers, json=document
            ) as response:
                if not response.is_success:
                    unknown = response.status_code >= 500
                    message = f"OpenRouter refused Messages request (HTTP {response.status_code})"
                    self._record_error(handle, message, unknown)
                    self.server.failure = self.server.failure or GatewayError(message)
                    self._error(response.status_code, message)
                    return
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if not self.server.active.is_set():
                        raise GatewayError("Wake stopped during the provider response")  # noqa: TRY301
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise GatewayError("Provider response exceeded its size bound")  # noqa: TRY301
                content_type = response.headers.get("content-type", "application/json")
                observed = observe_response(
                    bytes(raw),
                    content_type,
                    prompt_rate=self.server.prompt_rate,
                    completion_rate=self.server.completion_rate,
                    generation_id=response.headers.get("x-openrouter-generation-id"),
                )
        except Exception as exc:  # noqa: BLE001  # allow: suppress-exception
            # No retry: the supervisor sees failure and the ledger records uncertainty.
            message = f"Provider response unavailable or unaccountable ({type(exc).__name__})"
            self._record_error(handle, message, True)
            self.server.failure = self.server.failure or GatewayError(message)
            self._error(502, message)
            return
        try:
            self.server.after_call(handle, observed)
        except Exception as exc:  # noqa: BLE001  # allow: suppress-exception
            # Stop the wake before delivering an unrecorded paid response.
            self.server.failure = exc
            self._error(503, "Discovery accounting failed; paid response needs reconciliation")
            return
        # Whole-response buffering is bounded and lets us settle before Claude can
        # execute the returned tools. Preserve the original Anthropic SSE wire.
        safe_raw = bytes(raw).replace(self.server._key.encode(), b"[redacted]")
        try:
            self._reply(200, safe_raw, content_type)
        except OSError as exc:  # allow: suppress-exception
            # The response is already accounted for; a disconnected CLI cannot undo its charge.
            self.server.failure = GatewayError(
                f"Claude disconnected after accounting ({type(exc).__name__})"
            )
