from urllib.parse import quote

from app.core.config import settings
from app.shared.http_client import RestClient

backend = RestClient(settings.itsm_api_url, "ITSM service")


def create_ticket(payload: dict):
    return backend.post("tickets", payload)


def get_ticket(ticket_id: int):
    return backend.get(f"tickets/{ticket_id}")


def list_user_tickets(username: str):
    return backend.get("users/" + quote(username, safe="") + "/tickets")


def api_health() -> bool:
    return backend.health()
