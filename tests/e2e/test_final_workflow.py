"""The final cross-service workflow includes the actual application human gate."""

import asyncio
from uuid import uuid4

import pytest

from app.agent import orchestrator
from app.database import repository
from clients.multi_mcp_client import MultiMCPClient, request_access_workflow
from scripts.generate_multi_token import approver_token, employee_token

pytestmark = pytest.mark.integration
URLS = {service: f"http://127.0.0.1:8080/{service}/mcp"
        for service in ("iam", "hr", "itsm")}


def test_human_no_then_yes_and_audit(monkeypatch):
    async def run():
        key = f"final-approval-{uuid4().hex}"
        async with MultiMCPClient(employee_token(), URLS) as employee:
            workflow = await request_access_workflow(employee, "alice", "sap", key)
            assert workflow["success"]
            request_id = workflow["access"]["request"]["request_id"]
            ticket_id = workflow["ticket"]["ticket"]["ticket_id"]
            assert request_id > 0 and ticket_id > 0
            assert workflow["manager"]["manager"] == "jane"
            args = {"request_id": request_id, "approver": "manager.jane",
                    "reason": "Approved after human review"}
            async with MultiMCPClient(approver_token(), URLS) as approver:
                monkeypatch.setattr("builtins.input", lambda _: "NO")
                denied = await orchestrator.execute_mcp_tool(
                    approver, "iam_approve_access_request", args,
                )
                assert not denied["success"]
                pending = (await approver.call("iam", "get_access_request_by_id",
                                               {"request_id": request_id})).structured_content
                assert pending["request"]["status"] == "NEEDS_APPROVAL"
                monkeypatch.setattr("builtins.input", lambda _: "YES")
                approved = await orchestrator.execute_mcp_tool(
                    approver, "iam_approve_access_request", args,
                )
                assert approved["success"]
                assert approved["request"]["status"] == "APPROVED"
                logs = (await approver.call("iam", "list_audit_logs",
                                            {"request_id": request_id})).structured_content
                assert any(item["action"] == "REQUEST_APPROVED" for item in logs["logs"])
            return request_id, ticket_id
    request_id, ticket_id = asyncio.run(run())
    print(f"E2E request_id={request_id} ticket_id={ticket_id}")
    assert repository.get_access_request(request_id)["status"] == "APPROVED"
