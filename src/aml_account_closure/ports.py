"""Contratos (Protocols) entre la aplicación y el mundo exterior.

Las dependencias corporativas (conectar_mongo, desconectar_mongo, send_email, send_message) y las de
Databricks (dbutils.secrets, spark) no viven en este repo: el notebook de entrada las inyecta.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from aml_account_closure.domain.models import AccountSnapshot, Card, RunReport

# _____Dependencias externas (inyectadas desde el notebook)_____


class SecretReader(Protocol):
    """dbutils.secrets"""

    def get(self, scope: str, key: str) -> str: ...


class MongoConnector(Protocol):
    """conectar_mongo del %run corporativo: devuelve (db, client)."""

    def __call__(self, connection: str, secret: str, read: bool = ...) -> tuple[Any, Any]: ...


class MongoDisconnector(Protocol):
    """desconectar_mongo del %run corporativo."""

    def __call__(self, client: Any) -> Any: ...


class EmailSender(Protocol):
    """utils.send_email"""

    def __call__(self, to: str, subject: str, body: str) -> Any: ...


class TeamsSender(Protocol):
    """utils.send_message"""

    def __call__(self, message: str, chat_id: str) -> Any: ...


# _____Puertos de la aplicación_____


class AccountRepository(Protocol):
    """Lectura de cuentas, tarjetas y titulares (Mongo o JSON de dev)."""

    def prefetch(self, ruts: list[str]) -> None: ...

    def find_accounts_by_rut(self, rut: str) -> list[AccountSnapshot]: ...

    def get_account(self, account_id: str) -> AccountSnapshot | None: ...

    def get_holder_names(self, account_ids: list[str]) -> dict[str, str]: ...

    def list_cards(self, account_id: str) -> list[Card]: ...


class AccountApi(Protocol):
    """Operaciones del backoffice (reales, dry-run o reflejadas en el JSON de dev)."""

    def block_account(self, account_id: str, reason: str) -> None: ...

    def unblock_account(self, account_id: str, reason: str) -> None: ...

    def delete_account(self, account_id: str, reason: str) -> None: ...

    def block_card(self, card_id: str, reason: str) -> None: ...

    def withdraw(self, account_id: str, amount: Decimal, reason: str) -> None: ...


class ResultStore(Protocol):
    """Persistencia de runs, pasos y resultados (Delta)."""

    def find_previous_run(self, file_hash: str) -> str | None: ...

    def save_report(self, report: RunReport) -> None: ...


class FileArchive(Protocol):
    """Archivos del Volume."""

    def list_pending(self) -> list[Path]: ...

    def archive(self, path: Path, success: bool) -> str: ...


class LogPublisher(Protocol):
    def publish(self) -> str | None: ...


class RunNotifier(Protocol):
    def notify(self, report: RunReport) -> None: ...
