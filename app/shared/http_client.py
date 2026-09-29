"""Bounded, conservative REST client shared by HR and ITSM MCP services."""

import atexit
import logging
import time

import httpx

from app.shared.circuit_breaker import CircuitBreaker, CircuitOpen

logger = logging.getLogger(__name__)


class BackendUnavailable(RuntimeError):
    pass


class BackendRejected(ValueError):
    pass


class RestClient:
    def __init__(self, base_url: str, service: str):
        self.service = service
        self.breaker = CircuitBreaker()
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(connect=3, read=5, write=5, pool=3),
            trust_env=False,
        )
        atexit.register(self.client.close)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict | list | None:
        try:
            self.breaker.before_call()
        except CircuitOpen as exc:
            raise BackendUnavailable(f"{self.service} is temporarily unavailable") from exc
        attempts = 3 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.request(method, path, json=payload)
                if response.status_code == 404:
                    self.breaker.succeeded()
                    return None
                if response.status_code < 500 and response.status_code >= 400:
                    self.breaker.succeeded()
                    raise BackendRejected(f"{self.service} rejected the request")
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, (dict, list)):
                    raise ValueError("Backend returned non-object JSON")
                self.breaker.succeeded()
                return data
            except BackendRejected:
                raise
            except (httpx.TimeoutException, httpx.NetworkError,
                    httpx.RemoteProtocolError, httpx.HTTPStatusError, ValueError) as exc:
                if attempt == attempts:
                    self.breaker.failed()
                    logger.exception("Downstream REST failed service=%s", self.service)
                    raise BackendUnavailable(f"{self.service} is temporarily unavailable") from exc
                logger.warning("Retrying downstream GET service=%s attempt=%s", self.service, attempt)
                time.sleep(0.5 * attempt)
        raise BackendUnavailable(f"{self.service} is temporarily unavailable")

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload: dict):
        # Writes are never retried by this client, even when the API is idempotent.
        return self._request("POST", path, payload)

    def health(self) -> bool:
        try:
            response = self.get("health")
            return isinstance(response, dict) and response.get("status") == "ok"
        except (BackendUnavailable, BackendRejected):
            return False
