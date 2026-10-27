"""Dobles de prueba: nada de Databricks, Mongo real ni red."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import mongomock
import pytest

from aml_account_closure.adapters.delta_store import KEYS, report_rows
from aml_account_closure.adapters.log_publisher import VolumeLogPublisher
from aml_account_closure.adapters.notifier import Notifier
from aml_account_closure.adapters.volume_files import VolumeFiles
from aml_account_closure.application import retry
from aml_account_closure.application.run import RunContext
from aml_account_closure.config import Config, EnvSettings, load_config
from aml_account_closure.domain.models import AccountSnapshot, Card, RunReport

API_BASE_URL = "https://backoffice.example.test/api"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las políticas de reintento no esperan de verdad."""
    monkeypatch.setattr(retry, "sleep", lambda _seconds: None)


class FakeSecrets:
    """Reemplaza dbutils.secrets."""

    def __init__(self) -> None:
        self.requested: list[tuple[str, str]] = []

    def get(self, scope: str, key: str) -> str:
        self.requested.append((scope, key))
        return "fake-api-key"


class InMemoryResultStore:
    """Mismo contrato que SparkDeltaStore; guarda las filas que se escribirían en Delta."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tables: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {}

    def find_previous_run(self, file_hash: str) -> str | None:
        for row in self.tables.get("runs_table", {}).values():
            if row["file_hash"] == file_hash and not row["dry_run"] and row["status"] in ("SUCCESS", "PARTIAL"):
                return str(row["run_id"])
        return None

    def save_report(self, report: RunReport) -> None:
        for attr, rows in report_rows(report, self.cfg).items():
            table = self.tables.setdefault(attr, {})
            for row in rows:  # MERGE por llave
                table[tuple(row[k] for k in KEYS[attr])] = row

    def rows(self, attr: str) -> list[dict[str, Any]]:
        return list(self.tables.get(attr, {}).values())


class FakeSender:
    """Reemplaza utils.send_email y utils.send_message."""

    def __init__(self) -> None:
        self.emails: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str]] = []

    def send_email(self, to: str, subject: str, body: str) -> None:
        self.emails.append((to, subject, body))

    def send_message(self, message: str, chat_id: str) -> None:
        self.messages.append((message, chat_id))


class FakeRepo:
    """Repositorio en memoria para probar handlers sin Mongo."""

    def __init__(self, accounts: list[AccountSnapshot], cards: dict[str, list[Card]] | None = None):
        self.accounts = accounts
        self.cards = cards or {}

    def prefetch(self, ruts: list[str]) -> None:
        pass

    def find_accounts_by_rut(self, rut: str) -> list[AccountSnapshot]:
        return list(self.accounts)

    def get_account(self, account_id: str) -> AccountSnapshot | None:
        return next((a for a in self.accounts if a.account_id == account_id), None)

    def get_holder_names(self, account_ids: list[str]) -> dict[str, str]:
        return {}

    def list_cards(self, account_id: str) -> list[Card]:
        return self.cards.get(account_id, [])


class RecordingApi:
    """API falsa que registra cada llamada en orden."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def block_account(self, account_id: str, reason: str) -> None:
        self.calls.append(("block_account", account_id))

    def unblock_account(self, account_id: str, reason: str) -> None:
        self.calls.append(("unblock_account", account_id))

    def delete_account(self, account_id: str, reason: str) -> None:
        self.calls.append(("delete_account", account_id))

    def block_card(self, card_id: str, reason: str) -> None:
        self.calls.append(("block_card", card_id))

    def withdraw(self, account_id: str, amount: Decimal, reason: str) -> None:
        self.calls.append(("withdraw", account_id))


def make_settings(aml_dir: Path, **overrides: str) -> EnvSettings:
    values = {
        "api_base_url": API_BASE_URL,
        "api_key_scope": "<scope_api_key>",
        "api_key_name": "<key_api_key>",
        "mongo_connection": "<mongo_connection>",
        "mongo_secret": "<mongo_secret>",
        "aml_dir": str(aml_dir),
        "tables_schema": "catalogo_test.esquema_test",
        "email_recipients": "operaciones@example.test, cumplimiento@example.test",
        "success_recipients": "tesoreria@example.test",
        "teams_chat_id": "<teams_chat_id>",
        "operator_user": "operador@example.test",
        "treasury_source_account": "<cuenta_origen>",
        "treasury_target_account": "<cuenta_destino>",
        "logo_url": "https://example.test/logo.svg",
        **overrides,
    }
    return EnvSettings(**values)


@pytest.fixture
def aml_dir(tmp_path: Path) -> Path:
    """Volume temporal con la carpeta de entrada creada."""
    root = tmp_path / "Cierre_Cuentas_Fraude_AML"
    (root / "A_Procesar").mkdir(parents=True)
    return root


@pytest.fixture
def make_config(aml_dir: Path) -> Callable[..., Config]:
    def _make(env: str = "dev", dry_run: bool = False, force: bool = False, **overrides: str) -> Config:
        return load_config(env, dry_run, force, make_settings(aml_dir, **overrides), FakeSecrets())

    return _make


@pytest.fixture
def mongo_db() -> Any:
    return mongomock.MongoClient()["test_db"]


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def make_context(tmp_path: Path, sender: FakeSender) -> Callable[[Config], RunContext]:
    def _make(cfg: Config) -> RunContext:
        local_log = tmp_path / "accounts_ops_test.log"
        local_log.write_text("log de prueba\n", encoding="utf-8")
        return RunContext(
            cfg=cfg,
            store=InMemoryResultStore(cfg),
            files=VolumeFiles(cfg),
            notifier=Notifier(cfg, sender.send_email, sender.send_message),
            logs=VolumeLogPublisher(cfg, local_log),
        )

    return _make
