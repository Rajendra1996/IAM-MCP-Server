import logging
from typing import Any

from app.hr import service
from app.shared.http_client import BackendRejected, BackendUnavailable

logger = logging.getLogger(__name__)


def register_tools(mcp, controls):
    def run(name, username, operation):
        error = controls.before_tool(name, "hr:read")
        if error:
            return error
        username = service.normalize_username(username)
        if username is None:
            return {"success": False, "error": "username is required and must be at most 128 characters"}
        try:
            return operation(username)
        except (BackendUnavailable, BackendRejected):
            controls.increment("tool_errors_total")
            logger.exception("HR lookup failed tool=%s", name)
            return {"success": False, "error": "HR service is temporarily unavailable"}

    @mcp.tool()
    def search_employee(username: str) -> dict[str, Any]:
        """Look up an HR employee by username."""
        return run("search_employee", username, service.search_employee)

    @mcp.tool()
    def get_employee_manager(username: str) -> dict[str, Any]:
        """Get an employee's manager from HR."""
        return run("get_employee_manager", username, service.employee_manager)

    @mcp.tool()
    def get_employee_department(username: str) -> dict[str, Any]:
        """Get an employee's department from HR."""
        return run("get_employee_department", username, service.employee_department)
