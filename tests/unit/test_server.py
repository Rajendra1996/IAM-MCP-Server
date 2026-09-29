import asyncio
import json

import psycopg
import pytest
from mcp.server.auth.provider import AccessToken

from app.iam import mcp_server as server
from app.iam.api_client import IAMAPIError


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    token = AccessToken(
        token="test-token", client_id="test-client", subject="test.alice",
        scopes=["iam:basic"], resource=server.RESOURCE_URL,
    )
    monkeypatch.setattr(server, "current_token", lambda: token)
    monkeypatch.setattr(server, "check_rate_limit", lambda: True)
    for name in server.METRICS:
        server.METRICS[name] = 0
    return token


@pytest.mark.parametrize("tool", [server.approve_access_request, server.reject_access_request])
def test_employee_cannot_decide(tool, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Denied tool must not perform a database write")
    monkeypatch.setattr(server, "update_access_request_decision", unexpected)
    result = tool(request_id=1, approver="jane", reason="Test")
    assert result["success"] is False
    assert "not authorized" in result["error"]
    assert server.METRICS["authorization_rejections"] == 1


def test_allowlist_is_enforced(identity):
    assert server.tool_allowed("whoami") is True
    assert server.tool_allowed("approve_access_request") is False
    identity.scopes.append("iam:approve")
    assert server.tool_allowed("approve_access_request") is True
    assert server.tool_allowed("unknown_tool") is False
    identity.scopes.remove("iam:basic")
    assert server.tool_allowed("whoami") is False


def test_rate_limit_per_identity_and_expiry(identity, monkeypatch):
    from app.shared.rate_limit import RedisRateLimiter
    from app.shared import rate_limit
    from uuid import uuid4

    now = [100.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])
    limiter = RedisRateLimiter("unit-" + uuid4().hex)
    monkeypatch.setattr(server, "check_rate_limit", lambda: limiter.check(identity.subject))
    assert all(server.whoami()["authenticated"] for _ in range(20))
    assert server.whoami()["success"] is False
    assert server.METRICS["rate_limit_rejections"] == 1
    identity.subject = "another.employee"
    assert server.whoami()["authenticated"] is True
    identity.subject = "test.alice"
    now[0] += 61
    assert server.whoami()["authenticated"] is True


@pytest.mark.parametrize("function,args", [
    (server.search_user, {"username": "alice"}),
    (server.search_application, {"app_name": "sap"}),
    (server.user_resource, {"username": "alice"}),
])
def test_rest_failure_is_safe(function, args, monkeypatch, caplog):
    def fail(*args):
        raise IAMAPIError("private transport details")
    monkeypatch.setattr(server, "get_user", fail)
    monkeypatch.setattr(server, "get_application", fail)
    result = function(**args)
    assert result["success"] is False
    assert "temporarily unavailable" in result["error"]
    assert "private" not in json.dumps(result)
    assert "Backend operation failed" in caplog.text


def test_database_failure_is_safe_and_not_empty_success(monkeypatch):
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("private database details")
    monkeypatch.setattr(server, "get_access_request", fail)
    monkeypatch.setattr(server, "get_access_requests_paginated", fail)
    result = server.get_access_request_by_id(1)
    assert result["success"] is False
    assert "private" not in json.dumps(result)
    page = server.list_access_requests()
    assert page.success is False and page.error


@pytest.mark.parametrize("inactive", ["user", "application"])
def test_inactive_entities_cannot_request(inactive, monkeypatch):
    monkeypatch.setattr(server, "get_user", lambda name: {"active": inactive != "user"})
    monkeypatch.setattr(server, "get_application", lambda name: {"active": inactive != "application"})
    result = server.request_access("alice", "sap", "test-key")
    assert result["success"] is False
    assert "inactive" in result["error"]


@pytest.mark.parametrize("args", [
    (" ", "sap", "key"), ("alice", " ", "key"), ("alice", "sap", " "),
    ("alice", "sap", "k" * 201),
])
def test_required_inputs(args):
    assert server.request_access(*args)["success"] is False


def test_invalid_cursor_does_not_silently_restart():
    page = server.list_access_requests(cursor="bad")
    assert page.success is False
    assert page.error == "Invalid request cursor"


@pytest.mark.parametrize("db_ok,api_ok", [(True, True), (False, True), (True, False), (False, False)])
def test_health_route_reports_dependencies(monkeypatch, db_ok, api_ok):
    monkeypatch.setattr(server, "database_health", lambda: db_ok)
    monkeypatch.setattr(server, "api_health", lambda: api_ok)
    response = asyncio.run(server.health(None))
    body = json.loads(response.body)
    assert response.status_code == (200 if db_ok and api_ok else 503)
    assert body["database"] == ("ok" if db_ok else "down")
    assert body["iam_api"] == ("ok" if api_ok else "down")


def test_metrics_preserve_counters_and_include_pool(monkeypatch):
    monkeypatch.setattr(server, "get_pool_stats", lambda: {"version_specific_key": 7})
    server.whoami()
    response = asyncio.run(server.metrics(None))
    body = json.loads(response.body)
    assert body["mcp"]["tool_calls_total"] == body["tool_calls_total"] == 1
    assert body["database_pool"] == {"version_specific_key": 7}
