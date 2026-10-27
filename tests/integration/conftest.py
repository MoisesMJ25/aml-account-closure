"""Ayudas para integración: Excel en el Volume temporal, Mongo con mongomock y la API con `responses`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import responses

from aml_account_closure.application.run import RunContext
from aml_account_closure.composition import run_pending
from aml_account_closure.config import Config
from aml_account_closure.domain.models import RunReport
from tests.conftest import API_BASE_URL
from tests.fixtures.factories import excel_bytes

FILE_NAME = "gestion_aml_test.xlsx"


class MongoSession:
    """Reemplaza conectar_mongo / desconectar_mongo."""

    def __init__(self, db: Any):
        self.db = db
        self.client = object()
        self.disconnected: list[Any] = []

    def connect(self, connection: str, secret: str, read: bool = False) -> tuple[Any, Any]:
        assert read is True  # el proceso abre Mongo solo en lectura
        return self.db, self.client

    def disconnect(self, client: Any) -> None:
        self.disconnected.append(client)


@pytest.fixture
def api_mock() -> Any:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def run_file(
    make_config: Callable[..., Config], make_context: Callable[[Config], RunContext], mongo_db: Any
) -> Callable[..., tuple[list[RunReport], RunContext, MongoSession]]:
    """Deja un Excel en A_Procesar y ejecuta el proceso completo (composition root)."""

    def _run(rows: list[tuple[Any, ...]], **cfg_kwargs: Any) -> tuple[list[RunReport], RunContext, MongoSession]:
        cfg = make_config(**cfg_kwargs)
        (cfg.inbox / FILE_NAME).write_bytes(excel_bytes(rows))
        ctx = make_context(cfg)
        session = MongoSession(mongo_db)
        reports = run_pending(ctx, session.connect, session.disconnect)
        return reports, ctx, session

    return _run


def api_url(path: str) -> str:
    return f"{API_BASE_URL}/{path}"


def called(mock: Any) -> list[str]:
    """Llamadas a la API en orden, como "METODO ruta"."""
    return [f"{c.request.method} {c.request.url.removeprefix(API_BASE_URL + '/')}" for c in mock.calls]


def body_of(mock: Any, path: str) -> dict[str, Any]:
    call = next(c for c in mock.calls if c.request.url == api_url(path))
    return dict(json.loads(call.request.body))


def files_in(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []
