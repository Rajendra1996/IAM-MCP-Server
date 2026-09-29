"""Read-only HR MCP service on port 8002."""

import asyncio

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.hr.api_client import api_health
from app.hr.resources import register_resources
from app.hr.tools import register_tools
from app.shared.mcp_service import ServiceControls, create_mcp
from app.shared.redis_client import redis_health
from app.shared.observability import CorrelationMiddleware

mcp = create_mcp("HR MCP Server", settings.hr_mcp_url, ["hr:read"])
controls = ServiceControls("hr")
register_tools(mcp, controls)
register_resources(mcp)


@mcp.custom_route("/live", methods=["GET"])
async def live(request: Request):
    return JSONResponse({"status": "ok", "service": "hr-mcp"})


@mcp.custom_route("/ready", methods=["GET"])
@mcp.custom_route("/health", methods=["GET"])
async def ready(request: Request):
    api_ok, redis_ok = await asyncio.gather(
        run_in_threadpool(api_health), run_in_threadpool(redis_health),
    )
    ok = api_ok and redis_ok
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "hr_api": "ok" if api_ok else "down",
         "redis": "ok" if redis_ok else "down"},
        status_code=200 if ok else 503,
    )


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(request: Request):
    return JSONResponse({"mcp": controls.snapshot(), "redis": "ok" if redis_health() else "down"})


app = mcp.streamable_http_app(
    stateless_http=True, json_response=False, host=settings.hr_mcp_host,
    max_request_body_size=262144,
)
app.add_middleware(CorrelationMiddleware, service="hr-mcp")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.hr_mcp_host, port=settings.hr_mcp_port)
