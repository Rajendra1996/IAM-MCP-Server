"""Gateway route allowlist and defense-in-depth JWT scope checks."""

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.shared.auth import verify_jwt

ALLOWED_MCP_SERVERS = {"iam", "hr", "itsm"}
ROUTE_SCOPES = {
    "iam": {"iam:basic"},
    "hr": {"hr:read"},
    "itsm": {"itsm:read", "itsm:create"},
}
MAX_REQUEST_BYTES = 262144


def authorize_route(request: Request, service: str) -> tuple[dict | None, JSONResponse | None]:
    if service not in ALLOWED_MCP_SERVERS:
        return None, JSONResponse({"error": "Unknown MCP route"}, status_code=404)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, JSONResponse({"error": "Bearer JWT required"}, status_code=401)
    payload = verify_jwt(token)
    if payload is None:
        return None, JSONResponse({"error": "Invalid bearer JWT"}, status_code=401)
    raw_scope = payload.get("scope", "")
    if not isinstance(raw_scope, str):
        return None, JSONResponse({"error": "Invalid JWT scope"}, status_code=401)
    scopes = set(raw_scope.split())
    if not scopes.intersection(ROUTE_SCOPES[service]):
        return None, JSONResponse({"error": "Scope not authorized for route"}, status_code=403)
    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > MAX_REQUEST_BYTES:
                return None, JSONResponse({"error": "Request body too large"}, status_code=413)
        except ValueError:
            return None, JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
    return payload, None
