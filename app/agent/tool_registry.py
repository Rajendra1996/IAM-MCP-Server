"""Stable, collision-free names for independently discovered MCP tools."""

from dataclasses import dataclass

from clients.multi_mcp_client import MultiMCPClient


@dataclass(frozen=True)
class ToolRoute:
    service: str
    original_name: str
    definition: object


async def discover(client) -> dict[str, ToolRoute]:
    if isinstance(client, MultiMCPClient):
        found = await client.discover()
        return {
            name: ToolRoute(service, original, tool)
            for name, (service, original, tool) in found.items()
        }
    result = await client.list_tools()
    return {tool.name: ToolRoute("iam", tool.name, tool) for tool in result.tools}


def to_openai_tools(routes: dict[str, ToolRoute]) -> list[dict]:
    return [
        {
            "type": "function",
            "name": name,
            "description": route.definition.description or f"MCP tool: {name}",
            "parameters": route.definition.input_schema,
            "strict": False,
        }
        for name, route in routes.items()
    ]
