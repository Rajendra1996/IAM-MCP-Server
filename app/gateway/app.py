"""Policy-enforcing MCP gateway on port 8080."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import settings
from app.gateway.policy import authorize_route
from app.gateway.routing import forward_mcp, proxy_client
from app.shared.observability import CorrelationMiddleware, emit

logger = logging.getLogger(__name__)
BACKENDS = {
    "iam": settings.iam_backend_url,
    "hr": settings.hr_backend_url,
    "itsm": settings.itsm_backend_url,
}
METRICS = {"gateway_requests_total": 0, "gateway_errors_total": 0,
           "authorization_rejections_total": 0}


@asynccontextmanager
async def lifespan(app: Starlette):
    async with proxy_client() as client:
        app.state.client = client
        yield


async def live(request: Request):
    return JSONResponse({"status": "ok", "service": "mcp-gateway"})


async def ready(request: Request):
    async def probe(service, url):
        try:
            response = await request.app.state.client.get(url + "/ready")
            return service, response.status_code == 200
        except httpx.HTTPError:
            return service, False
    results = dict(await asyncio.gather(*(probe(s, u) for s, u in BACKENDS.items())))
    ok = all(results.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded",
         "backends": {name: "ok" if healthy else "down" for name, healthy in results.items()}},
        status_code=200 if ok else 503,
    )


async def metrics(request: Request):
    return JSONResponse({"gateway": dict(METRICS)})


async def mcp_route(request: Request):
    METRICS["gateway_requests_total"] += 1
    service = request.path_params["service"]
    identity, error = authorize_route(request, service)
    if error:
        METRICS["gateway_errors_total"] += 1
        if error.status_code in (401, 403):
            METRICS["authorization_rejections_total"] += 1
        emit(logger, "gateway", str(error.status_code), route=service)
        return error
    emit(logger, "gateway", "routed", actor=identity.get("sub"), route=service)
    response = await forward_mcp(
        request, BACKENDS[service] + "/mcp", request.app.state.client, "gateway",
    )
    if response.status_code >= 400:
        METRICS["gateway_errors_total"] += 1
    return response


app = Starlette(
    routes=[
        Route("/live", live), Route("/ready", ready), Route("/metrics", metrics),
        Route("/{service}/mcp", mcp_route, methods=["GET", "POST", "DELETE"]),
    ],
    lifespan=lifespan,
)
app.add_middleware(CorrelationMiddleware, service="gateway")


def main():
    import uvicorn
    uvicorn.run(app, host=settings.gateway_host, port=settings.gateway_port)


if __name__ == "__main__":
    main()
