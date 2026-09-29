from app.itsm.api_client import create_ticket as post_ticket, get_ticket as fetch_ticket
from app.itsm.api_client import list_user_tickets as fetch_user_tickets


def normalize_username(username: str | None) -> str | None:
    if not username or not username.strip() or len(username.strip()) > 128:
        return None
    return username.strip().lower()


def create_ticket(username: str, title: str, description: str,
                  idempotency_key: str, iam_request_id: int | None):
    return post_ticket({
        "username": username, "title": title, "description": description,
        "idempotency_key": idempotency_key, "iam_request_id": iam_request_id,
    })


def get_ticket(ticket_id: int):
    ticket = fetch_ticket(ticket_id)
    return {"found": ticket is not None, "ticket": ticket} if ticket else {"found": False}


def list_user_tickets(username: str):
    return fetch_user_tickets(username)
