"""Small stateless request-level round-robin proxy for two IAM replicas."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import settings
from app.gateway.routing import forward_mcp, proxy_client
from app.shared.observability import CorrelationMiddleware, emit

logger = logging.getLogger(__name__)
REPLICAS = {
    "iam-a": settings.iam_replica_a_url,
    "iam-b": settings.iam_replica_b_url,
}
COUNTS = {name: 0 for name in REPLICAS}
ERRORS = {name: 0 for name in REPLICAS}
_lock = asyncio.Lock()
_next = 0


@asynccontextmanager
async def lifespan(app: Starlette):
    async with proxy_client() as client:
        app.state.client = client
        yield


async def healthy_replicas(client):
    async def probe(name, url):
        try:
            response = await client.get(url + "/ready", timeout=3)
            return name, response.status_code == 200
        except httpx.HTTPError:
            return name, False
    results = dict(await asyncio.gather(*(probe(name, url) for name, url in REPLICAS.items())))
    return results


async def live(request: Request):
    return JSONResponse({"status": "ok", "service": "iam-load-balancer"})


async def ready(request: Request):
    health = await healthy_replicas(request.app.state.client)
    ok = any(health.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded",
         "replicas": {name: "ok" if healthy else "down" for name, healthy in health.items()}},
        status_code=200 if ok else 503,
    )


async def metrics(request: Request):
    return JSONResponse({"replica_requests": dict(COUNTS), "replica_errors": dict(ERRORS)})


async def mcp_route(request: Request):
    global _next
    health = await healthy_replicas(request.app.state.client)
    available = [name for name in REPLICAS if health[name]]
    if not available:
        return JSONResponse({"error": "No IAM replica is ready"}, status_code=503)
    async with _lock:
        # Rotate on every request, but choose only from currently ready replicas.
        ordered = list(REPLICAS)
        selected = next((name for name in ordered[_next:] + ordered[:_next] if name in available))
        _next = (ordered.index(selected) + 1) % len(ordered)
        COUNTS[selected] += 1
    emit(logger, "iam-load-balancer", "routed", replica=selected)
    response = await forward_mcp(
        request, REPLICAS[selected] + "/mcp", request.app.state.client,
        "iam-load-balancer",
    )
    if response.status_code >= 500:
        ERRORS[selected] += 1
    return response


app = Starlette(
    routes=[Route("/live", live), Route("/ready", ready), Route("/metrics", metrics),
            Route("/mcp", mcp_route, methods=["GET", "POST", "DELETE"])],
    lifespan=lifespan,
)
app.add_middleware(CorrelationMiddleware, service="iam-load-balancer")


def main():
    import uvicorn
    uvicorn.run(app, host=settings.gateway_host, port=8081)


if __name__ == "__main__":
    main()
