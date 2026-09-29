"""Print demo cross-service JWTs for local use only."""

from app.shared.auth import create_token


def employee_token():
    return create_token(
        "employee.alice", "multi-employee-client",
        ["iam:basic", "hr:read", "itsm:read", "itsm:create"],
    )


def approver_token():
    return create_token(
        "manager.jane", "multi-approver-client",
        ["iam:basic", "iam:approve", "hr:read", "itsm:read", "itsm:create"],
    )


def main():
    print("MULTI-SERVICE EMPLOYEE TOKEN")
    print(employee_token())
    print("MULTI-SERVICE APPROVER TOKEN")
    print(approver_token())


if __name__ == "__main__":
    main()
