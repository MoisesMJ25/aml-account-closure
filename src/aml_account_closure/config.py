"""Configuración del proceso.

- Constantes del negocio (campos de Mongo, motivos de auditoría, payloads de la API): viven aquí.
- Valores por ambiente (URLs, destinatarios, chats, rutas, secretos): llegan como parámetros del job,
  que a su vez vienen de las variables del Databricks Asset Bundle. Nada de eso se hardcodea en el paquete.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from aml_account_closure.ports import SecretReader

CHILE_TZ = ZoneInfo("America/Santiago")

ENVIRONMENTS = ("dev", "prod")
ORIGIN = "ops-databricks"
MAX_WORKERS = 4  # llamadas HTTP en paralelo
HTTP_TIMEOUT = 10

# Sub-carpetas dentro de la carpeta AML del Volume (dev y prod comparten carpetas).
INBOX_FOLDER = "A_Procesar"
PROCESSED_FOLDER = "Procesados"
ERROR_FOLDER = "Con_Error"
LOGS_FOLDER = "Logs"

TABLE_TEMPLATE = "{schema}.{env}_ops_cierre_accounts_{name}"


@dataclass(frozen=True)
class MongoFields:
    """Colecciones y campos de Mongo que usa el proceso."""

    accounts: str = "accounts"
    account_rut: str = "metadata.rut"
    balance: str = "balance.amount"
    currency: str = "balance.currency"
    expected_currency: str = "CLP"
    status: str = "status"  # ENABLED | BLOCKED | DISABLED | DELETED
    blocked_values: tuple[str, ...] = ("BLOCKED",)
    deleted_values: tuple[str, ...] = ("DELETED",)
    # la API no opera cuentas inactivas: se deriva a revisión manual
    manual_review_values: tuple[str, ...] = ("DISABLED",)
    # otro valor: no se opera la cuenta
    known_statuses: tuple[str, ...] = ("ENABLED", "BLOCKED", "DISABLED", "DELETED")
    cards: str = "cards"
    card_account: str = "accountId"  # ObjectId. El endpoint PUT card/{id} usa el _id de cards.
    card_type: str = "type"  # VIRTUAL | PHYSICAL
    card_status: str = "status"  # ACTIVE | BLOCKED | DISABLED
    # no se bloquean. cualquier otro status sí se intenta bloquear
    card_inactive_values: tuple[str, ...] = ("DISABLED",)
    users: str = "users"  # solo para mostrar el nombre en el correo
    user_account: str = "fidiAccountId"  # ObjectId de la cuenta
    user_name: str = "personalData.name"
    user_lastname: str = "personalData.lastname"


MONGO = MongoFields()

# Motivos de auditoría que se envían a la API.
REASONS = {
    "block": "bloqueo aml",
    "unblock": "desbloqueo aml",
    "withdraw": "cierre de cuenta aml",
    "close": "cierre de cuenta aml",
    "card": "cierre cuenta aml",
    "compensation": "bloqueo aml - compensacion por cierre fallido",
}

# Payload fijo del retiro (POST accounts/fund/manual).
WITHDRAW_TYPE = "WITHDRAWALS"
WITHDRAW_REFERENCE = "Ajuste operativo"
WITHDRAW_METADATA = {"paymentId": "", "provider": "MetroPago", "product": "Cash_out"}

# Payload fijo del bloqueo de tarjeta (PUT card/{id}).
CARD = {"status": "DISABLED", "status_reason": "USER_INTERNAL_REASON"}


@dataclass(frozen=True)
class EnvSettings:
    """Valores por ambiente. Cada campo es un parámetro del job con el mismo nombre."""

    api_base_url: str
    api_key_scope: str
    api_key_name: str
    mongo_connection: str
    mongo_secret: str
    aml_dir: str
    tables_schema: str
    email_recipients: str  # un string con los correos separados por coma (se envía en un solo send_email)
    success_recipients: str  # vacío -> no se envía 2° correo
    teams_chat_id: str
    operator_user: str
    treasury_source_account: str
    treasury_target_account: str
    logo_url: str
    dev_json_accounts: str = ""  # solo dev: JSON en vez de Mongo
    dev_json_cards: str = ""

    @classmethod
    def param_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def from_params(cls, get: Callable[[str], str]) -> EnvSettings:
        """Construye la configuración leyendo cada parámetro con `get` (ej: dbutils.widgets.get)."""
        return cls(**{name: (get(name) or "").strip() for name in cls.param_names()})


@dataclass(frozen=True)
class Config:
    env: str
    api_base_url: str
    api_key: str
    mongo_connection: str
    mongo_secret: str
    inbox_dir: str
    processed_dir: str
    error_dir: str
    logs_dir: str
    runs_table: str
    steps_table: str
    results_table: str
    email_recipients: tuple[str, ...]
    teams_chat_id: str
    operator_user: str
    origin: str
    max_workers: int
    http_timeout: int
    dry_run: bool
    force: bool
    treasury_source_account: str = ""
    treasury_target_account: str = ""
    logo_url: str = ""
    dev_json_accounts: str | None = None  # solo dev: JSON en vez de Mongo
    dev_json_cards: str | None = None
    success_recipients: tuple[str, ...] = ()  # vacío -> no se envía 2° correo

    @property
    def inbox(self) -> Path:
        return Path(self.inbox_dir)

    @property
    def processed(self) -> Path:
        return Path(self.processed_dir)

    @property
    def error(self) -> Path:
        return Path(self.error_dir)

    @property
    def logs(self) -> Path:
        return Path(self.logs_dir)


def _recipients(value: str) -> tuple[str, ...]:
    """El notebook guarda los destinatarios como una tupla de UN string separado por comas."""
    return (value,) if value else ()


def load_config(env: str, dry_run: bool, force: bool, settings: EnvSettings, secrets: SecretReader) -> Config:
    if env not in ENVIRONMENTS:
        raise ValueError(f"Ambiente desconocido: {env!r}")
    if env != "dev" and settings.dev_json_accounts:
        raise ValueError("dev_json_accounts solo está permitido en dev")  # en prod siempre se lee Mongo
    aml_dir = settings.aml_dir.rstrip("/")
    tables = {
        name: TABLE_TEMPLATE.format(schema=settings.tables_schema, env=env, name=name)
        for name in ("runs", "steps", "results")
    }
    return Config(
        env=env,
        api_base_url=settings.api_base_url,
        api_key=secrets.get(scope=settings.api_key_scope, key=settings.api_key_name),
        mongo_connection=settings.mongo_connection,
        mongo_secret=settings.mongo_secret,
        inbox_dir=f"{aml_dir}/{INBOX_FOLDER}",
        processed_dir=f"{aml_dir}/{PROCESSED_FOLDER}",
        error_dir=f"{aml_dir}/{ERROR_FOLDER}",
        logs_dir=f"{aml_dir}/{LOGS_FOLDER}",
        runs_table=tables["runs"],
        steps_table=tables["steps"],
        results_table=tables["results"],
        email_recipients=_recipients(settings.email_recipients),
        teams_chat_id=settings.teams_chat_id,
        operator_user=settings.operator_user,
        origin=ORIGIN,
        max_workers=MAX_WORKERS,
        http_timeout=HTTP_TIMEOUT,
        dry_run=dry_run,
        force=force,
        treasury_source_account=settings.treasury_source_account,
        treasury_target_account=settings.treasury_target_account,
        logo_url=settings.logo_url,
        dev_json_accounts=settings.dev_json_accounts or None,
        dev_json_cards=settings.dev_json_cards or None,
        success_recipients=_recipients(settings.success_recipients),
    )
