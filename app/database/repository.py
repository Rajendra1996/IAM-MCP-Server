"""PostgreSQL storage. All operations share one process-level connection pool."""

import atexit
import logging
import os
from threading import Lock

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.core.config import settings

logger = logging.getLogger(__name__)
DATABASE_URL = settings.database_url
pool = ConnectionPool(
    DATABASE_URL, min_size=2, max_size=10, timeout=10, open=False,
    kwargs={"row_factory": dict_row, "connect_timeout": 3},
    check=ConnectionPool.check_connection, name="iam-database",
)
atexit.register(pool.close)
_init_lock = Lock()
_initialized = False


class IdempotencyConflict(ValueError):
    """An existing key belongs to a different business request."""


class DecisionConflict(ValueError):
    """A request has already been decided or does not require approval."""


def init_db():
    """Initialize safely, including recovery after an unavailable startup DB."""
    global _initialized
    with _init_lock:
        pool.open()
        with pool.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS access_requests (
                    request_id BIGSERIAL PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    username TEXT NOT NULL,
                    application TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approver TEXT,
                    decision_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    decided_at TIMESTAMPTZ
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    audit_id BIGSERIAL PRIMARY KEY,
                    request_id BIGINT,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_access_requests_username
                ON access_requests (username)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_access_requests_status
                ON access_requests (status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_request_id
                ON audit_logs (request_id)
            """)
        _initialized = True


def _ensure_db():
    if not _initialized:
        init_db()


def _insert_audit(conn, request_id, action, actor, details=None):
    return conn.execute(
        """
        INSERT INTO audit_logs (request_id, action, actor, details)
        VALUES (%s, %s, %s, %s) RETURNING *
        """,
        (request_id, action, actor, details),
    ).fetchone()


def create_access_request(
    username: str, application: str, status: str, idempotency_key: str,
    *, actor: str | None = None, details: str | None = None,
) -> tuple[dict, bool]:
    """Return (request, created_new); creation and audit commit together.

    ON CONFLICT waits for concurrent inserts. The subsequent SELECT uses a new
    READ COMMITTED snapshot, so it can see the winning transaction's row.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    _ensure_db()
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO access_requests
                (username, application, status, idempotency_key)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING RETURNING *
            """,
            (username, application, status, idempotency_key),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM access_requests WHERE idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Idempotent request disappeared")
            if (row["username"], row["application"].casefold()) != (
                username, application.casefold()
            ):
                raise IdempotencyConflict(
                    "idempotency_key was already used for a different request"
                )
            return row, False
        _insert_audit(
            conn, row["request_id"], "REQUEST_CREATED", actor or username,
            details or f"Access requested for {application}",
        )
        if status == "NEEDS_APPROVAL":
            _insert_audit(
                conn, row["request_id"], "APPROVAL_REQUIRED", "system",
                "High-risk application requires approval",
            )
        return row, True


def get_access_request(request_id: int) -> dict | None:
    _ensure_db()
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM access_requests WHERE request_id = %s", (request_id,)
        ).fetchone()


def get_access_requests_paginated(
    limit: int = 10, cursor: int | None = None,
) -> tuple[list[dict], str | None]:
    limit = max(1, min(limit, 100))
    _ensure_db()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM access_requests WHERE request_id > %s
            ORDER BY request_id LIMIT %s
            """,
            (cursor or 0, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, str(rows[-1]["request_id"]) if has_more and rows else None


def update_access_request_decision(
    request_id: int, status: str, approver: str, decision_reason: str,
) -> dict | None:
    """Only one approval/rejection wins; its audit commits atomically."""
    if status not in {"APPROVED", "REJECTED"}:
        raise ValueError("Decision must be APPROVED or REJECTED")
    _ensure_db()
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE access_requests
            SET status = %s, approver = %s, decision_reason = %s,
                decided_at = NOW()
            WHERE request_id = %s AND status = 'NEEDS_APPROVAL'
            RETURNING *
            """,
            (status, approver, decision_reason, request_id),
        ).fetchone()
        if row is None:
            existing = conn.execute(
                "SELECT status FROM access_requests WHERE request_id = %s",
                (request_id,),
            ).fetchone()
            if existing:
                raise DecisionConflict(
                    f"Cannot decide request from status '{existing['status']}'"
                )
            return None
        _insert_audit(conn, request_id, f"REQUEST_{status}", approver, decision_reason)
        return row


def create_audit_log(
    request_id: int | None, action: str, actor: str, details: str | None = None,
) -> dict:
    _ensure_db()
    with pool.connection() as conn:
        return _insert_audit(conn, request_id, action, actor, details)


def get_audit_logs(request_id: int | None = None) -> list[dict]:
    _ensure_db()
    with pool.connection() as conn:
        if request_id is None:
            return conn.execute("SELECT * FROM audit_logs ORDER BY audit_id").fetchall()
        return conn.execute(
            "SELECT * FROM audit_logs WHERE request_id = %s ORDER BY audit_id",
            (request_id,),
        ).fetchall()


def database_health() -> bool:
    try:
        _ensure_db()
        with pool.connection() as conn:
            return conn.execute("SELECT 1 AS healthy").fetchone()["healthy"] == 1
    except Exception:
        logger.exception("PostgreSQL health check failed")
        return False


def get_pool_stats() -> dict:
    return pool.get_stats()
