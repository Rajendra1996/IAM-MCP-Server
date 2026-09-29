"""Request correlation and structured, secret-free operational events."""

import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def correlation_id(raw: str | None) -> str:
    return raw if raw and _SAFE_ID.fullmatch(raw) else uuid4().hex


def emit(logger: logging.Logger, service: str, status: str, **fields):
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "instance_id": settings.instance_id,
        "request_id": request_id_var.get(),
        "result_status": status,
    }
    event.update({key: value for key, value in fields.items() if value is not None})
    logger.info(json.dumps(event, separators=(",", ":"), default=str))


class CorrelationMiddleware:
    def __init__(self, app, service: str):
        self.app = app
        self.service = service
        self.logger = logging.getLogger("correlation")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = list(scope.get("headers", []))
        incoming = next((value.decode("latin-1") for key, value in headers
                         if key.lower() == b"x-request-id"), None)
        request_id = correlation_id(incoming)
        headers = [(key, value) for key, value in headers if key.lower() != b"x-request-id"]
        headers.append((b"x-request-id", request_id.encode("ascii")))
        scope = {**scope, "headers": headers}
        token = request_id_var.set(request_id)
        started = time.monotonic()
        response_status = 500

        async def forward(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, forward)
        finally:
            emit(
                self.logger, self.service, str(response_status),
                path=scope.get("path"), latency_ms=round((time.monotonic()-started)*1000, 2),
            )
            request_id_var.reset(token)
