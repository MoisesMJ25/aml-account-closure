"""Lectura y validación del Excel de Cumplimiento."""

from __future__ import annotations

import io
from collections import defaultdict

import pandas as pd

from aml_account_closure.domain.errors import InvalidFileError
from aml_account_closure.domain.models import AccountRequest, AccountResult, Action, Outcome, rejected_row
from aml_account_closure.domain.rut import normalize_rut

COLUMN_ALIASES = {
    "rut": {"rut", "rut_usuario", "rut usuario", "rut_cliente"},
    "type": {"type", "tipo", "gestion", "gestión", "accion", "acción"},
}
ACTION_ALIASES = {"bloqueo": Action.BLOCK, "bloquear": Action.BLOCK, "cierre": Action.CLOSE, "cerrar": Action.CLOSE}


def parse_excel(content: bytes) -> tuple[list[AccountRequest], list[AccountResult]]:
    """Devuelve (solicitudes válidas, filas rechazadas). Solo el archivo ilegible es fatal."""
    try:
        df = pd.read_excel(io.BytesIO(content), dtype=str, engine="openpyxl")  # dtype=str: no perder formato del RUT
    except Exception as exc:
        raise InvalidFileError(f"No se pudo leer el Excel: {exc}") from exc

    rename = {c: canon for c in df.columns for canon, al in COLUMN_ALIASES.items() if str(c).strip().lower() in al}
    df = df.rename(columns=rename)
    missing = [c for c in COLUMN_ALIASES if c not in df.columns]
    if missing:
        raise InvalidFileError(f"Faltan columnas {missing}. Encontradas: {list(df.columns)}")
    df = df[["rut", "type"]].dropna(how="all")
    if df.empty:
        raise InvalidFileError("El archivo no contiene filas con datos")

    valid: list[AccountRequest] = []
    rejected: list[AccountResult] = []
    for idx, row in df.iterrows():
        row_number = int(idx) + 2
        raw_rut = "" if pd.isna(row["rut"]) else str(row["rut"]).strip()
        raw_type = "" if pd.isna(row["type"]) else str(row["type"]).strip()
        try:
            rut = normalize_rut(raw_rut)
        except ValueError as exc:
            rejected.append(rejected_row(row_number, raw_rut, raw_type, str(exc)))
            continue
        action = ACTION_ALIASES.get(raw_type.lower())
        if not action:
            rejected.append(
                rejected_row(row_number, rut, raw_type, f"Tipo inválido: {raw_type!r} (se espera 'bloqueo' o 'cierre')")
            )
            continue
        valid.append(AccountRequest(row_number, rut, action))

    # Duplicados: misma gestión -> se procesa una vez. Gestiones contradictorias -> se rechazan todas.
    by_rut: dict[str, list[AccountRequest]] = defaultdict(list)
    for r in valid:
        by_rut[r.rut].append(r)
    requests_: list[AccountRequest] = []
    for rut, reqs in by_rut.items():
        if len({r.action for r in reqs}) > 1:
            rows = ", ".join(str(r.row_number) for r in reqs)
            rejected += [
                rejected_row(r.row_number, rut, r.action, f"RUT con gestiones contradictorias en filas {rows}")
                for r in reqs
            ]
            continue
        first, *dups = reqs
        requests_.append(first)
        rejected += [
            rejected_row(
                d.row_number, rut, d.action, f"Fila duplicada de la fila {first.row_number}", outcome=Outcome.SKIPPED
            )
            for d in dups
        ]
    return requests_, rejected
