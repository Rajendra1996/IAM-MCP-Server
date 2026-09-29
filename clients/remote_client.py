import asyncio
import os
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from app.core.config import settings

MCP_URL = settings.iam_mcp_url


@asynccontextmanager
async def connect_client(token: str | None = None):
    """Share the authenticated remote transport with the CLI and OpenAI agent."""
    token = token or os.getenv("IAM_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("IAM_ACCESS_TOKEN environment variable is missing")
    # MCP 2.2 uses httpx2 for its transport; the IAM REST client uses httpx.
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30, read=300), trust_env=False,
    ) as http_client:
        transport = streamable_http_client(MCP_URL, http_client=http_client)
        async with Client(transport) as client:
            yield client


async def main():
    if not os.getenv("IAM_ACCESS_TOKEN"):
        print("ERROR: IAM_ACCESS_TOKEN environment variable is missing.")
        print("Generate one using: python generate_token.py")
        return
    async with connect_client() as client:
        print("\n" + "=" * 60)
        print("REMOTE IAM MCP CLIENT")
        print("=" * 60)
        for title, name, arguments in [
            ("WHOAMI", "whoami", {}),
            ("SEARCH USER", "search_user", {"username": "alice"}),
            ("ACCESS REQUESTS", "list_access_requests", {"limit": 10}),
        ]:
            result = await client.call_tool(name, arguments)
            print(f"\n{title}")
            print(result.structured_content or result.content)


if __name__ == "__main__":
    asyncio.run(main())
