"""Wake-scoped MCP bridge: Claude Code can research and submit, with no shell access."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from django.db import close_old_connections, transaction
from pydantic import ValidationError

from discovery.errors import DiscoveryStop
from discovery.models import DiscoveryTask
from discovery.orchestrator import check_lease, commit_turn, content_hash
from discovery.research import check_limits, lease_token, perform_research
from discovery.schemas import Contract, DiscoveryConfig, Judgment, Proposal, Validation


class AgentTools:
    def __init__(self, task: DiscoveryTask, tools: list[dict[str, Any]]) -> None:
        self.task = task
        self.tools = tools
        self.token = secrets.token_urlsafe(32)
        self.failure: Exception | None = None
        self._lock = threading.Lock()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Request headers contain a capability token; no HTTP access log.
                return

            def do_POST(self) -> None:
                if self.path != "/mcp" or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {bridge.token}"
                ):
                    self.send_error(403)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 2_000_000:
                    self.send_error(413)
                    return
                close_old_connections()
                try:
                    request = json.loads(self.rfile.read(size))
                    result = bridge.handle(request)
                    if "id" not in request:
                        self.send_response(202)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
                    encoded = payload.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                finally:
                    close_old_connections()

            def do_GET(self) -> None:
                self.send_error(405, "This stateless MCP server accepts JSON POST requests")

            def do_DELETE(self) -> None:
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> AgentTools:
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        if method == "initialize":
            return {
                "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "discovery", "version": "1.0"},
            }
        if method in {"ping", "notifications/initialized", "notifications/cancelled"}:
            return {}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": tool["function"]["name"],
                        "description": tool["function"]["description"],
                        "inputSchema": tool["function"]["parameters"],
                    }
                    for tool in self.tools
                ]
            }
        if method != "tools/call":
            return {
                "content": [{"type": "text", "text": "Unsupported MCP method"}],
                "isError": True,
            }
        params = request.get("params", {})
        with self._lock:
            try:
                result = self.call(
                    params.get("name", ""), params.get("arguments", {}), request.get("id")
                )
            except (ValueError, ValidationError) as exc:  # allow: suppress-exception
                # Versioned tool validation errors are visible self-correction input.
                result = {"schema_version": "1.0", "status": "validation_failed", "error": str(exc)}
            except DiscoveryStop as exc:  # allow: suppress-exception
                # Research exhaustion is a visible tool quota, not an aborted
                # dialogue. Leave the agent able to submit its current findings.
                if exc.reason != "turn_call_limit":
                    self.failure = exc
                    raise
                result = {
                    "schema_version": "1.0",
                    "status": "research_limit_reached",
                    "research_calls_remaining": 0,
                    "message": "Research quota reached. Make no more research calls. "
                    "Submit your best evidence-backed brief now; label evidence gaps honestly.",
                }
            except Exception as exc:  # noqa: BLE001  # supervisor observes and propagates failure
                self.failure = exc
                raise
        return {
            "content": [{"type": "text", "text": self.model_result(result)}],
            "isError": result.get("status") == "validation_failed",
        }

    @staticmethod
    def model_result(result: dict[str, Any]) -> str:
        # Claude saves large MCP results to files. These agents deliberately have
        # no file tools, so keep the visible response bounded and retain the full
        # captured evidence in PostgreSQL instead of returning an unreadable file.
        if "documents" in result:
            original = result["documents"]
            allowance = min(16000, 30000 // max(1, len(original)))
            documents = [
                {
                    key: document.get(key)
                    for key in (
                        "evidence_id",
                        "url",
                        "kind",
                        "connector",
                        "external_id",
                        "retrieved_at",
                        "published_at",
                    )
                }
                | {
                    "title": document.get("title", "")[:200],
                    "text": document.get("text", "")[:allowance],
                    "context_truncated": bool(document.get("context_truncated"))
                    or len(document.get("text", "")) > allowance,
                }
                for document in original
            ]
            result = {**result, "documents": documents}
            while len(json.dumps(result, ensure_ascii=False).encode()) > 40000 and documents:
                documents.pop()
                result["truncated"] = True
                result["context_truncated"] = True
        rendered = json.dumps(result, ensure_ascii=False)
        if len(rendered.encode()) > 45000:
            return json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "validation_failed",
                    "error": "Tool result is too large; refine the query or request fewer results.",
                }
            )
        return rendered

    def call(self, name: str, arguments: dict[str, Any], request_id: Any = None) -> dict[str, Any]:
        check_limits(self.task)
        if name not in {tool["function"]["name"] for tool in self.tools}:
            raise ValueError("Tool is not available to this role")
        self.task.refresh_from_db(fields=["context"])
        if self.task.context.get("claude_submission"):
            return {
                "schema_version": "1.0",
                "status": "success",
                "message": "Your brief is submitted. End the session now.",
            }
        if name == "research":
            sequence = hashlib.sha256(
                json.dumps([name, request_id], sort_keys=True).encode()
            ).hexdigest()[:24]
            result = perform_research(
                self.task, f"{self.task.pk}:claude:research:{sequence}", arguments
            )
            config = DiscoveryConfig.model_validate(self.task.discovery.config_snapshot)
            remaining = max(
                0,
                config.max_research_calls_per_turn
                - self.task.operations.filter(kind="research").count(),
            )
            result = {**result, "research_calls_remaining": remaining}
            if remaining <= 2:
                result["budget_notice"] = (
                    f"Only {remaining} research calls remain. "
                    "Prioritize decisive pages and submit your brief."
                )
            return result
        schemas: dict[str, type[Contract]] = {
            "finder": Proposal,
            "validator": Validation,
            "judge": Judgment,
        }
        schema = schemas[self.task.role]
        normalized = schema.model_validate(arguments).model_dump(mode="json")
        # Validate now so the agent can repair a citation/schema problem. Commit
        # the mail handoff only after the supervisor settles the wake's usage.
        commit_turn(self.task.pk, lease_token(self.task), normalized, validate_only=True)
        with transaction.atomic():
            task = DiscoveryTask.objects.select_for_update().get(pk=self.task.pk)
            check_lease(task.pk, lease_token(self.task))
            task.context = {**task.context, "claude_submission": normalized}
            task.save(update_fields=["context"])
        return {
            "schema_version": "1.0",
            "status": "success",
            "submission_hash": content_hash(normalized),
            "message": "Brief accepted for delivery. End your session; the supervisor mails it.",
        }
