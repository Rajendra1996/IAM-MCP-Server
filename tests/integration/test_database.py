from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from app.database import repository as db
from app.database.models import AccessRequest, AuditLog

pytestmark = pytest.mark.integration


def test_health_schema_and_pool(request_factory):
    db.init_db()
    db.init_db()
    assert db.database_health() is True
    with db.pool.connection() as conn:
        indexes = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'").fetchall()
    assert {"idx_access_requests_username", "idx_access_requests_status", "idx_audit_request_id"} <= {
        row["indexname"] for row in indexes
    }
    assert isinstance(db.get_pool_stats(), dict)
    assert db.pool.min_size == 2 and db.pool.max_size == 10


def test_creation_duplicate_and_timestamps(request_factory):
    key = f"pytest-{uuid4().hex}"
    first, new = request_factory(key=key)
    duplicate, duplicate_new = request_factory(key=key)
    assert new is True and duplicate_new is False
    assert first["request_id"] == duplicate["request_id"]
    assert first["created_at"].tzinfo is not None
    assert AccessRequest(**first).idempotency_key == key
    logs = db.get_audit_logs(first["request_id"])
    assert [log["action"] for log in logs] == ["REQUEST_CREATED", "APPROVAL_REQUIRED"]
    assert AuditLog(**logs[0]).created_at.tzinfo is not None
    with db.pool.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM access_requests WHERE idempotency_key = %s", (key,),
        ).fetchone()["n"]
    assert count == 1


def test_concurrent_idempotency(request_factory):
    key = f"pytest-race-{uuid4().hex}"
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: request_factory(key=key), range(8)))
    ids = {row["request_id"] for row, _ in results}
    assert len(ids) == 1
    assert sum(created for _, created in results) == 1
    logs = db.get_audit_logs(ids.pop())
    assert [log["action"] for log in logs].count("REQUEST_CREATED") == 1


def test_idempotency_key_cannot_change_business_request(request_factory):
    key = f"pytest-conflict-{uuid4().hex}"
    row, _ = request_factory(key=key)
    with pytest.raises(db.IdempotencyConflict):
        request_factory(username="bob", key=key)
    assert len(db.get_audit_logs(row["request_id"])) == 2


def test_creation_rolls_back_when_audit_fails(request_factory, monkeypatch):
    key = f"pytest-rollback-{uuid4().hex}"
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("simulated audit failure")
    monkeypatch.setattr(db, "_insert_audit", fail)
    with pytest.raises(psycopg.OperationalError):
        request_factory(key=key)
    with db.pool.connection() as conn:
        assert conn.execute(
            "SELECT * FROM access_requests WHERE idempotency_key = %s", (key,),
        ).fetchone() is None


def test_pagination_has_no_overlap(request_factory):
    with db.pool.connection() as conn:
        starting_id = conn.execute("SELECT COALESCE(MAX(request_id), 0) AS n FROM access_requests").fetchone()["n"]
    for _ in range(5):
        request_factory()
    page1, cursor = db.get_access_requests_paginated(limit=2, cursor=starting_id)
    page2, cursor2 = db.get_access_requests_paginated(limit=2, cursor=int(cursor))
    page3, cursor3 = db.get_access_requests_paginated(limit=2, cursor=int(cursor2))
    assert [len(page1), len(page2), len(page3)] == [2, 2, 1]
    assert len({r["request_id"] for r in page1 + page2 + page3}) == 5
    assert cursor3 is None


def test_audit_and_approval_persist(request_factory):
    row, _ = request_factory()
    event = db.create_audit_log(row["request_id"], "TEST_EVENT", "pytest", "details")
    assert event in db.get_audit_logs(row["request_id"])
    updated = db.update_access_request_decision(row["request_id"], "APPROVED", "jane", "Manager agreed")
    persisted = db.get_access_request(row["request_id"])
    assert persisted == updated
    assert persisted["status"] == "APPROVED"
    assert persisted["approver"] == "jane"
    assert persisted["decision_reason"] == "Manager agreed"
    assert persisted["decided_at"].tzinfo is not None
    assert db.get_audit_logs(row["request_id"])[-1]["action"] == "REQUEST_APPROVED"


def test_concurrent_decisions_have_one_winner(request_factory):
    row, _ = request_factory()
    def decide(index):
        try:
            return db.update_access_request_decision(
                row["request_id"], "APPROVED" if index % 2 else "REJECTED", "jane", "Test",
            )
        except db.DecisionConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(decide, range(8)))
    assert sum(result is not None for result in results) == 1
    decisions = [log for log in db.get_audit_logs(row["request_id"])
                 if log["action"] in {"REQUEST_APPROVED", "REQUEST_REJECTED"}]
    assert len(decisions) == 1


def test_duplicate_after_decision_keeps_decision_and_audit(request_factory):
    key = f"pytest-replay-{uuid4().hex}"
    row, _ = request_factory(key=key)
    db.update_access_request_decision(row["request_id"], "APPROVED", "jane", "Test")
    duplicate, created = request_factory(key=key)
    assert created is False
    assert duplicate["request_id"] == row["request_id"]
    assert duplicate["status"] == "APPROVED"
    assert len(db.get_audit_logs(row["request_id"])) == 3


def test_decision_rolls_back_when_audit_fails(request_factory, monkeypatch):
    row, _ = request_factory()
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("simulated audit failure")
    monkeypatch.setattr(db, "_insert_audit", fail)
    with pytest.raises(psycopg.OperationalError):
        db.update_access_request_decision(row["request_id"], "APPROVED", "jane", "Test")
    assert db.get_access_request(row["request_id"])["status"] == "NEEDS_APPROVAL"
