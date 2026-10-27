"""Archivos del Volume: pendientes en la entrada, hash del contenido y movimiento a procesados/error."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from aml_account_closure.config import CHILE_TZ, Config


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class VolumeFiles:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def list_pending(self) -> list[Path]:
        if not self.cfg.inbox.exists():
            return []
        files = [
            p
            for p in self.cfg.inbox.iterdir()
            if p.is_file() and p.suffix.lower() == ".xlsx" and not p.name.startswith(("~$", "."))
        ]
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def archive(self, path: Path, success: bool) -> str:
        target = self.cfg.processed if success else self.cfg.error
        target.mkdir(parents=True, exist_ok=True)
        dest = target / f"{datetime.now(CHILE_TZ):%Y%m%d_%H%M%S}_{path.name}"
        shutil.move(str(path), dest)
        return str(dest)
