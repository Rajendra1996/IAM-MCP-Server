import asyncio
from types import SimpleNamespace

import pytest

from app.agent import orchestrator, tool_registry
from app.shared.circuit_breaker import CircuitBreaker, CircuitOpen
from app.shared.observability import correlation_id


def test_breaker_opens_probes_then_closes():
    now = [0.0]
    breaker = CircuitBreaker(failures=2, cooldown=5, clock=lambda: now[0])
    assert breaker.state == "CLOSED"
    breaker.before_call()
    breaker.failed()
    breaker.before_call()
    breaker.failed()
    assert breaker.state == "OPEN"
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    now[0] = 5.0
    assert breaker.state == "HALF_OPEN"
    breaker.before_call()
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    breaker.succeeded()
    assert breaker.state == "CLOSED"


def test_breaker_failed_probe_reopens():
    now = [0.0]
    breaker = CircuitBreaker(failures=1, cooldown=2, clock=lambda: now[0])
    breaker.before_call()
    breaker.failed()
    now[0] = 2.0
    breaker.before_call()
    breaker.failed()
    assert breaker.state == "OPEN"


def test_correlation_id_rejects_untrusted_header():
    assert correlation_id("test-correlation-123") == "test-correlation-123"
    assert correlation_id("newline\nsecret") != "newline\nsecret"


def test_namespaced_tool_mapping_and_human_gate(monkeypatch):
    class FakeMulti:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return SimpleNamespace(structured_content={"success": True}, content=[])

    client = FakeMulti()
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    denied = asyncio.run(orchestrator.execute_mcp_tool(
        client, "iam_approve_access_request", {"request_id": 7},
    ))
    assert denied["success"] is False and not client.calls
    monkeypatch.setattr("builtins.input", lambda _: "YES")
    asyncio.run(orchestrator.execute_mcp_tool(
        client, "iam_approve_access_request", {"request_id": 7},
    ))
    assert client.calls[0][0] == "iam_approve_access_request"
    asyncio.run(orchestrator.execute_mcp_tool(
        client, "hr_search_employee", {"username": "alice"},
    ))
    assert client.calls[1][0] == "hr_search_employee"


def test_openai_registry_names_are_stable():
    tool = SimpleNamespace(description="Look up HR", input_schema={"type": "object"})
    routes = {"hr_search_employee": tool_registry.ToolRoute("hr", "search_employee", tool)}
    definitions = tool_registry.to_openai_tools(routes)
    assert definitions[0]["name"] == "hr_search_employee"
    assert definitions[0]["parameters"] == {"type": "object"}
