"""Small thread-safe circuit breaker for downstream REST calls."""

import time
from threading import Lock


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failures: int = 3, cooldown: float = 5.0, clock=None):
        self.failures = failures
        self.cooldown = cooldown
        self.clock = clock or time.monotonic
        self._lock = Lock()
        self._state = "CLOSED"
        self._failure_count = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "OPEN" and self.clock() - self._opened_at >= self.cooldown:
                return "HALF_OPEN"
            return self._state

    def before_call(self):
        with self._lock:
            if self._state == "OPEN":
                if self.clock() - self._opened_at < self.cooldown:
                    raise CircuitOpen("Downstream circuit is open")
                self._state = "HALF_OPEN"
            if self._state == "HALF_OPEN":
                if self._probe_in_flight:
                    raise CircuitOpen("Downstream probe already in flight")
                self._probe_in_flight = True

    def succeeded(self):
        with self._lock:
            self._state = "CLOSED"
            self._failure_count = 0
            self._probe_in_flight = False

    def failed(self):
        with self._lock:
            self._failure_count += 1
            if self._state == "HALF_OPEN" or self._failure_count >= self.failures:
                self._state = "OPEN"
                self._opened_at = self.clock()
            self._probe_in_flight = False
