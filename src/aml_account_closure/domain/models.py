"""Modelos del proceso: solicitudes, snapshots, resultados por cuenta y reporte del run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Action(StrEnum):
    """Gestión solicitada en el Excel."""

    BLOCK = "bloqueo"
    CLOSE = "cierre"


class Outcome(StrEnum):
    """Resultado de una cuenta o de un paso."""

    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    """Estado de un archivo procesado."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AccountRequest:
    row_number: int  # fila real del Excel (encabezado = 1)
    rut: str  # normalizado "12345678-5"
    action: Action


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    balance: Decimal
    is_blocked: bool
    is_deleted: bool
    status: str = ""


@dataclass(frozen=True)
class Card:
    card_id: str
    card_type: str
    is_active: bool


@dataclass
class StepRecord:
    step: str
    status: Outcome
    attempts: int
    duration_ms: int
    started_at: datetime
    detail: str | None = None
    error: str | None = None


@dataclass
class AccountResult:
    row_number: int
    rut: str
    action: str  # Action, o el texto original si la fila se rechazó por tipo inválido
    account_id: str | None = None
    outcome: Outcome | None = None
    failed_step: str | None = None
    reason: str | None = None
    balance_before: Decimal | None = None
    amount_withdrawn: Decimal = Decimal("0")
    was_unblocked: bool = False
    account_blocked: bool = False
    account_closed: bool = False
    cards_blocked: list[str] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    processed_at: datetime = field(default_factory=now)
    holder_name: str = ""  # nombre y apellido del titular, solo para el correo

    def succeed(self, detail: str | None = None) -> None:
        self.outcome, self.reason = Outcome.SUCCESS, detail

    def skip(self, reason: str) -> None:
        self.outcome, self.reason = Outcome.SKIPPED, reason

    def fail(self, step: str, reason: str) -> None:
        self.outcome, self.failed_step, self.reason = Outcome.FAILED, step, reason

    def note(self, text: str) -> None:
        self.reason = f"{self.reason} | {text}" if self.reason else text


def rejected_row(
    row_number: int, rut: str, action: str, reason: str, outcome: Outcome = Outcome.FAILED
) -> AccountResult:
    """Fila descartada en la validación: nunca tocó una cuenta."""
    r = AccountResult(row_number, rut, action)
    failed = outcome == Outcome.FAILED
    r.steps.append(
        StepRecord(
            "validate_input",
            outcome,
            0,
            0,
            now(),
            detail=None if failed else reason,
            error=reason if failed else None,
        )
    )
    if failed:
        r.fail("validate_input", reason)
    else:
        r.skip(reason)
    return r


@dataclass
class RunReport:
    run_id: str
    file_name: str
    file_hash: str | None = None
    started_at: datetime = field(default_factory=now)
    finished_at: datetime | None = None
    results: list[AccountResult] = field(default_factory=list)
    fatal_error: str | None = None
    archived_to: str | None = None
    log_file: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        if self.fatal_error or not self.results:
            return RunStatus.FAILED
        failed = sum(r.outcome == Outcome.FAILED for r in self.results)
        if failed == 0:
            return RunStatus.SUCCESS
        return RunStatus.FAILED if failed == len(self.results) else RunStatus.PARTIAL

    def count(self, outcome: str, action: str | None = None) -> int:
        return sum(r.outcome == outcome and (action is None or r.action == action) for r in self.results)

    @property
    def total_withdrawn(self) -> Decimal:
        return sum((r.amount_withdrawn for r in self.results), Decimal("0"))
