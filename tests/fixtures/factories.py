"""Datos de prueba inventados: RUTs ficticios con DV válido, Excel generado y documentos Mongo."""

from __future__ import annotations

import io
from typing import Any

from bson import ObjectId
from openpyxl import Workbook

from aml_account_closure.domain.rut import rut_check_digit


def fake_rut(body: int) -> str:
    """RUT ficticio normalizado con dígito verificador válido."""
    return f"{body}-{rut_check_digit(str(body))}"


def excel_bytes(rows: list[tuple[Any, ...]], header: tuple[str, ...] = ("RUT", "Tipo")) -> bytes:
    """Genera un .xlsx en memoria. Una fila `()` deja la fila vacía."""
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def account_doc(rut: str, status: str = "ENABLED", balance: Any = 0, oid: ObjectId | None = None) -> dict[str, Any]:
    return {
        "_id": oid or ObjectId(),
        "metadata": {"rut": rut},
        "balance": {"amount": balance, "currency": "CLP"},
        "status": status,
    }


def card_doc(account_id: ObjectId, card_type: str = "VIRTUAL", status: str = "ACTIVE") -> dict[str, Any]:
    return {"_id": ObjectId(), "accountId": account_id, "type": card_type, "status": status}


def user_doc(account_id: ObjectId, name: str, lastname: str) -> dict[str, Any]:
    return {"_id": ObjectId(), "fidiAccountId": account_id, "personalData": {"name": name, "lastname": lastname}}
