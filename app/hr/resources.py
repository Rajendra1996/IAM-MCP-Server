from typing import Any

from app.hr import service
from app.shared.http_client import BackendRejected, BackendUnavailable


def register_resources(mcp):
    @mcp.resource("employee://{username}", description="HR employee record", mime_type="application/json")
    def employee_resource(username: str) -> dict[str, Any]:
        username = service.normalize_username(username)
        if username is None:
            return {"found": False, "error": "Invalid username"}
        try:
            return service.search_employee(username)
        except (BackendUnavailable, BackendRejected):
            return {"success": False, "error": "HR service is temporarily unavailable"}
