"""Common authentication, authorization and rate-limit controls for MCP servers."""

import logging
from threading import Lock

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl

from app.shared.auth import JWTTokenVerifier
from app.shared.rate_limit import RedisRateLimiter, RateLimitUnavailable

logger = logging.getLogger(__name__)


def create_mcp(name: str, resource_url: str, required_scopes: list[str]) -> MCPServer:
    return MCPServer(
        name,
        token_verifier=JWTTokenVerifier(resource_url),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.example.com"),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=required_scopes,
            validate_token_resource=True,
        ),
    )


class ServiceControls:
    def __init__(self, service: str):
        self.service = service
        self.limiter = RedisRateLimiter(service)
        self.metrics = {
            "tool_calls_total": 0,
            "tool_errors_total": 0,
            "authorization_rejections": 0,
            "rate_limit_rejections": 0,
        }
        self._lock = Lock()

    def increment(self, name: str):
        with self._lock:
            self.metrics[name] += 1

    def snapshot(self):
        with self._lock:
            return dict(self.metrics)

    def before_tool(self, name: str, required_scope: str) -> dict | None:
        self.increment("tool_calls_total")
        token = get_access_token()
        if token is None or required_scope not in token.scopes:
            self.increment("authorization_rejections")
            logger.warning("Tool scope rejected service=%s tool=%s", self.service, name)
            return {"success": False, "error": f"{required_scope} scope required"}
        try:
            allowed = self.limiter.check(token.subject or token.client_id)
        except RateLimitUnavailable:
            self.increment("tool_errors_total")
            return {"success": False, "error": "Shared rate limit is temporarily unavailable"}
        if not allowed:
            self.increment("rate_limit_rejections")
            return {"success": False, "error": "Rate limit exceeded. Try again shortly."}
        return None
