"""Reglas de negocio por gestión: bloqueo y cierre (saga con compensación)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

from aml_account_closure.application.retry import VERIFY_POLICY
from aml_account_closure.application.step_trail import StepFailed, StepTrail
from aml_account_closure.config import REASONS
from aml_account_closure.domain import decisions
from aml_account_closure.domain.errors import AmbiguousError, BusinessError, TransientError, describe
from aml_account_closure.domain.models import AccountRequest, AccountResult, AccountSnapshot, Action
from aml_account_closure.ports import AccountApi, AccountRepository

log = logging.getLogger("accounts_ops")


def resolve_account(req: AccountRequest, trail: StepTrail, repo: AccountRepository) -> AccountSnapshot | None:
    """Ver decisions.choose_account: None significa que el RUT solo tiene cuentas cerradas."""
    return trail.run(
        "resolve_account",
        lambda: decisions.choose_account(repo.find_accounts_by_rut(req.rut)),
        detail=lambda a: (
            f"accountId={a.account_id} status={a.status} saldo={a.balance}" if a else "Solo cuentas cerradas"
        ),
    )


def _block_active_cards(
    account_id: str, trail: StepTrail, api: AccountApi, repo: AccountRepository, result: AccountResult
) -> None:
    """Bloquea las tarjetas activas de la cuenta. Las DISABLED se registran como omitidas."""
    cards = trail.run(
        "list_cards",
        lambda: repo.list_cards(account_id),
        detail=lambda cs: f"{len(cs)} tarjeta(s) en cuenta {account_id}",
    )
    for card in cards:
        step = f"block_card_{card.card_type.lower()}"
        if not decisions.card_needs_block(card):
            trail.skip(step, f"Tarjeta {card.card_id} ya inactiva")
            continue
        trail.run(step, partial(api.block_card, card.card_id, REASONS["card"]), detail=f"Tarjeta {card.card_id}")
        result.cards_blocked.append(card.card_id)


def _handle_closed_account(
    req: AccountRequest, trail: StepTrail, api: AccountApi, repo: AccountRepository, result: AccountResult
) -> AccountResult:
    """Cuenta ya cerrada: la cuenta no se toca, pero sus tarjetas activas sí se bloquean."""
    closed = trail.run(
        "find_closed_accounts",
        lambda: decisions.closed_accounts(repo.find_accounts_by_rut(req.rut)),
        detail=lambda accs: f"{len(accs)} cuenta(s) cerrada(s)",
    )
    result.account_id = ", ".join(a.account_id for a in closed)
    for acc in closed:
        _block_active_cards(acc.account_id, trail, api, repo, result)
    if result.cards_blocked:
        result.succeed(f"La cuenta ya estaba cerrada; {len(result.cards_blocked)} tarjeta(s) bloqueada(s)")
    else:
        result.skip("La cuenta ya estaba cerrada; sin tarjetas activas")
    return result


def handle_block(req: AccountRequest, api: AccountApi, repo: AccountRepository, dry_run: bool) -> AccountResult:
    """resolver -> bloquear la cuenta (si no lo está). Solo una cuenta ya cerrada bloquea tarjetas."""
    result = AccountResult(req.row_number, req.rut, req.action)
    trail = StepTrail(result)
    try:
        account = resolve_account(req, trail, repo)
        if account is None:
            return _handle_closed_account(req, trail, api, repo, result)

        acc_id = account.account_id
        result.account_id, result.balance_before = acc_id, account.balance
        if not decisions.needs_block(account):  # idempotencia por estado
            trail.skip("block_account", "La cuenta ya estaba bloqueada")
            result.account_blocked = True
            result.skip("La cuenta ya estaba bloqueada")
            return result
        trail.run("block_account", lambda: api.block_account(acc_id, REASONS["block"]))
        result.account_blocked = True
        result.succeed("Cuenta bloqueada")
    except StepFailed as exc:
        result.fail(exc.step, describe(exc.cause))
    return result


def _verify_zero(account_id: str, trail: StepTrail, repo: AccountRepository, step: str) -> None:
    def check() -> None:
        snap = repo.get_account(account_id)
        if snap is None:
            raise BusinessError("La cuenta no se encontró al verificar el saldo")
        if snap.balance != 0:  # se reintenta con VERIFY_POLICY
            raise TransientError(f"Saldo actual {snap.balance}, se esperaba 0")

    trail.run(step, check, policy=VERIFY_POLICY, detail="Saldo confirmado en 0")


def handle_close(req: AccountRequest, api: AccountApi, repo: AccountRepository, dry_run: bool) -> AccountResult:
    """resolver -> (desbloquear -> retirar -> verificar 0) -> bloquear tarjetas -> cerrar.
    Tarjetas ANTES del cierre: si el cierre falla, las tarjetas ya quedan en estado seguro."""
    result = AccountResult(req.row_number, req.rut, req.action)
    trail = StepTrail(result)
    try:
        account = resolve_account(req, trail, repo)
        if account is None:
            return _handle_closed_account(req, trail, api, repo, result)

        acc_id, amount = account.account_id, account.balance
        result.account_id, result.balance_before = acc_id, amount

        # 1. Vaciar la cuenta
        if decisions.has_negative_balance(amount):
            trail.fail("check_balance", BusinessError(f"Saldo negativo ({amount}); requiere revisión manual"))
        if not decisions.needs_withdraw(amount):
            trail.skip("withdraw", "Saldo 0, no requiere retiro")
        else:
            if decisions.needs_unblock_before_withdraw(account):
                trail.run("unblock_account", lambda: api.unblock_account(acc_id, REASONS["unblock"]))
                result.was_unblocked = True
            try:
                trail.run(
                    "withdraw", lambda: api.withdraw(acc_id, amount, REASONS["withdraw"]), detail=f"Retiro de {amount}"
                )
            except StepFailed as exc:
                if not isinstance(exc.cause, AmbiguousError):
                    raise
                # El servidor pudo aplicar el retiro sin respondernos: reintentar podría debitar dos veces.
                _verify_zero(acc_id, trail, repo, "reconcile_withdraw")
            else:
                if dry_run:
                    trail.skip("verify_zero_balance", "Dry-run: el saldo no cambia")
                else:
                    _verify_zero(acc_id, trail, repo, "verify_zero_balance")
            result.amount_withdrawn = amount

        # 2. Bloquear tarjetas activas (física/virtual)
        _block_active_cards(acc_id, trail, api, repo, result)

        # 3. Cerrar la cuenta
        trail.run("close_account", lambda: api.delete_account(acc_id, REASONS["close"]))
        result.account_closed = True
        parts = (
            ["Cuenta cerrada"]
            + ([f"retiro de {amount}"] if result.amount_withdrawn else [])
            + ([f"{len(result.cards_blocked)} tarjeta(s) bloqueada(s)"] if result.cards_blocked else [])
        )
        result.succeed(", ".join(parts))

    except StepFailed as exc:
        result.fail(exc.step, describe(exc.cause))
        # Saga: si se desbloqueó y no se cerró, re-bloquear para no dejar una cuenta AML desbloqueada.
        if decisions.needs_compensation(result):
            reblock_id = str(result.account_id)
            try:
                trail.run("compensate_reblock", lambda: api.block_account(reblock_id, REASONS["compensation"]))
                result.note("Compensación: la cuenta fue re-bloqueada")
            except StepFailed:
                result.note("ATENCIÓN: falló la compensación, la cuenta quedó DESBLOQUEADA")
    return result


Handler = Callable[[AccountRequest, AccountApi, AccountRepository, bool], AccountResult]

# Strategy: un handler por tipo de gestión
HANDLERS: dict[str, Handler] = {Action.BLOCK: handle_block, Action.CLOSE: handle_close}


def safe_handle(req: AccountRequest, api: AccountApi, repo: AccountRepository, dry_run: bool) -> AccountResult:
    """Red de seguridad: un bug en una cuenta no afecta a las demás."""
    try:
        result = HANDLERS[req.action](req, api, repo, dry_run)
    except Exception as exc:
        log.exception("Error inesperado en fila %s", req.row_number)
        result = AccountResult(req.row_number, req.rut, req.action)
        result.fail("unexpected", f"Error inesperado: {describe(exc)}")
    return result
