"""Unitaria 3: qué pasos aplican según la acción y el estado de la cuenta y sus tarjetas."""

from decimal import Decimal

import pytest

from aml_account_closure.adapters.mongo_repo import MongoRepo
from aml_account_closure.application.account_actions import Handler, handle_block, handle_close
from aml_account_closure.domain import decisions
from aml_account_closure.domain.errors import BusinessError
from aml_account_closure.domain.models import AccountRequest, AccountResult, AccountSnapshot, Action, Card, Outcome
from tests.conftest import FakeRepo, RecordingApi
from tests.fixtures.factories import fake_rut

RUT = fake_rut(13444555)


def snapshot(account_id: str, status: str, balance: str = "0") -> AccountSnapshot:
    is_blocked, is_deleted = decisions.account_flags(status)
    return AccountSnapshot(account_id, Decimal(balance), is_blocked, is_deleted, status)


def steps(result: AccountResult) -> list[tuple[str, str]]:
    return [(s.step, s.status) for s in result.steps]


# _____Decisiones puras_____


def test_choose_account_rules() -> None:
    enabled, closed = snapshot("a1", "ENABLED"), snapshot("a0", "DELETED")

    assert decisions.choose_account([closed, enabled]) == enabled
    assert decisions.choose_account([closed]) is None  # solo cerradas
    with pytest.raises(BusinessError, match="No se encontró ninguna cuenta"):
        decisions.choose_account([])
    with pytest.raises(BusinessError, match="2 cuentas activas"):
        decisions.choose_account([enabled, snapshot("a2", "BLOCKED")])
    with pytest.raises(BusinessError, match="DISABLED .*revisión manual"):
        decisions.choose_account([snapshot("a3", "DISABLED", "1000")])


def test_balance_and_block_decisions() -> None:
    assert decisions.needs_block(snapshot("a", "ENABLED"))
    assert not decisions.needs_block(snapshot("a", "BLOCKED"))
    assert decisions.needs_unblock_before_withdraw(snapshot("a", "BLOCKED"))
    assert not decisions.needs_withdraw(Decimal("0"))
    assert decisions.needs_withdraw(Decimal("5000"))
    assert decisions.has_negative_balance(Decimal("-1"))


@pytest.mark.parametrize(("status", "active"), [("ACTIVE", True), ("BLOCKED", True), ("DISABLED", False)])
def test_card_status_decides_if_card_is_blocked(status: str, active: bool) -> None:
    """Solo DISABLED se omite: una tarjeta BLOCKED igual se envía a deshabilitar (como en el notebook)."""
    card = MongoRepo(db=None)._card({"_id": "c1", "type": "physical", "status": status})

    assert card == Card("c1", "PHYSICAL", active)
    assert decisions.card_needs_block(card) is active


# _____Bloqueo_____


def test_block_account_already_blocked_is_skipped_without_api_calls() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-1", "BLOCKED", "2500")])

    result = handle_block(AccountRequest(2, RUT, Action.BLOCK), api, repo, dry_run=False)

    assert result.outcome == Outcome.SKIPPED
    assert result.reason == "La cuenta ya estaba bloqueada"
    assert result.account_blocked is True
    assert api.calls == []
    assert steps(result) == [("resolve_account", "SUCCESS"), ("block_account", "SKIPPED")]


def test_block_enabled_account_blocks_only_the_account() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-1", "ENABLED")], cards={"acc-1": [Card("c1", "VIRTUAL", True)]})

    result = handle_block(AccountRequest(2, RUT, Action.BLOCK), api, repo, dry_run=False)

    assert result.outcome == Outcome.SUCCESS
    assert api.calls == [("block_account", "acc-1")]  # el bloqueo no toca tarjetas de una cuenta activa


# _____Cuenta ya cerrada: las tarjetas se bloquean igual_____


@pytest.mark.parametrize("handler", [handle_block, handle_close])
def test_closed_account_still_blocks_active_cards(handler: Handler) -> None:
    api = RecordingApi()
    cards = [Card("c-act", "VIRTUAL", True), Card("c-blk", "PHYSICAL", True), Card("c-dis", "PHYSICAL", False)]
    repo = FakeRepo([snapshot("acc-0", "DELETED")], cards={"acc-0": cards})

    result = handler(AccountRequest(2, RUT, Action.CLOSE), api, repo, False)

    assert api.calls == [("block_card", "c-act"), ("block_card", "c-blk")]
    assert result.outcome == Outcome.SUCCESS
    assert result.reason == "La cuenta ya estaba cerrada; 2 tarjeta(s) bloqueada(s)"
    assert result.account_id == "acc-0"
    assert result.account_closed is False
    assert ("block_card_physical", "SKIPPED") in steps(result)


def test_closed_account_without_active_cards_is_skipped() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-0", "DELETED")], cards={"acc-0": [Card("c-dis", "VIRTUAL", False)]})

    result = handle_close(AccountRequest(2, RUT, Action.CLOSE), api, repo, dry_run=False)

    assert api.calls == []
    assert result.outcome == Outcome.SKIPPED
    assert result.reason == "La cuenta ya estaba cerrada; sin tarjetas activas"


# _____Cierre_____


def test_close_with_zero_balance_skips_withdraw_and_unblock() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-1", "BLOCKED", "0")], cards={"acc-1": [Card("c1", "VIRTUAL", True)]})

    result = handle_close(AccountRequest(2, RUT, Action.CLOSE), api, repo, dry_run=False)

    assert api.calls == [("block_card", "c1"), ("delete_account", "acc-1")]
    assert result.outcome == Outcome.SUCCESS
    assert result.reason == "Cuenta cerrada, 1 tarjeta(s) bloqueada(s)"
    assert ("withdraw", "SKIPPED") in steps(result)


def test_close_with_negative_balance_fails_without_api_calls() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-1", "ENABLED", "-10")])

    result = handle_close(AccountRequest(2, RUT, Action.CLOSE), api, repo, dry_run=False)

    assert api.calls == []
    assert (result.outcome, result.failed_step) == (Outcome.FAILED, "check_balance")


def test_close_dry_run_skips_balance_verification() -> None:
    api = RecordingApi()
    repo = FakeRepo([snapshot("acc-1", "ENABLED", "1000")])

    result = handle_close(AccountRequest(2, RUT, Action.CLOSE), api, repo, dry_run=True)

    assert api.calls == [("withdraw", "acc-1"), ("delete_account", "acc-1")]
    assert ("verify_zero_balance", "SKIPPED") in steps(result)
    assert result.amount_withdrawn == Decimal("1000")
