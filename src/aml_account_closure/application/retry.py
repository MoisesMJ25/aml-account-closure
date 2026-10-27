"""Política de reintentos: backoff exponencial con jitter."""

import random
import time
from dataclasses import dataclass

# Punto de inyección: los tests lo reemplazan por una función que no espera.
sleep = time.sleep


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay: float
    max_delay: float
    jitter: bool = True

    def delay(self, attempt: int) -> float:
        ceiling = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return random.uniform(0, ceiling) if self.jitter else ceiling


# Reintentos: API/Mongo (3 intentos con backoff) y verificación de saldo post-retiro.
RETRY_POLICY = RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=8.0)
VERIFY_POLICY = RetryPolicy(max_attempts=5, base_delay=2.0, max_delay=2.0, jitter=False)
