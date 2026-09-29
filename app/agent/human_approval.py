"""Terminal-only human gate for IAM approval and rejection."""

import json

SENSITIVE_TOOLS = {
    "approve_access_request", "reject_access_request",
    "iam_approve_access_request", "iam_reject_access_request",
}


def ask_human_permission(tool_name: str, arguments: dict) -> bool:
    print("\n" + "=" * 60)
    print("HUMAN APPROVAL REQUIRED")
    print("=" * 60)
    print(f"Tool: {tool_name}")
    print("Arguments:", json.dumps(arguments, indent=2))
    return input("Allow this action? Type YES to approve: ").strip().upper() == "YES"
