import logging
from typing import Any

from app.itsm import service
from app.shared.http_client import BackendRejected, BackendUnavailable

logger = logging.getLogger(__name__)


def register_tools(mcp, controls):
    def execute(name, scope, operation):
        error = controls.before_tool(name, scope)
        if error:
            return error
        try:
            return operation()
        except BackendRejected:
            controls.increment("tool_errors_total")
            return {"success": False, "error": "ITSM rejected the request"}
        except BackendUnavailable:
            controls.increment("tool_errors_total")
            logger.exception("ITSM backend unavailable tool=%s", name)
            return {"success": False, "error": "ITSM service is temporarily unavailable"}

    @mcp.tool()
    def create_ticket(
        username: str, title: str, description: str, idempotency_key: str,
        iam_request_id: int | None = None,
    ) -> dict[str, Any]:
        """Create an idempotent ITSM ticket; reuse the key for retries."""
        username = service.normalize_username(username)
        if username is None or not title.strip() or not description.strip() or not idempotency_key.strip():
            return {"success": False, "error": "username, title, description and idempotency_key are required"}
        if len(title.strip()) > 200 or len(description.strip()) > 2000 or len(idempotency_key.strip()) > 200:
            return {"success": False, "error": "Ticket input is too long"}
        if iam_request_id is not None and iam_request_id < 1:
            return {"success": False, "error": "iam_request_id must be positive"}
        return execute("create_ticket", "itsm:create", lambda: service.create_ticket(
            username, title.strip(), description.strip(), idempotency_key.strip(), iam_request_id,
        ))

    @mcp.tool()
    def get_ticket(ticket_id: int) -> dict[str, Any]:
        """Retrieve a ticket by ID."""
        if ticket_id < 1:
            return {"success": False, "error": "ticket_id must be positive"}
        return execute("get_ticket", "itsm:read", lambda: service.get_ticket(ticket_id))

    @mcp.tool()
    def list_user_tickets(username: str) -> dict[str, Any]:
        """List tickets belonging to a user."""
        username = service.normalize_username(username)
        if username is None:
            return {"success": False, "error": "username is required and must be at most 128 characters"}
        return execute("list_user_tickets", "itsm:read", lambda: service.list_user_tickets(username))
