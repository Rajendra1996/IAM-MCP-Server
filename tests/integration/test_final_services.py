"""Live protocol and shared-infrastructure tests; run with local stack started."""

import asyncio
import time
from uuid import uuid4

import httpx
import jwt
import pytest

from app.core.config import settings
from app.shared.auth import JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET, create_token
from app.shared.rate_limit import RedisRateLimiter
from app.shared.redis_client import client as redis_client
from clients.multi_mcp_client import MultiMCPClient, request_access_workflow

pytestmark = pytest.mark.integration
URLS = {service: f"http://127.0.0.1:8080/{service}/mcp"
        for service in ("iam", "hr", "itsm")}


def token(scopes=None):
    return create_token(f"final-subject-{uuid4().hex}", f"final-{uuid4().hex}", scopes or [
        "iam:basic", "hr:read", "itsm:read", "itsm:create",
    ])


def test_live_services_protocol_discovery_and_resources():
    async def run():
        async with MultiMCPClient(token(), URLS) as multi:
            for client in multi.clients.values():
                assert client.session.protocol_version == "2026-07-28"
            tools = await multi.discover()
            assert {"hr_search_employee", "hr_get_employee_manager",
                    "hr_get_employee_department"} <= set(tools)
            assert {"itsm_create_ticket", "itsm_get_ticket",
                    "itsm_list_user_tickets"} <= set(tools)
            assert "iam_request_access" in tools
            assert (await multi.call_tool("hr_search_employee", {"username": "alice"})).structured_content["found"]
            assert (await multi.call_tool("iam_search_application", {"app_name": "sap"})).structured_content["found"]
            employee = await multi.clients["hr"].read_resource("employee://alice")
            assert "alice" in employee.contents[0].text
    asyncio.run(run())


def test_cross_server_workflow_and_idempotency():
    async def run():
        async with MultiMCPClient(token(), URLS) as multi:
            key = f"final-flow-{uuid4().hex}"
            first = await request_access_workflow(multi, "alice", "sap", key)
            assert first["success"] and first["stage"] == "complete"
            assert first["employee"]["employee"]["username"] == "alice"
            assert first["manager"]["manager"] == "jane"
            assert first["access"]["request"]["status"] == "NEEDS_APPROVAL"
            assert first["ticket"]["ticket"]["username"] == "alice"
            again = await request_access_workflow(multi, "alice", "sap", key)
            assert again["access"]["duplicate"] and again["ticket"]["duplicate"]
            assert again["access"]["request"]["request_id"] == first["access"]["request"]["request_id"]
            assert again["ticket"]["ticket"]["ticket_id"] == first["ticket"]["ticket"]["ticket_id"]
    asyncio.run(run())


@pytest.mark.parametrize("path,expected", [
    ("/admin/mcp", 404), ("/iam/mcp", 401),
])
def test_gateway_unknown_and_missing_jwt(path, expected):
    response = httpx.get("http://127.0.0.1:8080" + path, timeout=5, trust_env=False)
    assert response.status_code == expected


def test_gateway_wrong_scope_invalid_expired_audience_and_size():
    base = "http://127.0.0.1:8080"
    with httpx.Client(timeout=5, trust_env=False) as client:
        assert client.get(base + "/hr/mcp", headers={"Authorization": "Bearer " + token(["iam:basic"])}).status_code == 403
        assert client.get(base + "/iam/mcp", headers={"Authorization": "Bearer invalid"}).status_code == 401
        for changes in ({"exp": 1}, {"aud": "wrong-audience"}):
            payload = {"sub": "alice", "client_id": "test", "scope": "iam:basic",
                       "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "exp": int(time.time()) + 60}
            payload.update(changes)
            bad = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
            assert client.get(base + "/iam/mcp", headers={"Authorization": "Bearer " + bad}).status_code == 401
        response = client.post(base + "/iam/mcp", headers={"Authorization": "Bearer " + token()},
                               content=b"x" * 262145)
        assert response.status_code == 413


def test_correlation_header_and_lb_distribution():
    headers = {"Authorization": "Bearer " + token(), "X-Request-ID": "test-correlation-123"}
    with httpx.Client(timeout=5, trust_env=False) as client:
        # Streamable HTTP GET may remain open as an SSE stream; inspect headers only.
        with client.stream("GET", "http://127.0.0.1:8080/iam/mcp", headers=headers) as response:
            assert response.headers["x-request-id"] == "test-correlation-123"
    async def run():
        async with MultiMCPClient(token(), URLS) as multi:
            for _ in range(10):
                assert (await multi.call("iam", "whoami", {})).structured_content["authenticated"]
    asyncio.run(run())
    counts = httpx.get("http://127.0.0.1:8081/metrics", trust_env=False).json()["replica_requests"]
    assert counts["iam-a"] >= 5 and counts["iam-b"] >= 5


def test_redis_shared_limit_and_ttl():
    assert redis_client.ping()
    limiter_a = RedisRateLimiter("test-shared", limit=2, window=1)
    limiter_b = RedisRateLimiter("test-shared", limit=2, window=1)
    subject = "test-" + uuid4().hex
    assert limiter_a.check(subject)
    assert limiter_b.check(subject)
    assert not limiter_a.check(subject)
    keys = list(redis_client.scan_iter("ratelimit:test-shared:*"))
    assert keys and all(redis_client.ttl(key) > 0 for key in keys)
    time.sleep(3)
    assert not list(redis_client.scan_iter("ratelimit:test-shared:*"))


def test_two_iam_replicas_enforce_one_subject_limit():
    shared_token = create_token("rate-" + uuid4().hex, "replica-limit-test", ["iam:basic"])
    async def run():
        async with MultiMCPClient(shared_token, {"iam": "http://127.0.0.1:8011/mcp"}) as a:
            async with MultiMCPClient(shared_token, {"iam": "http://127.0.0.1:8012/mcp"}) as b:
                results = []
                for index in range(settings.rate_limit + 1):
                    replica = a if index % 2 == 0 else b
                    results.append((await replica.call("iam", "whoami", {})).structured_content)
                assert sum(item.get("authenticated", False) for item in results) == settings.rate_limit
                assert "Rate limit exceeded" in results[-1]["error"]
    asyncio.run(run())
