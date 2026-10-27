"""Orquestación de un archivo: validar -> gestionar cuentas -> archivar -> log -> persistir -> notificar."""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aml_account_closure.adapters.excel_reader import parse_excel
from aml_account_closure.adapters.volume_files import file_hash
from aml_account_closure.application import retry
from aml_account_closure.application.account_actions import safe_handle
from aml_account_closure.application.retry import RETRY_POLICY
from aml_account_closure.config import Config
from aml_account_closure.domain.errors import DuplicateFileError, TransientError, describe
from aml_account_closure.domain.models import AccountResult, RunReport, RunStatus, now
from aml_account_closure.ports import (
    AccountApi,
    AccountRepository,
    FileArchive,
    LogPublisher,
    ResultStore,
    RunNotifier,
)

log = logging.getLogger("accounts_ops")


@dataclass(frozen=True)
class RunContext:
    """Dependencias de un run (las arma el composition root)."""

    cfg: Config
    store: ResultStore
    files: FileArchive
    notifier: RunNotifier
    logs: LogPublisher


def prefetch_accounts(repo: AccountRepository, ruts: list[str]) -> None:
    """Una sola consulta por archivo; reintenta ante errores transitorios de Mongo."""
    for attempt in range(1, RETRY_POLICY.max_attempts + 1):
        try:
            return repo.prefetch(ruts)
        except TransientError:
            if attempt == RETRY_POLICY.max_attempts:
                raise
            retry.sleep(RETRY_POLICY.delay(attempt))


def attach_holder_names(repo: AccountRepository, results: list[AccountResult]) -> None:
    """Agrega el nombre del titular a cada resultado, solo para el correo.
    account_id puede traer varias cuentas cerradas separadas por coma."""
    ids = sorted({i.strip() for r in results if r.account_id for i in r.account_id.split(",")})
    names = repo.get_holder_names(ids)
    for r in results:
        if r.account_id:
            r.holder_name = ", ".join(n for i in r.account_id.split(",") if (n := names.get(i.strip())))


def process_file(
    path: Path,
    api: AccountApi | None,
    repo: AccountRepository | None,
    ctx: RunContext,
    setup_error: Exception | None = None,
) -> RunReport:
    cfg = ctx.cfg
    report = RunReport(run_id=uuid.uuid4().hex, file_name=path.name)
    log.info("═" * 70)
    log.info("INICIO run=%s | archivo=%s | env=%s | dry_run=%s", report.run_id, path.name, cfg.env, cfg.dry_run)
    try:
        if setup_error:  # ej: Mongo caído -> igual se notifica
            raise setup_error
        assert api is not None and repo is not None  # sin setup_error, ambos existen
        content = path.read_bytes()
        report.file_hash = file_hash(content)
        if not cfg.dry_run and not cfg.force:
            previous = ctx.store.find_previous_run(report.file_hash)
            if previous:
                raise DuplicateFileError(
                    f"Este archivo ya se procesó en el run {previous}. Si es intencional, re-ejecuta con force=true."
                )
        if cfg.dev_json_accounts:
            report.warnings.append("DEV: cuentas en JSON, no Mongo")
        requests_, rejected = parse_excel(content)
        log.info("Validación | válidas=%d | rechazadas=%d", len(requests_), len(rejected))
        if requests_:
            prefetch_accounts(repo, [r.rut for r in requests_])
        report.results.extend(rejected)
        # Hilos (no Spark): el trabajo es esperar respuestas HTTP (I/O-bound).
        with ThreadPoolExecutor(max_workers=cfg.max_workers, thread_name_prefix="cuenta") as pool:
            report.results.extend(pool.map(lambda r: safe_handle(r, api, repo, cfg.dry_run), requests_))
        report.results.sort(key=lambda x: x.row_number)

        try:  # el nombre es informativo: si falla, el correo sale sin nombres y la gestión no se ve afectada
            attach_holder_names(repo, report.results)
        except Exception as exc:
            log.warning("No se pudieron obtener los nombres de los titulares: %s", describe(exc))
            report.warnings.append("No se pudieron obtener los nombres de los titulares")

    except Exception as exc:
        log.exception("Error general procesando %s", path.name)
        report.fatal_error = describe(exc)
    finally:
        # SIEMPRE: archivar -> log -> persistir -> notificar (cada paso aislado del siguiente)
        report.finished_at = now()
        if not cfg.dry_run:
            try:
                report.archived_to = ctx.files.archive(path, success=report.status != RunStatus.FAILED)
            except Exception as exc:
                log.exception("No se pudo archivar el archivo")
                report.warnings.append(f"No se pudo mover el archivo: {describe(exc)}")
        log.info(
            "FIN run=%s | estado=%s | filas=%d | retirado=%s",
            report.run_id,
            report.status,
            len(report.results),
            report.total_withdrawn,
        )
        report.log_file = ctx.logs.publish()
        try:
            ctx.store.save_report(report)
        except Exception as exc:
            log.exception("No se pudieron guardar los resultados")
            report.warnings.append(f"No se pudo guardar en Delta: {describe(exc)}")
        ctx.notifier.notify(report)
    return report


def summary_rows(reports: list[RunReport]) -> list[dict[str, Any]]:
    """Resumen que el notebook muestra con display()."""
    return [
        {
            "run_id": r.run_id,
            "archivo": r.file_name,
            "estado": str(r.status),
            "filas": len(r.results),
            "retirado": float(r.total_withdrawn),
            "error_general": r.fatal_error,
            "archivado_en": r.archived_to,
        }
        for r in reports
    ]


def raise_if_failed(reports: list[RunReport]) -> None:
    """Un run FAILED hace fallar el job (activa alertas nativas). PARTIAL no."""
    failed = [r for r in reports if r.status == RunStatus.FAILED]
    if failed:
        raise RuntimeError(
            "Archivos con estado FAILED: "
            + ", ".join(f"{r.file_name} ({r.fatal_error or 'todas las filas fallaron'})" for r in failed)
        )
