"""Integración 1: cierre completo de una cuenta BLOCKED con saldo y tarjetas activas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import responses

from aml_account_closure.application.run import raise_if_failed
from aml_account_closure.domain.models import RunStatus
from tests.fixtures.factories import account_doc, card_doc, fake_rut, user_doc
from tests.integration.conftest import FILE_NAME, api_url, body_of, called, files_in

RUT = fake_rut(16777888)
BALANCE = 150000


def test_close_blocked_account_with_balance_and_cards(api_mock: Any, run_file: Any, mongo_db: Any, sender: Any) -> None:
    account = account_doc(RUT, status="BLOCKED", balance=BALANCE)
    acc_id = str(account["_id"])
    virtual, physical = card_doc(account["_id"], "VIRTUAL"), card_doc(account["_id"], "PHYSICAL")
    mongo_db.accounts.insert_one(account)
    mongo_db.cards.insert_many([virtual, physical])
    mongo_db.users.insert_one(user_doc(account["_id"], "Ana", "Pérez"))

    def withdraw_applied(request: Any) -> tuple[int, dict[str, str], str]:
        # El backoffice real debita el saldo: se refleja en Mongo para que la verificación lo vea en 0.
        mongo_db.accounts.update_one({"_id": account["_id"]}, {"$set": {"balance.amount": 0}})
        return 200, {}, "{}"

    api_mock.add(responses.POST, api_url(f"accounts/unblock/{acc_id}"), json={})
    api_mock.add_callback(responses.POST, api_url("accounts/fund/manual"), callback=withdraw_applied)
    api_mock.add(responses.PUT, api_url(f"card/{virtual['_id']}"), json={})
    api_mock.add(responses.PUT, api_url(f"card/{physical['_id']}"), json={})
    api_mock.add(responses.POST, api_url(f"accounts/delete/{acc_id}"), json={})

    reports, ctx, session = run_file([(RUT, "cierre")])

    # Orden de la saga: desbloquear -> retirar -> bloquear tarjetas -> cerrar
    assert called(api_mock) == [
        f"POST accounts/unblock/{acc_id}",
        "POST accounts/fund/manual",
        f"PUT card/{virtual['_id']}",
        f"PUT card/{physical['_id']}",
        f"POST accounts/delete/{acc_id}",
    ]
    withdraw = body_of(api_mock, "accounts/fund/manual")
    assert withdraw["accountId"] == acc_id
    assert withdraw["amount"] == BALANCE
    assert withdraw["type"] == "WITHDRAWALS"
    assert withdraw["user"] == "operador@example.test"
    assert body_of(api_mock, f"card/{virtual['_id']}")["status"] == "DISABLED"

    # Resultado SUCCESS en el store de resultados
    (report,) = reports
    assert report.status == RunStatus.SUCCESS
    (row,) = ctx.store.rows("results_table")
    assert row["outcome"] == "SUCCESS"
    assert row["requested_action"] == "cierre"
    assert row["amount_withdrawn"] == Decimal("150000.00")
    assert row["was_unblocked"] is True and row["account_closed"] is True
    assert row["cards_blocked"] == [str(virtual["_id"]), str(physical["_id"])]
    (run_row,) = ctx.store.rows("runs_table")
    assert run_row["status"] == "SUCCESS"
    assert run_row["total_withdrawn"] == Decimal("150000.00")
    assert [s["step"] for s in ctx.store.rows("steps_table")] == [
        "resolve_account",
        "unblock_account",
        "withdraw",
        "verify_zero_balance",
        "list_cards",
        "block_card_virtual",
        "block_card_physical",
        "close_account",
    ]

    # Archivo movido a procesados (fuera de la carpeta vigilada) y log publicado
    cfg = ctx.cfg
    assert files_in(cfg.inbox) == []
    (archived,) = files_in(cfg.processed)
    assert archived.endswith(FILE_NAME)
    assert report.archived_to and report.archived_to.endswith(archived)
    assert files_in(cfg.logs)
    assert session.disconnected == [session.client]

    # Correo a Operaciones + correo de éxito (nombre y monto) + Teams
    (ops_to, ops_subject, ops_body), (ok_to, ok_subject, ok_body) = sender.emails
    assert ops_to == "operaciones@example.test, cumplimiento@example.test"
    assert ops_subject == f"✅ Gestión de cuentas AML exitoso - {FILE_NAME}"
    assert "$150.000" in ops_body
    assert ok_to == "tesoreria@example.test"
    assert ok_subject == f"🔐 Cierre automático de cuentas exitoso - {FILE_NAME}"
    assert "<td>Ana Pérez</td>" in ok_body
    assert "<td>$150.000</td>" in ok_body
    assert "Total retirado: <u>$150.000</u>" in ok_body
    assert "<cuenta_origen>" in ok_body and "<cuenta_destino>" in ok_body
    (teams_body, chat_id) = sender.messages[0]
    assert chat_id == "<teams_chat_id>"
    assert "Total retirado:</b> $150.000" in teams_body

    raise_if_failed(reports)  # SUCCESS: el job no falla
