"""Delta: runs, auditoría por paso y resultado por cuenta. El DDL lo crea el propio código."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any

from aml_account_closure.config import Config
from aml_account_closure.domain.models import Outcome, RunReport, now

log = logging.getLogger("accounts_ops")

DDL = {
    "runs_table": """run_id STRING, env STRING, dry_run BOOLEAN, file_name STRING, file_hash STRING,
        status STRING, total_rows INT, success_rows INT, skipped_rows INT, failed_rows INT,
        total_withdrawn DECIMAL(18,2), fatal_error STRING, warnings ARRAY<STRING>, archived_to STRING,
        log_file STRING, started_at TIMESTAMP, finished_at TIMESTAMP""",
    "steps_table": """run_id STRING, row_number INT, step_seq INT, rut STRING, account_id STRING,
        action STRING, step STRING, status STRING, attempts INT, duration_ms BIGINT, detail STRING,
        error STRING, started_at TIMESTAMP""",
    "results_table": """run_id STRING, env STRING, dry_run BOOLEAN, file_name STRING, row_number INT,
        rut STRING, requested_action STRING, account_id STRING, outcome STRING, failed_step STRING,
        reason STRING, balance_before DECIMAL(18,2), amount_withdrawn DECIMAL(18,2), was_unblocked BOOLEAN,
        account_blocked BOOLEAN, account_closed BOOLEAN, cards_blocked ARRAY<STRING>, processed_at TIMESTAMP""",
}
KEYS = {
    "runs_table": ["run_id"],
    "steps_table": ["run_id", "row_number", "step_seq"],
    "results_table": ["run_id", "row_number"],
}


def _money(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(Decimal("0.01"))


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    """Los Enum (StrEnum) se guardan como str simple: Spark no debe recibir clases propias."""
    return {k: v.value if isinstance(v, Enum) else v for k, v in row.items()}


def report_rows(r: RunReport, cfg: Config) -> dict[str, list[dict[str, Any]]]:
    """Filas a guardar por tabla (atributo de Config -> filas)."""
    base = {"run_id": r.run_id, "env": cfg.env, "dry_run": cfg.dry_run, "file_name": r.file_name}
    results = [
        {
            **base,
            "row_number": x.row_number,
            "rut": x.rut,
            "requested_action": x.action,
            "account_id": x.account_id,
            "outcome": x.outcome,
            "failed_step": x.failed_step,
            "reason": x.reason,
            "balance_before": _money(x.balance_before),
            "amount_withdrawn": _money(x.amount_withdrawn),
            "was_unblocked": x.was_unblocked,
            "account_blocked": x.account_blocked,
            "account_closed": x.account_closed,
            "cards_blocked": x.cards_blocked,
            "processed_at": x.processed_at,
        }
        for x in r.results
    ]
    steps = [
        {
            "run_id": r.run_id,
            "row_number": x.row_number,
            "step_seq": i,
            "rut": x.rut,
            "account_id": x.account_id,
            "action": x.action,
            "step": s.step,
            "status": s.status,
            "attempts": s.attempts,
            "duration_ms": s.duration_ms,
            "detail": s.detail,
            "error": s.error,
            "started_at": s.started_at,
        }
        for x in r.results
        for i, s in enumerate(x.steps, start=1)
    ]
    runs = [
        {
            **base,
            "file_hash": r.file_hash,
            "status": r.status,
            "total_rows": len(r.results),
            "success_rows": r.count(Outcome.SUCCESS),
            "skipped_rows": r.count(Outcome.SKIPPED),
            "failed_rows": r.count(Outcome.FAILED),
            "total_withdrawn": _money(r.total_withdrawn),
            "fatal_error": r.fatal_error,
            "warnings": r.warnings,
            "archived_to": r.archived_to,
            "log_file": r.log_file,
            "started_at": r.started_at,
            "finished_at": r.finished_at or now(),
        }
    ]
    # Mismo orden de escritura que el notebook: results -> steps -> runs
    return {
        "results_table": [_plain(x) for x in results],
        "steps_table": [_plain(x) for x in steps],
        "runs_table": [_plain(x) for x in runs],
    }


class SparkDeltaStore:
    def __init__(self, spark: Any, cfg: Config):
        self._spark = spark
        self._cfg = cfg
        self._tables_ready = False

    def ensure_tables(self) -> None:
        if not self._tables_ready:
            for attr, cols in DDL.items():
                self._spark.sql(f"CREATE TABLE IF NOT EXISTS {getattr(self._cfg, attr)} ({cols}) USING DELTA")
            self._tables_ready = True

    def find_previous_run(self, file_hash: str) -> str | None:
        self.ensure_tables()
        rows = self._spark.sql(
            f"SELECT run_id FROM {self._cfg.runs_table} WHERE file_hash = :h AND dry_run = false "
            "AND status IN ('SUCCESS', 'PARTIAL') LIMIT 1",
            args={"h": file_hash},
        ).collect()
        return str(rows[0]["run_id"]) if rows else None

    def _merge(self, attr: str, rows: list[dict[str, Any]]) -> None:
        """MERGE idempotente: guardar dos veces el mismo run no duplica filas."""
        if not rows:
            return
        table = getattr(self._cfg, attr)
        schema = self._spark.table(table).schema
        df = self._spark.createDataFrame([tuple(r[f.name] for f in schema.fields) for r in rows], schema=schema)
        view = f"_src_{attr}_{uuid.uuid4().hex[:8]}"
        df.createOrReplaceTempView(view)
        on = " AND ".join(f"t.{k} = s.{k}" for k in KEYS[attr])
        self._spark.sql(
            f"MERGE INTO {table} t USING {view} s ON {on} "
            "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
        )
        self._spark.catalog.dropTempView(view)

    def save_report(self, report: RunReport) -> None:
        self.ensure_tables()
        for attr, rows in report_rows(report, self._cfg).items():
            self._merge(attr, rows)
        log.info("Resultados guardados en Delta | run=%s", report.run_id)
