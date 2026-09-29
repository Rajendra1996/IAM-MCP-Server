"""Authenticated IAM MCP tools backed by REST and pooled PostgreSQL."""

import asyncio
import inspect
import logging
import os
from contextlib import asynccontextmanager
from functools import wraps
from threading import Lock
from typing import Any

import psycopg
from psycopg_pool import PoolClosed, PoolTimeout
from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.mcpserver import MCPServer, Context
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from app.core.config import settings
from app.shared.rate_limit import RedisRateLimiter, RateLimitUnavailable
from app.shared.redis_client import redis_health
from app.shared.observability import CorrelationMiddleware

from app.database.repository import (
    DecisionConflict, IdempotencyConflict, init_db, create_access_request,
    get_access_request, get_access_requests_paginated,
    update_access_request_decision, get_audit_logs, database_health, get_pool_stats,
)
from app.iam.api_client import IAMAPIError, get_user, get_application, api_health
from app.database.models import AccessRequest, AccessRequestPage
from app.shared.auth import verify_jwt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("iam-mcp")
HOST = settings.iam_mcp_host
PORT = settings.iam_mcp_port
RESOURCE_URL = settings.iam_mcp_url

METRICS = {
    "tool_calls_total": 0,
    "tool_errors_total": 0,
    "rate_limit_rejections": 0,
    "authorization_rejections": 0,
}
metrics_lock = Lock()


def increment_metric(name: str):
    with metrics_lock:
        METRICS[name] = METRICS.get(name, 0) + 1


class IAMTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        payload = verify_jwt(token)
        if not payload or not all(
            isinstance(payload.get(field), str) and payload[field]
            for field in ("sub", "client_id")
        ) or not isinstance(payload.get("scope", ""), str):
            logger.warning("Invalid JWT received")
            return None
        return AccessToken(
            token=token, client_id=payload["client_id"], subject=payload["sub"],
            scopes=payload.get("scope", "").split(), resource=RESOURCE_URL,
            expires_at=payload.get("exp"),
        )


@asynccontextmanager
async def lifespan(server: MCPServer):
    try:
        await run_in_threadpool(init_db)
    except Exception:
        # Health remains available during an outage; operations retry initialization.
        logger.exception("PostgreSQL initialization failed")
    yield {}
    # The process-level pool and HTTP client close through their atexit handlers.


mcp = MCPServer(
    "IAM MCP Server", token_verifier=IAMTokenVerifier(), lifespan=lifespan,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        resource_server_url=AnyHttpUrl(RESOURCE_URL),
        required_scopes=["iam:basic"], validate_token_resource=True,
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    db_ok, api_ok = await asyncio.gather(
        run_in_threadpool(database_health), run_in_threadpool(api_health),
    )
    healthy = db_ok and api_ok
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "service": "iam-mcp-server",
            "database": "ok" if db_ok else "down",
            "iam_api": "ok" if api_ok else "down",
        },
        status_code=200 if healthy else 503,
    )


@mcp.custom_route("/live", methods=["GET"])
async def live(request: Request):
    return JSONResponse({"status": "ok", "service": "iam-mcp-server", "instance_id": settings.instance_id})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(request: Request):
    db_ok, api_ok, redis_ok = await asyncio.gather(
        run_in_threadpool(database_health), run_in_threadpool(api_health),
        run_in_threadpool(redis_health),
    )
    ok = db_ok and api_ok and redis_ok
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "database": "ok" if db_ok else "down",
         "iam_api": "ok" if api_ok else "down", "redis": "ok" if redis_ok else "down",
         "instance_id": settings.instance_id},
        status_code=200 if ok else 503,
    )


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(request: Request):
    with metrics_lock:
        snapshot = dict(METRICS)
    return JSONResponse({
        **snapshot, "mcp": snapshot, "database_pool": get_pool_stats(),
        "redis": "ok" if redis_health() else "down",
        "instance_id": settings.instance_id,
    })


def current_token():
    return get_access_token()


def current_identity() -> dict | None:
    token = current_token()
    if not token:
        return None
    return {
        "client_id": token.client_id, "subject": token.subject,
        "scopes": token.scopes,
    }


def has_scope(scope: str) -> bool:
    token = current_token()
    return bool(token and scope in token.scopes)


BASIC_TOOLS = {
    "whoami", "search_user", "search_application", "request_access",
    "get_access_request_by_id", "list_access_requests", "list_audit_logs",
    "analyze_access_requests",
}
APPROVER_TOOLS = BASIC_TOOLS | {"approve_access_request", "reject_access_request"}


def tool_allowed(tool_name: str) -> bool:
    token = current_token()
    if not token or "iam:basic" not in token.scopes:
        return False
    allowed = APPROVER_TOOLS if "iam:approve" in token.scopes else BASIC_TOOLS
    return tool_name in allowed


def authorize_tool(tool_name: str) -> dict | None:
    if tool_allowed(tool_name):
        return None
    increment_metric("authorization_rejections")
    logger.warning("Tool authorization rejected tool=%s", tool_name)
    return {
        "success": False,
        "error": f"You are not authorized to use tool '{tool_name}'",
    }


RATE_LIMIT = settings.rate_limit
RATE_WINDOW_SECONDS = settings.rate_window_seconds
limiter = RedisRateLimiter("iam", RATE_LIMIT, RATE_WINDOW_SECONDS)


def check_rate_limit() -> bool:
    token = current_token()
    subject = token.subject if token else "anonymous"
    return limiter.check(subject)


def before_tool(tool_name: str) -> dict | None:
    increment_metric("tool_calls_total")
    try:
        allowed = check_rate_limit()
    except RateLimitUnavailable:
        increment_metric("tool_errors_total")
        return {"success": False, "error": "Shared rate limit is temporarily unavailable"}
    if not allowed:
        increment_metric("rate_limit_rejections")
        logger.warning("Rate limit exceeded tool=%s", tool_name)
        return {"success": False, "error": "Rate limit exceeded. Try again shortly."}
    return authorize_tool(tool_name)


def normalize_required(
    value: str | None, field_name: str,
) -> tuple[str | None, dict | None]:
    if not value or not value.strip():
        return None, {"success": False, "error": f"{field_name} is required"}
    if len(value.strip()) > 128:
        return None, {"success": False, "error": f"{field_name} must be at most 128 characters"}
    return value.strip().lower(), None


def backend_errors(fn):
    """Keep downstream exceptions in server logs, including resource failures."""
    def failure(exc):
        increment_metric("tool_errors_total")
        if isinstance(exc, (IdempotencyConflict, DecisionConflict)):
            message = str(exc)
        else:
            logger.exception("Backend operation failed operation=%s", fn.__name__)
            message = (
                "IAM service is temporarily unavailable"
                if isinstance(exc, IAMAPIError)
                else "IAM request storage is temporarily unavailable"
            )
        if fn.__name__ == "list_access_requests":
            return AccessRequestPage(requests=[], count=0, success=False, error=message)
        return {"success": False, "error": message}

    errors = (IAMAPIError, psycopg.Error, PoolTimeout, PoolClosed,
              IdempotencyConflict, DecisionConflict)
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except errors as exc:
                return failure(exc)
        return async_wrapper

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except errors as exc:
            return failure(exc)
    return wrapper


@mcp.tool()
def whoami() -> dict[str, Any]:
    """Return authenticated identity."""
    error = before_tool("whoami")
    if error:
        return error
    return {"authenticated": True, **current_identity()}


@mcp.tool()
@backend_errors
def search_user(username: str | None = None) -> dict[str, Any]:
    """Search IAM user by username through the REST backend."""
    error = before_tool("search_user")
    if error:
        return error
    username, error = normalize_required(username, "username")
    if error:
        return error
    user = get_user(username)
    if user is None:
        return {"found": False, "message": f"User '{username}' was not found."}
    return {"found": True, "user": user}


@mcp.tool()
@backend_errors
def search_application(app_name: str | None = None) -> dict[str, Any]:
    """Search IAM application through the REST backend."""
    error = before_tool("search_application")
    if error:
        return error
    app_name, error = normalize_required(app_name, "app_name")
    if error:
        return error
    application = get_application(app_name)
    if application is None:
        return {"found": False, "message": f"Application '{app_name}' was not found."}
    return {"found": True, "application": application}


@mcp.tool()
@backend_errors
def request_access(username: str, app_name: str, idempotency_key: str) -> dict[str, Any]:
    """Create an access request; reuse the same key when retrying the same request."""
    error = before_tool("request_access")
    if error:
        return error
    username, error = normalize_required(username, "username")
    if error:
        return error
    app_name, error = normalize_required(app_name, "app_name")
    if error:
        return error
    if not idempotency_key.strip():
        return {"success": False, "error": "idempotency_key is required"}
    idempotency_key = idempotency_key.strip()
    if len(idempotency_key) > 200:
        return {"success": False, "error": "idempotency_key must be at most 200 characters"}
    user = get_user(username)
    if user is None:
        return {"success": False, "error": f"User '{username}' was not found"}
    if not user["active"]:
        return {"success": False, "error": f"User '{username}' is inactive"}
    application = get_application(app_name)
    if application is None:
        return {"success": False, "error": f"Application '{app_name}' was not found"}
    if not application["active"]:
        return {"success": False, "error": f"Application '{app_name}' is inactive"}
    risk_level = application["risk_level"]
    status = "NEEDS_APPROVAL" if risk_level == "High" else "PENDING"
    identity = current_identity()
    request, created_new = create_access_request(
        username=username, application=application["name"], status=status,
        idempotency_key=idempotency_key,
        actor=identity["subject"] if identity else username,
        details=f"Access requested for {application['name']} risk={risk_level}",
    )
    logger.info("Access request request_id=%s created_new=%s", request["request_id"], created_new)
    return {
        "success": True, "duplicate": not created_new, "risk_level": risk_level,
        "requires_approval": request["status"] == "NEEDS_APPROVAL", "request": request,
    }


@mcp.tool()
@backend_errors
def get_access_request_by_id(request_id: int | None = None) -> dict[str, Any]:
    """Get one access request."""
    error = before_tool("get_access_request_by_id")
    if error:
        return error
    if request_id is None or request_id < 1:
        return {"found": False, "error": "A positive request_id is required"}
    request = get_access_request(request_id)
    if request is None:
        return {"found": False, "error": f"Request '{request_id}' was not found"}
    return {"found": True, "request": request}


@mcp.tool()
@backend_errors
def list_access_requests(limit: int = 10, cursor: str | None = None) -> AccessRequestPage:
    """List access requests using a request-ID cursor."""
    error = before_tool("list_access_requests")
    if error:
        return AccessRequestPage(requests=[], count=0, success=False, error=error["error"])
    cursor_id = None
    if cursor:
        try:
            cursor_id = int(cursor)
            if cursor_id < 0:
                raise ValueError
        except ValueError:
            return AccessRequestPage(
                requests=[], count=0, success=False, error="Invalid request cursor",
            )
    rows, next_cursor = get_access_requests_paginated(limit=limit, cursor=cursor_id)
    requests = [AccessRequest(**row) for row in rows]
    return AccessRequestPage(requests=requests, count=len(requests), next_cursor=next_cursor)


def _decide(request_id, approver, reason, status):
    if not has_scope("iam:approve"):
        increment_metric("authorization_rejections")
        return {"success": False, "error": "iam:approve scope required"}
    if request_id is None or request_id < 1:
        return {"success": False, "error": "A positive request_id is required"}
    approver, error = normalize_required(approver, "approver")
    if error:
        return error
    if not reason or not reason.strip():
        if status == "REJECTED":
            return {"success": False, "error": "reason is required"}
        reason = "Approved"
    if len(reason.strip()) > 2000:
        return {"success": False, "error": "reason must be at most 2000 characters"}
    updated = update_access_request_decision(request_id, status, approver, reason.strip())
    if updated is None:
        return {"success": False, "error": f"Request '{request_id}' was not found"}
    return {
        "success": True,
        "message": "Request approved" if status == "APPROVED" else "Request rejected",
        "request": updated,
    }


@mcp.tool()
@backend_errors
def approve_access_request(
    request_id: int | None = None, approver: str | None = None, reason: str | None = None,
) -> dict[str, Any]:
    """Approve an access request. Requires iam:approve and human approval in the agent."""
    error = before_tool("approve_access_request")
    return error if error else _decide(request_id, approver, reason, "APPROVED")


@mcp.tool()
@backend_errors
def reject_access_request(
    request_id: int | None = None, approver: str | None = None, reason: str | None = None,
) -> dict[str, Any]:
    """Reject an access request. Requires iam:approve and a reason."""
    error = before_tool("reject_access_request")
    return error if error else _decide(request_id, approver, reason, "REJECTED")


@mcp.tool()
@backend_errors
def list_audit_logs(request_id: int | None = None) -> dict[str, Any]:
    """List audit logs, optionally filtered by request."""
    error = before_tool("list_audit_logs")
    if error:
        return error
    logs = get_audit_logs(request_id=request_id)
    return {"count": len(logs), "logs": logs}


@mcp.tool()
@backend_errors
async def analyze_access_requests(ctx: Context) -> dict[str, Any]:
    """Analyze requests with progress."""
    error = before_tool("analyze_access_requests")
    if error:
        return error
    rows, _ = await run_in_threadpool(get_access_requests_paginated, limit=100, cursor=None)
    counts = {"PENDING": 0, "NEEDS_APPROVAL": 0, "APPROVED": 0, "REJECTED": 0}
    for index, request in enumerate(rows, start=1):
        if request["status"] in counts:
            counts[request["status"]] += 1
        await ctx.report_progress(
            progress=index, total=len(rows),
            message=f"Analyzed request {request['request_id']}",
        )
    return {"total": len(rows), "status_counts": counts}


@mcp.resource("policy://access-policy", description="IAM access policy", mime_type="text/plain")
def access_policy() -> str:
    return """IAM Access Policy

1. User must exist and be active.
2. Application must exist and be active.
3. High-risk applications require approval.
4. Least privilege must be followed.
5. Requests must be auditable.
6. Rejections require a reason."""


@mcp.resource("user://{username}", description="Read IAM user information", mime_type="application/json")
@backend_errors
def user_resource(username: str) -> dict:
    username = username.strip().lower()
    user = get_user(username)
    if user is None:
        return {"found": False, "message": f"User '{username}' was not found"}
    return {"found": True, "user": user}


@mcp.resource("request://{request_id}", description="Read IAM request", mime_type="application/json")
@backend_errors
def request_resource(request_id: str) -> dict:
    try:
        numeric_id = int(request_id)
        if numeric_id < 1:
            raise ValueError
    except ValueError:
        return {"found": False, "error": "Invalid request ID"}
    request = get_access_request(numeric_id)
    if request is None:
        return {"found": False, "error": f"Request '{request_id}' was not found"}
    return {"found": True, "request": request}


@mcp.prompt(name="review_access_request", description="Generate IAM review instructions")
def review_access_request(username: str, app_name: str) -> str:
    return f"""Review this IAM access request.

User:
{username}

Application:
{app_name}

Check:
1. User exists and is active.
2. Application exists and is active.
3. Application risk.
4. Least privilege.
5. Whether human approval is required.
6. Any security concerns.

Do not approve or reject automatically."""


# SSE responses carry progress notifications on the Streamable HTTP request.
app = mcp.streamable_http_app(
    stateless_http=True, json_response=False, host=HOST,
    max_request_body_size=262144,
)
app.add_middleware(CorrelationMiddleware, service="iam-mcp")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
