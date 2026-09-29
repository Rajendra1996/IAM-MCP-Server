"""Independent authenticated MCP connections with orchestration above services."""

import asyncio
import os
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings


class MultiMCPClient:
    def __init__(self, token: str, urls: dict[str, str] | None = None):
        if not token:
            raise ValueError("A bearer token is required")
        self.token = token
        self.urls = urls or {
            "iam": settings.iam_mcp_url,
            "hr": settings.hr_mcp_url,
            "itsm": settings.itsm_mcp_url,
        }
        self.clients: dict[str, Client] = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        try:
            for service, url in self.urls.items():
                http_client = await self._stack.enter_async_context(httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=httpx2.Timeout(30, read=300), trust_env=False,
                ))
                self.clients[service] = await self._stack.enter_async_context(
                    Client(streamable_http_client(url, http_client=http_client))
                )
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *args):
        await self._stack.__aexit__(*args)

    async def call(self, service: str, tool: str, arguments: dict):
        if service not in self.clients:
            raise ValueError(f"Unknown MCP service: {service}")
        return await self.clients[service].call_tool(tool, arguments)

    async def call_tool(self, prefixed_name: str, arguments: dict):
        service, separator, tool = prefixed_name.partition("_")
        if not separator or service not in self.clients:
            raise ValueError("Unknown namespaced MCP tool")
        return await self.call(service, tool, arguments)

    async def discover(self) -> dict[str, tuple[str, str, object]]:
        registry = {}
        for service, client in self.clients.items():
            result = await client.list_tools()
            for tool in result.tools:
                name = f"{service}_{tool.name}"
                if name in registry:
                    raise ValueError("Duplicate namespaced MCP tool")
                registry[name] = service, tool.name, tool
        return registry


async def request_access_workflow(
    multi: MultiMCPClient, username: str, app_name: str,
    idempotency_key: str,
) -> dict:
    """Demonstrate cross-service orchestration without hiding partial failures."""
    employee = (await multi.call("hr", "search_employee", {"username": username})).structured_content
    if not employee or not employee.get("found") or not employee["employee"].get("active"):
        return {"success": False, "stage": "hr", "employee": employee}
    manager = (await multi.call("hr", "get_employee_manager", {"username": username})).structured_content
    application = (await multi.call("iam", "search_application", {"app_name": app_name})).structured_content
    if not application or not application.get("found"):
        return {"success": False, "stage": "iam_application", "employee": employee,
                "manager": manager, "application": application}
    access = (await multi.call("iam", "request_access", {
        "username": username, "app_name": app_name, "idempotency_key": idempotency_key,
    })).structured_content
    if not access or not access.get("success"):
        return {"success": False, "stage": "iam_request", "employee": employee,
                "manager": manager, "application": application, "access": access}
    ticket = None
    if access.get("requires_approval"):
        ticket = (await multi.call("itsm", "create_ticket", {
            "username": username,
            "title": f"Access approval for {application['application']['name']}",
            "description": f"Manager {manager.get('manager')} review IAM request {access['request']['request_id']}",
            "iam_request_id": access["request"]["request_id"],
            "idempotency_key": "approval-ticket-" + idempotency_key,
        })).structured_content
    return {
        "success": ticket is None or bool(ticket.get("success")),
        "partial": ticket is not None and not ticket.get("success"),
        "stage": "itsm_ticket" if ticket is not None and not ticket.get("success") else "complete",
        "employee": employee, "manager": manager, "application": application,
        "access": access, "ticket": ticket,
    }


async def main():
    token = os.getenv("IAM_ACCESS_TOKEN")
    if not token:
        print("Set IAM_ACCESS_TOKEN to a multi-service employee JWT")
        return
    async with MultiMCPClient(token) as multi:
        result = await request_access_workflow(
            multi, "alice", "sap", f"multi-client-{uuid4().hex}",
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
