import asyncio

from clients.remote_client import connect_client


async def show_progress(
    progress: float,
    total: float | None,
    message: str | None,
):
    print(
        f"[PROGRESS] "
        f"{progress}/{total} "
        f"{message or ''}"
    )


async def main():

    async with connect_client() as client:

        # ====================================================
        # SERVER INFO
        # ====================================================

        print("\n==============================")
        print("CONNECTED TO MCP SERVER")
        print("==============================")

        print(
            client.server_capabilities
        )

        # ====================================================
        # LIST TOOLS
        # ====================================================

        print("\n==============================")
        print("TOOLS")
        print("==============================")

        tools_result = (
            await client.list_tools()
        )

        for tool in tools_result.tools:

            print(
                f"- {tool.name}"
            )

        # ====================================================
        # CALL SEARCH USER
        # ====================================================

        print("\n==============================")
        print("SEARCH USER")
        print("==============================")

        result = await client.call_tool(
            "search_user",
            {
                "username": "John"
            },
        )

        print(
            result.structured_content
        )

        # ====================================================
        # SEARCH APPLICATION
        # ====================================================

        print("\n==============================")
        print("SEARCH APPLICATION")
        print("==============================")

        result = await client.call_tool(
            "search_application",
            {
                "app_name": "SAP"
            },
        )

        print(
            result.structured_content
        )

        # ====================================================
        # CREATE ACCESS REQUEST
        # ====================================================

        print("\n==============================")
        print("CREATE ACCESS REQUEST")
        print("==============================")

        result = await client.call_tool(
            "request_access",
            {
                "username": "alice",
                "app_name": "sap",
                "idempotency_key": "local-client-alice-sap-001",
            },
        )

        print(
            result.structured_content
        )

        # ====================================================
        # LIST ACCESS REQUESTS
        # ====================================================

        print("\n==============================")
        print("ACCESS REQUESTS")
        print("==============================")

        result = await client.call_tool(
            "list_access_requests",
            {
                "limit": 5
            },
        )

        print(
            result.structured_content
        )

        # ====================================================
        # AUDIT LOGS
        # ====================================================

        print("\n==============================")
        print("AUDIT LOG")
        print("==============================")

        result = await client.call_tool(
            "list_audit_logs",
            {},
        )

        print(
            result.structured_content
        )

        # ====================================================
        # PROGRESS EXAMPLE
        # ====================================================

        print("\n==============================")
        print("PROGRESS DEMO")
        print("==============================")

        result = await client.call_tool(
            "analyze_access_requests",
            {},
            progress_callback=show_progress,
        )

        print(
            result.structured_content
        )


if __name__ == "__main__":

    asyncio.run(main())
