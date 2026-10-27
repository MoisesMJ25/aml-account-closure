"""Unitaria 2: lectura y validación del Excel (generado en memoria con openpyxl)."""

import pytest

from aml_account_closure.adapters.excel_reader import parse_excel
from aml_account_closure.domain.errors import InvalidFileError
from aml_account_closure.domain.models import Action, Outcome
from tests.fixtures.factories import excel_bytes, fake_rut

RUT_A = fake_rut(11222333)
RUT_B = fake_rut(15555666)
RUT_C = fake_rut(17888999)


def test_valid_rows_with_column_aliases_and_action_aliases() -> None:
    content = excel_bytes([(RUT_A, "Bloqueo"), (RUT_B.replace("-", ""), "cerrar")], header=(" RUT Usuario ", "Acción"))

    valid, rejected = parse_excel(content)

    assert rejected == []
    assert [(r.row_number, r.rut, r.action) for r in valid] == [(2, RUT_A, Action.BLOCK), (3, RUT_B, Action.CLOSE)]


def test_rut_saved_by_excel_as_number() -> None:
    valid, _ = parse_excel(excel_bytes([(123456785.0, "cierre")]))

    assert valid[0].rut == "12345678-5"


def test_missing_required_column_is_fatal() -> None:
    with pytest.raises(InvalidFileError, match="Faltan columnas \\['type'\\]"):
        parse_excel(excel_bytes([(RUT_A,)], header=("rut", "observacion")))


def test_unreadable_file_is_fatal() -> None:
    with pytest.raises(InvalidFileError, match="No se pudo leer el Excel"):
        parse_excel(b"esto no es un xlsx")


def test_file_without_data_rows_is_fatal() -> None:
    with pytest.raises(InvalidFileError, match="no contiene filas"):
        parse_excel(excel_bytes([]))


def test_invalid_action_and_invalid_rut_are_rejected_as_failed() -> None:
    valid, rejected = parse_excel(excel_bytes([(RUT_A, "suspender"), ("12345678-9", "bloqueo"), (RUT_B, "cierre")]))

    assert [r.rut for r in valid] == [RUT_B]
    by_row = {r.row_number: r for r in rejected}
    assert by_row[2].outcome == Outcome.FAILED
    assert by_row[2].failed_step == "validate_input"
    assert by_row[2].reason == "Tipo inválido: 'suspender' (se espera 'bloqueo' o 'cierre')"
    assert by_row[3].outcome == Outcome.FAILED
    assert "Dígito verificador inválido" in (by_row[3].reason or "")
    assert by_row[3].steps[0].error == by_row[3].reason


def test_empty_rows_are_ignored_and_row_numbers_are_kept() -> None:
    valid, rejected = parse_excel(excel_bytes([(RUT_A, "bloqueo"), (), (None, None), (RUT_B, "cierre")]))

    assert rejected == []
    assert [r.row_number for r in valid] == [2, 5]


def test_duplicated_rows_same_action_are_processed_once() -> None:
    valid, rejected = parse_excel(excel_bytes([(RUT_A, "bloqueo"), (RUT_A, "Bloquear")]))

    assert [r.row_number for r in valid] == [2]
    assert len(rejected) == 1
    dup = rejected[0]
    assert (dup.row_number, dup.outcome, dup.reason) == (3, Outcome.SKIPPED, "Fila duplicada de la fila 2")
    assert dup.steps[0].detail == dup.reason
    assert dup.steps[0].error is None


def test_contradictory_actions_for_same_rut_reject_all_rows() -> None:
    valid, rejected = parse_excel(excel_bytes([(RUT_A, "bloqueo"), (RUT_C, "cierre"), (RUT_A, "cierre")]))

    assert [r.rut for r in valid] == [RUT_C]
    assert sorted(r.row_number for r in rejected) == [2, 4]
    assert all(r.outcome == Outcome.FAILED for r in rejected)
    assert all(r.reason == "RUT con gestiones contradictorias en filas 2, 4" for r in rejected)
