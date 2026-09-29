import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.database import repository as db
from clients.remote_client import connect_client
from app.shared.auth import create_token, verify_jwt, JWT_SECRET, JWT_ALGORITHM
import jwt

pytestmark = pytest.mark.integration
MCP_URL = os.getenv("IAM_MCP_URL", "http://127.0.0.1:8001/mcp")
BASE_URL = MCP_URL.rsplit("/mcp", 1)[0]


def token(scopes=None, expires_minutes=60):
    return create_token(
        subject=f"pytest.employee.{uuid4().hex}", client_id="pytest-client",
        scopes=scopes if scopes is not None else ["iam:basic"],
        expires_minutes=expires_minutes,
    )


def test_live_health_metrics_and_rest_lookup():
    response = httpx.get(BASE_URL + "/health", timeout=20)
    assert response.status_code == 200
    assert response.json()["database"] == response.json()["iam_api"] == "ok"
    metrics = httpx.get(BASE_URL + "/metrics").json()
    assert "mcp" in metrics and "database_pool" in metrics
    assert httpx.get(os.getenv("IAM_API_URL", "http://127.0.0.1:9000") + "/users/alice").json()["username"] == "alice"


@pytest.mark.parametrize("bearer,expected", [
    (None, 401), ("invalid-jwt", 401), ("expired", 401), ("no-basic", 403),
    ("wrong-audience", 401), ("wrong-issuer", 401), ("wrong-signature", 401),
])
def test_http_authentication_and_scope_enforcement(bearer, expected):
    if bearer == "expired":
        bearer = token(expires_minutes=-1)
    elif bearer == "no-basic":
        bearer = token(scopes=["iam:approve"])
    elif bearer in {"wrong-audience", "wrong-issuer", "wrong-signature"}:
        payload = verify_jwt(token())
        signing_key = JWT_SECRET
        if bearer == "wrong-audience":
            payload["aud"] = "another-server"
        elif bearer == "wrong-issuer":
            payload["iss"] = "another-issuer"
        else:
            signing_key = "a-different-test-key-at-least-32-bytes-long"
        bearer = jwt.encode(payload, signing_key, algorithm=JWT_ALGORITHM)
    headers = {"Accept": "application/json, text/event-stream"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    response = httpx.post(MCP_URL, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "1"}},
    })
    assert response.status_code == expected


def test_mcp_end_to_end_security_workflows_resources_and_progress():
    keys = [f"pytest-mcp-{uuid4().hex}" for _ in range(4)]
    ids = []

    async def flow():
        async with connect_client(token()) as employee:
            assert employee.session.protocol_version == "2026-07-28"
            tools = (await employee.list_tools()).tools
            assert len(tools) == 10
            request_tool = next(tool for tool in tools if tool.name == "request_access")
            assert "idempotency_key" in request_tool.input_schema["required"]
            from app.agent.orchestrator import get_openai_tools
            agent_tools = await get_openai_tools(employee)
            assert len(agent_tools) == 10
            assert next(t for t in agent_tools if t["name"] == "request_access")["parameters"] == request_tool.input_schema
            async def call(name, args=None, **kwargs):
                result = await employee.call_tool(name, args or {}, **kwargs)
                assert not result.is_error, result.content
                return result.structured_content

            assert (await call("whoami"))["scopes"] == ["iam:basic"]
            assert (await call("search_user", {"username": "alice"}))["found"] is True
            assert (await call("search_user", {"username": "notfound"}))["found"] is False
            assert (await call("search_application", {"app_name": "sap"}))["application"]["risk_level"] == "High"
            assert (await call("search_application", {"app_name": "notfound"}))["found"] is False
            args = {"username": "alice", "app_name": "sap", "idempotency_key": keys[0]}
            first = await call("request_access", args)
            ids.append(first["request"]["request_id"])
            assert first["success"] and first["requires_approval"]
            assert first["risk_level"] == "High" and first["request"]["status"] == "NEEDS_APPROVAL"
            assert first["duplicate"] is False
            duplicate = await call("request_access", args)
            assert duplicate["duplicate"] is True
            assert duplicate["request"]["request_id"] == ids[0]
            logs = db.get_audit_logs(ids[0])
            assert sum(log["action"] == "REQUEST_CREATED" for log in logs) == 1
            for tool in ("approve_access_request", "reject_access_request"):
                denied = await call(tool, {"request_id": ids[0], "approver": "jane", "reason": "Test"})
                assert denied["success"] is False and "not authorized" in denied["error"]
            assert db.get_access_request(ids[0])["status"] == "NEEDS_APPROVAL"
            assert (await call("get_access_request_by_id", {"request_id": ids[0]}))["found"]
            page = await call("list_access_requests", {"limit": 2})
            assert page["success"] and page["count"] <= 2
            assert (await call("list_audit_logs", {"request_id": ids[0]}))["count"] == 2
            assert "High-risk" in (await employee.read_resource("policy://access-policy")).contents[0].text
            assert json.loads((await employee.read_resource("user://alice")).contents[0].text)["found"]
            request_resource = await employee.read_resource(f"request://{ids[0]}")
            assert json.loads(request_resource.contents[0].text)["request"]["request_id"] == ids[0]
            prompt = await employee.get_prompt("review_access_request", {"username": "alice", "app_name": "sap"})
            assert "Do not approve or reject automatically" in prompt.messages[0].content.text
            progress = []
            async def report(value, total, message):
                progress.append((value, total, message))
            analysis = await call("analyze_access_requests", progress_callback=report)
            assert analysis["total"] >= 1 and progress
            conflict = await call("request_access", {**args, "username": "bob"})
            assert conflict["success"] is False and "different request" in conflict["error"]
            missing_key = await employee.call_tool("request_access", {"username": "alice", "app_name": "sap"})
            assert missing_key.is_error
            second = await call("request_access", {**args, "idempotency_key": keys[1]})
            ids.append(second["request"]["request_id"])
            for app_name, key in zip(["salesforce", "servicenow"], keys[2:]):
                low_risk = await call("request_access", {**args, "app_name": app_name, "idempotency_key": key})
                assert low_risk["success"] and not low_risk["requires_approval"]
                assert low_risk["request"]["status"] == "PENDING"

        async with connect_client(token(["iam:basic", "iam:approve"])) as approver:
            identity = (await approver.call_tool("whoami", {})).structured_content
            assert "iam:approve" in identity["scopes"]
            approved = (await approver.call_tool("approve_access_request", {
                "request_id": ids[0], "approver": "manager.jane", "reason": "Manager approved",
            })).structured_content
            assert approved["success"] and approved["request"]["status"] == "APPROVED"
            no_reason = (await approver.call_tool("reject_access_request", {
                "request_id": ids[1], "approver": "manager.jane",
            })).structured_content
            assert no_reason["success"] is False
            rejected = (await approver.call_tool("reject_access_request", {
                "request_id": ids[1], "approver": "manager.jane", "reason": "No business need",
            })).structured_content
            assert rejected["success"] and rejected["request"]["status"] == "REJECTED"
            for request_id, action in zip(ids, ["REQUEST_APPROVED", "REQUEST_REJECTED"]):
                assert db.get_audit_logs(request_id)[-1]["action"] == action

    try:
        asyncio.run(flow())
    finally:
        db.init_db()
        with db.pool.connection() as conn:
            for key in keys:
                conn.execute("DELETE FROM audit_logs WHERE request_id IN (SELECT request_id FROM access_requests WHERE idempotency_key = %s)", (key,))
                conn.execute("DELETE FROM access_requests WHERE idempotency_key = %s", (key,))


def test_mcp_rate_limiting():
    async def flow():
        async with connect_client(token()) as employee:
            for _ in range(20):
                result = await employee.call_tool("whoami", {})
                assert result.structured_content["authenticated"] is True
            rejected = (await employee.call_tool("whoami", {})).structured_content
            assert rejected["success"] is False and "Rate limit exceeded" in rejected["error"]
    asyncio.run(flow())
