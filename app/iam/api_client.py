"""Bounded HTTP timeouts and retries for the downstream IAM service."""

import atexit
import logging
import os
import time
from urllib.parse import quote

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)
IAM_API_URL = settings.iam_api_url
MAX_ATTEMPTS = 3
client = httpx.Client(
    base_url=IAM_API_URL.rstrip("/") + "/",
    timeout=httpx.Timeout(connect=3, read=5, write=5, pool=3),
    trust_env=False,
)
atexit.register(client.close)


class IAMAPIError(RuntimeError):
    """A downstream failure that must not expose transport details to clients."""


def _get(path: str) -> dict | None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(path)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected an IAM JSON object")
            return payload
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            if attempt == MAX_ATTEMPTS:
                logger.exception("IAM transport failed after %s attempts", attempt)
                raise IAMAPIError("IAM service is temporarily unavailable") from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == MAX_ATTEMPTS:
                logger.exception("IAM service returned an unsuccessful HTTP status")
                raise IAMAPIError("IAM service is temporarily unavailable") from None
        except (ValueError, httpx.HTTPError, httpx.InvalidURL):
            logger.exception("Invalid IAM service response or configuration")
            raise IAMAPIError("IAM service is temporarily unavailable") from None
        logger.warning("Retrying IAM request after attempt %s/%s", attempt, MAX_ATTEMPTS)
        time.sleep(0.5 * attempt)
    raise IAMAPIError("IAM service is temporarily unavailable")


def _lookup(path: str, fields: dict[str, type]) -> dict | None:
    payload = _get(path)
    if payload is not None and any(
        not isinstance(payload.get(name), field_type)
        for name, field_type in fields.items()
    ):
        logger.error("IAM service returned an invalid lookup payload")
        raise IAMAPIError("IAM service is temporarily unavailable")
    return payload


def get_user(username: str) -> dict | None:
    return _lookup(
        "users/" + quote(username, safe=""),
        {"username": str, "display_name": str, "department": str, "active": bool},
    )


def get_application(app_name: str) -> dict | None:
    return _lookup(
        "applications/" + quote(app_name, safe=""),
        {"name": str, "owner": str, "risk_level": str, "active": bool},
    )


def api_health() -> bool:
    try:
        payload = _get("health")
        return payload is not None and payload.get("status") == "ok"
    except IAMAPIError:
        return False
