from uuid import uuid4

import pytest


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="Run live Docker/REST/MCP tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        skip = pytest.mark.skip(reason="Use --integration with PostgreSQL, IAM API and MCP running")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def request_factory():
    from app.database import repository as database

    database.init_db()
    created_ids = set()

    def create(username="alice", application="SAP", status="NEEDS_APPROVAL", key=None):
        row, created = database.create_access_request(
            username, application, status, key or f"pytest-{uuid4().hex}",
        )
        if created:
            created_ids.add(row["request_id"])
        return row, created

    yield create
    # Clean only rows created by this fixture, preserving historical/developer data.
    with database.pool.connection() as conn:
        for request_id in created_ids:
            conn.execute("DELETE FROM audit_logs WHERE request_id = %s", (request_id,))
            conn.execute("DELETE FROM access_requests WHERE request_id = %s", (request_id,))
