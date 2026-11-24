"""Circuit Breaker para proteger chamadas ao Web Service do Moodle."""
from collections.abc import Callable
import time

from app.core.errors import CircuitBreakerOpenError
from app.core.logging import get_logger
from app.core.metrics import CIRCUIT_BREAKER_STATE

_STATE_VALUES = {
    "closed": 0,
    "half_open": 1,
    "open": 2,
}


class CircuitBreaker:
    """Circuit Breaker com estados CLOSED, OPEN e HALF_OPEN."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.1, recovery_seconds)
        self._clock = clock
        self._state = "closed"
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_probe_active = False
        self._log = get_logger("circuit_breaker")
        CIRCUIT_BREAKER_STATE.set(_STATE_VALUES[self._state])

    @property
    def state(self) -> str:
        if (
            self._state == "open"
            and self._opened_at is not None
            and (self._clock() - self._opened_at) >= self.recovery_seconds
        ):
            return "half_open"
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def before_call(self) -> None:
        """Valida se a chamada ao Moodle pode prosseguir ou falha rápido se aberto."""
        if self._state == "open":
            assert self._opened_at is not None
            if (self._clock() - self._opened_at) >= self.recovery_seconds:
                self._transition("half_open")
            else:
                raise CircuitBreakerOpenError(
                    "Circuit breaker do Moodle está aberto; chamada bloqueada temporariamente."
                )

        if self._state == "half_open":
            if self._half_open_probe_active:
                raise CircuitBreakerOpenError(
                    "Circuit breaker do Moodle está em teste de recuperação (half-open)."
                )
            self._half_open_probe_active = True

    def record_success(self) -> None:
        """Registra resposta normal do Moodle e fecha o circuito."""
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_probe_active = False
        if self._state != "closed":
            self._transition("closed")

    def record_connectivity_ok(self) -> None:
        """Usado em erros funcionais/domínio: não abre circuito e fecha se estava em half-open."""
        self._half_open_probe_active = False
        if self._state == "half_open":
            self._consecutive_failures = 0
            self._opened_at = None
            self._transition("closed")

    def record_transient_failure(self) -> None:
        """Registra falha de transporte/5xx; abre o circuito ao atingir o limiar."""
        self._consecutive_failures += 1
        was_half_open = self._state == "half_open"
        self._half_open_probe_active = False
        if was_half_open or self._consecutive_failures >= self.failure_threshold:
            self._opened_at = self._clock()
            self._transition("open")

    def release_probe(self) -> None:
        """Garante liberação da trava de sonda em exceções inesperadas."""
        self._half_open_probe_active = False

    def _transition(self, new_state: str) -> None:
        old_state = self._state
        self._state = new_state
        CIRCUIT_BREAKER_STATE.set(_STATE_VALUES[new_state])
        if old_state != new_state:
            self._log.warning(
                "Circuit breaker do Moodle mudou de %s para %s.",
                old_state,
                new_state,
                extra={"cb_state": new_state},
            )
