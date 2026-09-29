from app.shared.auth import (
    create_token,
)


def employee_token():

    return create_token(
        subject="employee.alice",
        client_id="employee-client",
        scopes=[
            "iam:basic",
        ],
    )


def approver_token():

    return create_token(
        subject="manager.jane",
        client_id="manager-client",
        scopes=[
            "iam:basic",
            "iam:approve",
        ],
    )


def main():
    print()
    print("=" * 60)
    print("EMPLOYEE TOKEN")
    print("=" * 60)
    print(employee_token())
    print()
    print("=" * 60)
    print("APPROVER TOKEN")
    print("=" * 60)
    print(approver_token())


if __name__ == "__main__":
    main()
