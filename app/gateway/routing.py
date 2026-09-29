"""Transparent, bounded MCP HTTP reverse-proxy transport."""

import logging

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from app.gateway.policy import MAX_REQUEST_BYTES
from app.shared.observability import emit

logger = logging.getLogger(__name__)
_HOP_HEADERS = {
    "host", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
    "content-length",
}


def proxy_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2, read=30, write=5, pool=2),
        trust_env=False, follow_redirects=False,
    )


async def forward_mcp(request: Request, target: str, client: httpx.AsyncClient,
                      service: str) -> StreamingResponse | JSONResponse:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            return JSONResponse({"error": "Request body too large"}, status_code=413)
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS
    }
    upstream_request = client.build_request(
        request.method, target, headers=headers, content=bytes(body),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        emit(logger, service, "unavailable", target=service)
        return JSONResponse({"error": "MCP backend is temporarily unavailable"}, status_code=503)

    async def content():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response_headers = {
        key: value for key, value in upstream.headers.items()
        if key.lower() not in _HOP_HEADERS and key.lower() != "x-request-id"
    }
    emit(logger, service, str(upstream.status_code), target=service)
    return StreamingResponse(
        content(), status_code=upstream.status_code, headers=response_headers,
    )
