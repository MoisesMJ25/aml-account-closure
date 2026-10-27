"""Adaptador de la API backoffice.

Las operaciones idempotentes (bloqueos, cierre, tarjetas) se reintentan ante errores transitorios.
El RETIRO no es idempotente: solo se reintenta si la conexión nunca se estableció (o con 429);
ante una respuesta ambigua se concilia leyendo el saldo real.
"""

from __future__ import annotations

import logging
import threading
from decimal import Decimal
from typing import Any

import requests

from aml_account_closure.config import CARD, WITHDRAW_METADATA, WITHDRAW_REFERENCE, WITHDRAW_TYPE, Config
from aml_account_closure.domain.errors import AmbiguousError, BusinessError, TransientError

log = logging.getLogger("accounts_ops")


class BackofficeApi:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._local = threading.local()  # una Session por hilo

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({"Content-Type": "application/json", "x-api-key": self.cfg.api_key})
            self._local.session = s
        session: requests.Session = self._local.session
        return session

    def _audit(self, reason: str) -> dict[str, Any]:
        return {"user": self.cfg.operator_user, "origin": self.cfg.origin, "reason": reason}

    def _call(self, method: str, path: str, payload: dict[str, Any], idempotent: bool) -> None:
        try:
            resp = self._session().request(
                method, f"{self.cfg.api_base_url.rstrip('/')}/{path}", json=payload, timeout=self.cfg.http_timeout
            )
        except requests.exceptions.ConnectTimeout as exc:
            raise TransientError(f"Timeout de conexión en {method} /{path}") from exc
        except requests.exceptions.RequestException as exc:
            raise (TransientError if idempotent else AmbiguousError)(
                f"Error de red en {method} /{path}: {type(exc).__name__}"
            ) from exc
        code = resp.status_code
        if 200 <= code < 300:
            return
        msg = f"HTTP {code} en {method} /{path}: {_api_error_text(resp)}"
        if code == 429:
            raise TransientError(msg)
        if code >= 500 or code == 408:
            raise (TransientError if idempotent else AmbiguousError)(msg)
        raise BusinessError(msg)

    def block_account(self, account_id: str, reason: str) -> None:
        self._call("POST", f"accounts/block/{account_id}", self._audit(reason), idempotent=True)

    def unblock_account(self, account_id: str, reason: str) -> None:
        self._call("POST", f"accounts/unblock/{account_id}", self._audit(reason), idempotent=True)

    def delete_account(self, account_id: str, reason: str) -> None:
        self._call("POST", f"accounts/delete/{account_id}", self._audit(reason), idempotent=True)

    def block_card(self, card_id: str, reason: str) -> None:
        self._call("PUT", f"card/{card_id}", {**CARD, **self._audit(reason)}, idempotent=True)

    def withdraw(self, account_id: str, amount: Decimal, reason: str) -> None:
        if amount != amount.to_integral_value():
            raise BusinessError(f"Monto con decimales ({amount}); revisar la unidad del saldo")
        payload = {
            "accountId": account_id,
            "type": WITHDRAW_TYPE,
            "amount": int(amount),
            "reference": WITHDRAW_REFERENCE,
            "metadata": dict(WITHDRAW_METADATA),
            **self._audit(reason),
        }
        self._call("POST", "accounts/fund/manual", payload, idempotent=False)


def _api_error_text(resp: requests.Response) -> str:
    """Backoffice responde "error": extrae solo lo legible."""
    try:
        err = resp.json().get("error") or {}
        parts = [str(err.get("message") or "").strip()]
        if err.get("internalCode") is not None:
            parts.append(f"código {err['internalCode']}")
        if err.get("metadata"):
            parts.append(f"detalle {err['metadata']}")
        text = " | ".join(p for p in parts if p)
        if text:
            return text
    except Exception:
        pass
    return (resp.text or "")[:300]


class DryRunApi:
    """Mismo contrato que BackofficeApi, pero solo registra lo que haría."""

    @staticmethod
    def _log(operation: str, target: str, *args: Any) -> None:
        log.info("[DRY-RUN] %s %s %s", operation, target, args)

    def block_account(self, account_id: str, reason: str) -> None:
        self._log("block_account", account_id, reason)

    def unblock_account(self, account_id: str, reason: str) -> None:
        self._log("unblock_account", account_id, reason)

    def delete_account(self, account_id: str, reason: str) -> None:
        self._log("delete_account", account_id, reason)

    def block_card(self, card_id: str, reason: str) -> None:
        self._log("block_card", card_id, reason)

    def withdraw(self, account_id: str, amount: Decimal, reason: str) -> None:
        self._log("withdraw", account_id, amount, reason)
