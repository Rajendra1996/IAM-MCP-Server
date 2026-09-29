from datetime import datetime

from pydantic import BaseModel


class AccessRequest(BaseModel):
    request_id: int
    idempotency_key: str | None = None
    username: str
    application: str
    status: str
    approver: str | None = None
    decision_reason: str | None = None
    created_at: datetime | str | None = None
    decided_at: datetime | str | None = None


class AuditLog(BaseModel):
    audit_id: int
    request_id: int | None
    action: str
    actor: str
    details: str | None = None
    created_at: datetime | str | None = None


class AccessRequestPage(BaseModel):
    requests: list[AccessRequest]
    count: int
    next_cursor: str | None = None
    success: bool = True
    error: str | None = None


class OperationResult(BaseModel):
    success: bool
    message: str | None = None
    error: str | None = None
