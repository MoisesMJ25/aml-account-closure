"""Decisiones puras: qué pasos aplican según la acción y el estado de la cuenta o tarjeta.

Son las mismas condiciones que usaba el notebook dentro de los handlers, extraídas sin cambios
para poder probarlas sin API ni Mongo. Los handlers (application/account_actions.py) las llaman
en el mismo orden que antes.
"""

from decimal import Decimal

from aml_account_closure.config import MONGO
from aml_account_closure.domain.errors import BusinessError
from aml_account_closure.domain.models import AccountResult, AccountSnapshot, Card


def account_flags(status: str) -> tuple[bool, bool]:
    """(is_blocked, is_deleted) según el status de Mongo."""
    return status in MONGO.blocked_values, status in MONGO.deleted_values


def is_known_status(status: str) -> bool:
    return status in MONGO.known_statuses


def is_card_active(status: str) -> bool:
    """Toda tarjeta que no esté DISABLED se intenta bloquear (incluidas las BLOCKED)."""
    return status not in MONGO.card_inactive_values


def choose_account(accounts: list[AccountSnapshot]) -> AccountSnapshot | None:
    """0 cuentas -> error · solo cerradas -> None (se tratan como cerradas) · >1 no eliminadas -> error
    (revisión) · 1 DISABLED -> error (revisión) · 1 ENABLED/BLOCKED -> esa."""
    if not accounts:
        raise BusinessError("No se encontró ninguna cuenta asociada al RUT")
    active = [a for a in accounts if not a.is_deleted]
    if len(active) > 1:
        raise BusinessError(
            f"El RUT tiene {len(active)} cuentas activas "
            f"({', '.join(a.account_id for a in active)}); requiere revisión manual"
        )
    account = active[0] if active else None
    if account and account.status in MONGO.manual_review_values:
        raise BusinessError(
            f"Cuenta {account.account_id} en estado {account.status} (saldo {account.balance}): "
            "la API no opera cuentas inactivas; requiere revisión manual"
        )
    return account


def closed_accounts(accounts: list[AccountSnapshot]) -> list[AccountSnapshot]:
    return [a for a in accounts if a.is_deleted]


def needs_block(account: AccountSnapshot) -> bool:
    """Bloqueo: idempotencia por estado (si ya está bloqueada, se omite)."""
    return not account.is_blocked


def card_needs_block(card: Card) -> bool:
    return card.is_active


def has_negative_balance(balance: Decimal) -> bool:
    return balance < 0


def needs_withdraw(balance: Decimal) -> bool:
    return balance != 0


def needs_unblock_before_withdraw(account: AccountSnapshot) -> bool:
    """La API no permite retirar de una cuenta bloqueada."""
    return account.is_blocked


def needs_compensation(result: AccountResult) -> bool:
    """Saga: si se desbloqueó y no se cerró, hay que re-bloquear."""
    return result.was_unblocked and not result.account_closed
