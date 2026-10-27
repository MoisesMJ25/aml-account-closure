"""SOLO DEV: cuentas y tarjetas desde un export JSON (temporal). Prod nunca llega aquí (ver load_config)."""

from __future__ import annotations

import logging
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from bson import json_util

from aml_account_closure.adapters.mongo_repo import MongoRepo, _get_path, _to_decimal
from aml_account_closure.config import CARD, MONGO
from aml_account_closure.domain.models import AccountSnapshot, Card
from aml_account_closure.domain.rut import rut_variants
from aml_account_closure.ports import AccountApi

log = logging.getLogger("accounts_ops")


def _load_extended_json(path: str) -> list[dict[str, Any]]:
    """Acepta arreglo JSON"""
    text = Path(path).read_text(encoding="utf-8").strip()
    for candidate in (text, "[" + text.rstrip(",") + "]"):
        try:
            data = json_util.loads(candidate)
            return data if isinstance(data, list) else [data]
        except ValueError:
            continue
    return [json_util.loads(line.rstrip(",")) for line in text.splitlines() if line.strip()]


def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    *parents, last = path.split(".")
    for part in parents:
        doc = doc.setdefault(part, {})
    doc[last] = value


class JsonAccountStore(MongoRepo):
    """Hereda _snapshot y _card de MongoRepo: los datos del JSON se validan con las mismas reglas."""

    def __init__(self, accounts: list[dict[str, Any]], cards: list[dict[str, Any]]):
        super().__init__(db=None)
        self._accounts = {str(d["_id"]): d for d in accounts}
        self._cards = {str(d["_id"]): d for d in cards}
        self._lock = threading.RLock()

    @classmethod
    def from_files(cls, accounts_path: str, cards_path: str | None = None) -> JsonAccountStore:
        accounts = _load_extended_json(accounts_path)
        cards: list[dict[str, Any]] = []
        if cards_path and Path(cards_path).exists():
            cards = _load_extended_json(cards_path)
        elif cards_path:
            log.warning("No existe %s: se asume que ninguna cuenta tiene tarjetas", cards_path)
        log.warning(
            "⚠️ DEV: cuentas leídas desde JSON (%d cuentas, %d tarjetas), NO desde Mongo", len(accounts), len(cards)
        )
        return cls(accounts, cards)

    def prefetch(self, ruts: list[str]) -> None:
        pass  # todo está en memoria

    def get_holder_names(self, account_ids: list[str]) -> dict[str, str]:
        return {}  # el export JSON de dev no incluye users: el nombre queda vacío

    def find_accounts_by_rut(self, rut: str) -> list[AccountSnapshot]:
        variants = {v.upper() for v in rut_variants(rut)}
        with self._lock:
            return [
                self._snapshot(d)
                for d in self._accounts.values()
                if str(_get_path(d, MONGO.account_rut)).upper() in variants
            ]

    def get_account(self, account_id: str) -> AccountSnapshot | None:
        with self._lock:
            doc = self._accounts.get(str(account_id))
            return self._snapshot(doc) if doc else None

    def list_cards(self, account_id: str) -> list[Card]:
        with self._lock:
            return [
                self._card(d) for d in self._cards.values() if str(_get_path(d, MONGO.card_account)) == str(account_id)
            ]

    # --- efectos reflejados por MirroredApi ---
    def set_account_status(self, account_id: str, status: str) -> None:
        with self._lock:
            if (doc := self._accounts.get(str(account_id))) is not None:
                _set_path(doc, MONGO.status, status)

    def debit(self, account_id: str, amount: Decimal) -> None:
        with self._lock:
            if (doc := self._accounts.get(str(account_id))) is not None:
                _set_path(doc, MONGO.balance, _to_decimal(_get_path(doc, MONGO.balance)) - amount)

    def set_card_status(self, card_id: str, status: str) -> None:
        with self._lock:
            if (doc := self._cards.get(str(card_id))) is not None:
                _set_path(doc, MONGO.card_status, status)


class MirroredApi:
    """Decorator: ejecuta la llamada (API de dev o dry-run) y, solo si fue exitosa, refleja el efecto en el JSON."""

    def __init__(self, api: AccountApi, store: JsonAccountStore):
        self._api, self._store = api, store

    def block_account(self, account_id: str, reason: str) -> None:
        self._api.block_account(account_id, reason)
        self._store.set_account_status(account_id, "BLOCKED")

    def unblock_account(self, account_id: str, reason: str) -> None:
        self._api.unblock_account(account_id, reason)
        self._store.set_account_status(account_id, "ENABLED")

    def delete_account(self, account_id: str, reason: str) -> None:
        self._api.delete_account(account_id, reason)
        self._store.set_account_status(account_id, "DELETED")

    def withdraw(self, account_id: str, amount: Decimal, reason: str) -> None:
        self._api.withdraw(account_id, amount, reason)
        self._store.debit(account_id, amount)

    def block_card(self, card_id: str, reason: str) -> None:
        self._api.block_card(card_id, reason)
        self._store.set_card_status(card_id, CARD["status"])
