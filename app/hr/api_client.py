from urllib.parse import quote

from app.core.config import settings
from app.shared.http_client import RestClient

backend = RestClient(settings.hr_api_url, "HR service")


def get_employee(username: str):
    return backend.get("employees/" + quote(username, safe=""))


def get_manager(username: str):
    return backend.get("employees/" + quote(username, safe="") + "/manager")


def api_health() -> bool:
    return backend.health()
