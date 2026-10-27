"""Repository de Mongo: cuentas por metadata.rut, tarjetas y nombre del titular (solo lectura)."""

from __future__ import annotations

import functools
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from typing import Any, ParamSpec, TypeVar

from bson import Decimal128, ObjectId
from pymongo.errors import PyMongoError

from aml_account_closure.config import MONGO
from aml_account_closure.domain import decisions
from aml_account_closure.domain.errors import BusinessError, TransientError
from aml_account_closure.domain.models import AccountSnapshot, Card
from aml_account_closure.domain.rut import rut_variants

log = logging.getLogger("accounts_ops")

P = ParamSpec("P")
R = TypeVar("R")


def _get_path(doc: Any, path: str) -> Any:
    for part in path.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        raise BusinessError(f"La cuenta no tiene el campo de saldo '{MONGO.balance}'")
    if isinstance(value, Decimal128):
        return value.to_decimal()
    try:
        return Decimal(str(value))  # str() evita arrastrar el error del float
    except InvalidOperation as exc:
        raise BusinessError(f"Saldo no numérico: {value!r}") from exc


def _id_variants(values: Iterable[Any]) -> list[Any]:
    """Un id puede estar guardado como ObjectId o como string: se busca por ambos."""
    out: list[Any] = []
    for v in values:
        for c in (v, str(v), ObjectId(str(v)) if ObjectId.is_valid(str(v)) else None):
            if c is not None and c not in out:
                out.append(c)
    return out


def mongo_call(fn: Callable[P, R]) -> Callable[P, R]:
    """Decorator: errores de pymongo -> TransientError (reintentables)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except PyMongoError as exc:
            raise TransientError(f"Mongo: {exc}") from exc

    return wrapper


class MongoRepo:
    PROJECTION = {"_id": 1, MONGO.balance: 1, MONGO.currency: 1, MONGO.status: 1}

    def __init__(self, db: Any):
        self.db = db
        self._ids_by_rut: dict[str, list[Any]] | None = None

    @staticmethod
    def _status(doc: dict[str, Any], path: str) -> str:
        return str(_get_path(doc, path) or "").upper()

    def _snapshot(self, doc: dict[str, Any]) -> AccountSnapshot:
        currency = _get_path(doc, MONGO.currency)
        if currency not in (None, MONGO.expected_currency):
            raise BusinessError(f"Moneda inesperada en la cuenta {doc['_id']}: {currency!r}")
        status = self._status(doc, MONGO.status)

        if not decisions.is_known_status(status):
            raise BusinessError(f"Status desconocido {status!r} en la cuenta {doc['_id']}; no se opera")
        is_blocked, is_deleted = decisions.account_flags(status)
        return AccountSnapshot(
            str(doc["_id"]), _to_decimal(_get_path(doc, MONGO.balance)), is_blocked, is_deleted, status
        )

    def _card(self, doc: dict[str, Any]) -> Card:
        return Card(
            str(doc["_id"]),
            str(_get_path(doc, MONGO.card_type) or "UNKNOWN").upper(),
            decisions.is_card_active(self._status(doc, MONGO.card_status)),
        )

    @mongo_call
    def prefetch(self, ruts: list[str]) -> None:
        """Una sola consulta por archivo: metadata.rut no tiene índice."""
        variants = sorted({v for r in ruts for v in rut_variants(r)})
        ids: dict[str, list[Any]] = defaultdict(list)
        for d in self.db[MONGO.accounts].find({MONGO.account_rut: {"$in": variants}}, {"_id": 1, MONGO.account_rut: 1}):
            ids[str(_get_path(d, MONGO.account_rut)).upper()].append(d["_id"])
        self._ids_by_rut = dict(ids)
        log.info("Prefetch Mongo | ruts=%d | con cuenta=%d", len(set(ruts)), len(self._ids_by_rut))

    @mongo_call
    def find_accounts_by_rut(self, rut: str) -> list[AccountSnapshot]:
        if self._ids_by_rut is None:
            query: dict[str, Any] = {MONGO.account_rut: {"$in": rut_variants(rut)}}
        else:
            ids = self._ids_by_rut.get(rut.upper(), [])
            if not ids:
                return []
            query = {"_id": {"$in": ids}}  # lectura por índice
        return [self._snapshot(d) for d in self.db[MONGO.accounts].find(query, self.PROJECTION)]

    @mongo_call
    def get_account(self, account_id: str) -> AccountSnapshot | None:
        doc = self.db[MONGO.accounts].find_one({"_id": {"$in": _id_variants([account_id])}}, self.PROJECTION)
        return self._snapshot(doc) if doc else None

    @mongo_call
    def get_holder_names(self, account_ids: list[str]) -> dict[str, str]:
        """accountId -> "Nombre Apellido". Cuentas sin usuario o nombre no aparecen"""
        if not account_ids:
            return {}
        docs = self.db[MONGO.users].find(
            {MONGO.user_account: {"$in": _id_variants(account_ids)}},
            {MONGO.user_account: 1, MONGO.user_name: 1, MONGO.user_lastname: 1},
        )
        names: dict[str, str] = {}
        for d in docs:
            parts = (_get_path(d, MONGO.user_name), _get_path(d, MONGO.user_lastname))
            full = " ".join(str(p).strip() for p in parts if p and str(p).strip())
            if full:
                names[str(_get_path(d, MONGO.user_account))] = full
        return names

    @mongo_call
    def list_cards(self, account_id: str) -> list[Card]:
        docs = self.db[MONGO.cards].find(
            {MONGO.card_account: {"$in": _id_variants([account_id])}},
            {"_id": 1, MONGO.card_type: 1, MONGO.card_status: 1},
        )
        return [self._card(d) for d in docs]
