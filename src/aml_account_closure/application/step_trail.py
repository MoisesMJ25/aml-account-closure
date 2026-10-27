"""Ejecución de pasos con reintentos y registro (StepRecord) de cada uno."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from aml_account_closure.application import retry
from aml_account_closure.application.retry import RETRY_POLICY, RetryPolicy
from aml_account_closure.domain.errors import TransientError, describe
from aml_account_closure.domain.models import AccountResult, Outcome, StepRecord, now

log = logging.getLogger("accounts_ops")

T = TypeVar("T")


class StepFailed(Exception):
    def __init__(self, step: str, cause: BaseException):
        super().__init__(f"{step}: {cause}")
        self.step, self.cause = step, cause


class StepTrail:
    """Ejecuta cada paso con reintentos y deja un StepRecord"""

    def __init__(self, result: AccountResult):
        self.result = result

    def run(
        self,
        step: str,
        fn: Callable[[], T],
        *,
        detail: str | Callable[[T], str] | None = None,
        retry_on: tuple[type[BaseException], ...] = (TransientError,),
        policy: RetryPolicy = RETRY_POLICY,
    ) -> T:
        started, t0, attempt = now(), time.perf_counter(), 0
        while True:
            attempt += 1
            try:
                value = fn()
                break
            except Exception as exc:
                if attempt < policy.max_attempts and isinstance(exc, retry_on):
                    retry.sleep(policy.delay(attempt))
                    continue
                self._record(step, Outcome.FAILED, attempt, t0, started, error=describe(exc))
                log.warning(
                    "[fila %s | %s] %s FALLÓ (%s intento/s): %s",
                    self.result.row_number,
                    self.result.rut,
                    step,
                    attempt,
                    describe(exc),
                )
                raise StepFailed(step, exc) from exc
        text = detail(value) if callable(detail) else detail
        self._record(step, Outcome.SUCCESS, attempt, t0, started, detail=text)
        log.info(
            "[fila %s | %s] %s OK (%s intento/s)%s",
            self.result.row_number,
            self.result.rut,
            step,
            attempt,
            f" - {text}" if text else "",
        )
        return value

    def skip(self, step: str, detail: str) -> None:
        self._record(step, Outcome.SKIPPED, 0, time.perf_counter(), now(), detail=detail)

    def fail(self, step: str, error: BaseException) -> None:
        self._record(step, Outcome.FAILED, 0, time.perf_counter(), now(), error=describe(error))
        raise StepFailed(step, error)

    def _record(
        self,
        step: str,
        status: Outcome,
        attempts: int,
        t0: float,
        started: datetime,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        self.result.steps.append(
            StepRecord(step, status, attempts, int((time.perf_counter() - t0) * 1000), started, detail, error)
        )
