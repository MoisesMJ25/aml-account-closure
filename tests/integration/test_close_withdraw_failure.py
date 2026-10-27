"""Integración 2: falla en el retiro durante un cierre -> compensación con re-bloqueo."""

from __future__ import annotations

from typing import Any

import pytest
import responses

from aml_account_closure.application.run import raise_if_failed
from aml_account_closure.domain.models import RunStatus
from tests.fixtures.factories import account_doc, card_doc, fake_rut
from tests.integration.conftest import FILE_NAME, api_url, called, files_in

RUT = fake_rut(18999000)
OTHER_RUT = fake_rut(19111222)

WITHDRAW_FAILURES = [
    # 4xx: BusinessError -> falla en el mismo paso, sin reintento
    pytest.param(400, "withdraw", 1, id="rechazo-4xx"),
    # 5xx en el retiro: AmbiguousError -> se concilia leyendo el saldo (5 lecturas) y sigue en el saldo original
    pytest.param(503, "reconcile_withdraw", 5, id="ambigua-no-aplicada"),
]


def seed_blocked_account(mongo_db: Any, rut: str = RUT) -> dict[str, Any]:
    account = account_doc(rut, status="BLOCKED", balance=80000)
    mongo_db.accounts.insert_one(account)
    mongo_db.cards.insert_one(card_doc(account["_id"], "VIRTUAL"))
    return account


def mock_failed_withdraw(api_mock: Any, acc_id: str, status_code: int) -> None:
    api_mock.add(responses.POST, api_url(f"accounts/unblock/{acc_id}"), json={})
    api_mock.add(
        responses.POST,
        api_url("accounts/fund/manual"),
        status=status_code,
        json={"error": {"message": "Operación rechazada", "internalCode": 4001}},
    )
    api_mock.add(responses.POST, api_url(f"accounts/block/{acc_id}"), json={})


@pytest.mark.parametrize(("status_code", "failed_step", "reconcile_reads"), WITHDRAW_FAILURES)
def test_withdraw_failure_compensates_with_reblock(
    api_mock: Any, run_file: Any, mongo_db: Any, sender: Any, status_code: int, failed_step: str, reconcile_reads: int
) -> None:
    account = seed_blocked_account(mongo_db)
    acc_id = str(account["_id"])
    mock_failed_withdraw(api_mock, acc_id, status_code)

    reports, ctx, _ = run_file([(RUT, "cierre")])

    # El retiro se intenta UNA vez (nunca a ciegas) y luego se re-bloquea; no hay tarjetas ni cierre.
    assert called(api_mock) == [
        f"POST accounts/unblock/{acc_id}",
        "POST accounts/fund/manual",
        f"POST accounts/block/{acc_id}",
    ]

    (report,) = reports
    (result,) = report.results
    assert result.outcome == "FAILED"
    assert result.failed_step == failed_step
    assert result.was_unblocked is True
    assert result.account_closed is False
    assert result.amount_withdrawn == 0
    assert result.reason and result.reason.endswith("Compensación: la cuenta fue re-bloqueada")
    withdraw_step = next(s for s in result.steps if s.step == "withdraw")
    assert (withdraw_step.status, withdraw_step.attempts) == ("FAILED", 1)
    assert f"HTTP {status_code}" in (withdraw_step.error or "")
    step_status = {s.step: (s.status, s.attempts) for s in result.steps}
    assert step_status[failed_step] == ("FAILED", reconcile_reads)
    assert step_status["compensate_reblock"] == ("SUCCESS", 1)

    # La cuenta no queda cerrada
    assert mongo_db.accounts.find_one({"_id": account["_id"]})["status"] != "DELETED"
    (row,) = ctx.store.rows("results_table")
    assert (row["outcome"], row["failed_step"], row["account_closed"]) == ("FAILED", failed_step, False)

    # Todas las filas fallaron -> run FAILED -> Con_Error, solo correo a Operaciones y el job falla
    assert report.status == RunStatus.FAILED
    assert ctx.store.rows("runs_table")[0]["status"] == "FAILED"
    assert files_in(ctx.cfg.inbox) == [] and files_in(ctx.cfg.processed) == []
    assert files_in(ctx.cfg.error)[0].endswith(FILE_NAME)
    (ops_email,) = sender.emails
    assert ops_email[1] == f"❌ Gestión de cuentas AML con errores - {FILE_NAME}"
    assert len(sender.messages) == 1
    with pytest.raises(RuntimeError, match="todas las filas fallaron"):
        raise_if_failed(reports)


def test_withdraw_failure_with_other_successful_row_is_partial(
    api_mock: Any, run_file: Any, mongo_db: Any, sender: Any
) -> None:
    account = seed_blocked_account(mongo_db)
    mock_failed_withdraw(api_mock, str(account["_id"]), 400)
    other = account_doc(OTHER_RUT, status="ENABLED", balance=0)
    mongo_db.accounts.insert_one(other)
    api_mock.add(responses.POST, api_url(f"accounts/block/{other['_id']}"), json={})

    reports, ctx, _ = run_file([(RUT, "cierre"), (OTHER_RUT, "bloqueo")])

    (report,) = reports
    assert [r.outcome for r in report.results] == ["FAILED", "SUCCESS"]
    assert report.status == RunStatus.PARTIAL
    assert files_in(ctx.cfg.processed)[0].endswith(FILE_NAME)  # PARTIAL se archiva como procesado
    assert files_in(ctx.cfg.error) == []
    (ops_email,) = sender.emails  # sin retiro exitoso no hay correo de éxito
    assert ops_email[1].startswith("⚠️ Gestión de cuentas AML parcialmente exitoso")
    raise_if_failed(reports)  # PARTIAL no hace fallar el job
