"""Logging con hora de Chile y copia del log local al Volume."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from aml_account_closure.config import CHILE_TZ, Config

LOGGER_NAME = "accounts_ops"


class ChileanFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=CHILE_TZ)
        return dt.strftime(datefmt) if datefmt else dt.strftime("%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d}"


def setup_logging() -> tuple[logging.Logger, Path]:
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):  # re-ejecutar no duplica handlers
        h.close()
        log.removeHandler(h)
    fmt = ChileanFormatter("%(asctime)s | %(levelname)-7s | %(threadName)s | %(message)s")
    local_file = Path(tempfile.gettempdir()) / f"accounts_ops_{datetime.now(CHILE_TZ):%Y%m%d_%H%M%S}.log"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(local_file, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log, local_file


class VolumeLogPublisher:
    def __init__(self, cfg: Config, local_log: Path):
        self.cfg = cfg
        self.local_log = local_log

    def publish(self) -> str | None:
        """Copia el log local al Volume (se llama al cierre de cada archivo)."""
        log = logging.getLogger(LOGGER_NAME)
        try:
            for h in log.handlers:
                h.flush()
            self.cfg.logs.mkdir(parents=True, exist_ok=True)
            dest = self.cfg.logs / self.local_log.name
            shutil.copy(self.local_log, dest)
            return str(dest)
        except Exception as exc:
            log.error("No se pudo copiar el log al Volume: %s", exc)
            return None
