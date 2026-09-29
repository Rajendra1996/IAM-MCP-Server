"""Simulated ticket API with process-local atomic idempotency for learning."""

from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Demo ITSM API")
_lock = Lock()
_tickets: dict[int, dict] = {}
_keys: dict[str, int] = {}
_next_id = 1


class TicketInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    iam_request_id: int | None = Field(default=None, ge=1)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets")
def create_ticket(payload: TicketInput):
    global _next_id
    username = payload.username.strip().lower()
    key = payload.idempotency_key.strip()
    if not username or not key or not payload.title.strip() or not payload.description.strip():
        raise HTTPException(status_code=422, detail="Required ticket field is empty")
    with _lock:
        existing_id = _keys.get(key)
        if existing_id is not None:
            existing = _tickets[existing_id]
            if (existing["username"], existing["title"], existing["description"], existing["iam_request_id"]) != (
                username, payload.title.strip(), payload.description.strip(), payload.iam_request_id
            ):
                raise HTTPException(status_code=409, detail="Idempotency key belongs to another ticket")
            return {"success": True, "duplicate": True, "ticket": existing}
        ticket = {
            "ticket_id": _next_id,
            "username": username,
            "title": payload.title.strip(),
            "description": payload.description.strip(),
            "iam_request_id": payload.iam_request_id,
            "status": "OPEN",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _tickets[_next_id] = ticket
        _keys[key] = _next_id
        _next_id += 1
        return {"success": True, "duplicate": False, "ticket": ticket}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    ticket = _tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.get("/users/{username}/tickets")
def list_user_tickets(username: str):
    username = username.strip().lower()
    with _lock:
        tickets = [ticket for ticket in _tickets.values() if ticket["username"] == username]
    return {"username": username, "tickets": tickets, "count": len(tickets)}
