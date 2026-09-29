import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent import orchestrator as agent


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(structured_content={"success": True}, content=[])


@pytest.mark.parametrize("name", ["approve_access_request", "reject_access_request"])
def test_human_denial_does_not_execute(name, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    client = FakeClient()
    result = asyncio.run(agent.execute_mcp_tool(client, name, {"request_id": 1}))
    assert result["success"] is False
    assert client.calls == []


@pytest.mark.parametrize("name", ["approve_access_request", "reject_access_request"])
def test_human_approval_executes_once(name, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "YES")
    client = FakeClient()
    assert asyncio.run(agent.execute_mcp_tool(client, name, {"request_id": 1}))["success"]
    assert len(client.calls) == 1


def test_agent_supplies_and_preserves_idempotency_key():
    client = FakeClient()
    args = {"username": "alice", "app_name": "sap"}
    asyncio.run(agent.execute_mcp_tool(client, "request_access", args))
    first_key = args["idempotency_key"]
    asyncio.run(agent.execute_mcp_tool(client, "request_access", args))
    assert client.calls[1][1]["idempotency_key"] == first_key
    explicit = {**args, "idempotency_key": "user-provided-key"}
    asyncio.run(agent.execute_mcp_tool(client, "request_access", explicit))
    assert client.calls[-1][1]["idempotency_key"] == "user-provided-key"


def test_agent_turn_preserves_tool_outputs_for_next_model_call(monkeypatch):
    class FunctionCall:
        type = "function_call"
        name = "request_access"
        call_id = "call-test"
        arguments = '{"username":"alice","app_name":"sap"}'

        def model_dump(self, **kwargs):
            return {"type": self.type, "name": self.name, "call_id": self.call_id,
                    "arguments": self.arguments}

    model_calls = []
    responses = iter([
        SimpleNamespace(output=[FunctionCall()], output_text=""),
        SimpleNamespace(output=[], output_text="Access request created."),
    ])

    def create(**kwargs):
        model_calls.append(list(kwargs["input"]))
        return next(responses)

    monkeypatch.setattr(agent, "openai_client", SimpleNamespace(responses=SimpleNamespace(create=create)))
    conversation = [{"role": "user", "content": "Request SAP access for Alice"}]
    client = FakeClient()
    reply = asyncio.run(agent.run_agent_turn(client, conversation, []))
    assert reply == "Access request created."
    assert len(model_calls) == 2 and len(client.calls) == 1
    assert model_calls[1][-1]["type"] == "function_call_output"
    assert json.loads(model_calls[1][-1]["output"])["success"] is True
