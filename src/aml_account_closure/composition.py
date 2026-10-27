"""Composition root: arma los adaptadores concretos y procesa los archivos pendientes."""

from __future__ import annotations

import logging
from typing import Any

from aml_account_closure.adapters.backoffice_api import BackofficeApi, DryRunApi
from aml_account_closure.adapters.delta_store import SparkDeltaStore
from aml_account_closure.adapters.dev_json import JsonAccountStore, MirroredApi
from aml_account_closure.adapters.log_publisher import VolumeLogPublisher, setup_logging
from aml_account_closure.adapters.mongo_repo import MongoRepo
from aml_account_closure.adapters.notifier import Notifier
from aml_account_closure.adapters.volume_files import VolumeFiles
from aml_account_closure.application.run import RunContext, process_file
from aml_account_closure.config import Config
from aml_account_closure.domain.models import RunReport
from aml_account_closure.ports import (
    AccountApi,
    AccountRepository,
    EmailSender,
    MongoConnector,
    MongoDisconnector,
    TeamsSender,
)

log = logging.getLogger("accounts_ops")


def run_pending(ctx: RunContext, connect_mongo: MongoConnector, disconnect_mongo: MongoDisconnector) -> list[RunReport]:
    """Procesa todos los Excel pendientes de la carpeta de entrada."""
    cfg = ctx.cfg
    pending = ctx.files.list_pending()
    if not pending:
        log.info("No hay archivos pendientes en %s", cfg.inbox)
        return []
    log.info("%d archivo(s) pendiente(s)", len(pending))

    client: Any = None
    api: AccountApi | None = None
    repo: AccountRepository | None = None
    setup_error: Exception | None = None
    try:
        api = DryRunApi() if cfg.dry_run else BackofficeApi(cfg)
        if cfg.dev_json_accounts:  # solo dev
            store = JsonAccountStore.from_files(cfg.dev_json_accounts, cfg.dev_json_cards)
            repo, api = store, MirroredApi(api, store)
        else:
            db, client = connect_mongo(cfg.mongo_connection, cfg.mongo_secret, read=True)
            repo = MongoRepo(db)
    except Exception as exc:
        log.exception("Error inicializando conexiones")
        setup_error = exc
    try:
        return [process_file(p, api, repo, ctx, setup_error) for p in pending]
    finally:
        if client is not None:
            disconnect_mongo(client)


def main(
    cfg: Config,
    *,
    spark: Any,
    connect_mongo: MongoConnector,
    disconnect_mongo: MongoDisconnector,
    send_email: EmailSender,
    send_message: TeamsSender,
) -> list[RunReport]:
    """Punto de entrada que llama el notebook con las dependencias de Databricks ya resueltas."""
    _, local_log = setup_logging()
    ctx = RunContext(
        cfg=cfg,
        store=SparkDeltaStore(spark, cfg),
        files=VolumeFiles(cfg),
        notifier=Notifier(cfg, send_email, send_message),
        logs=VolumeLogPublisher(cfg, local_log),
    )
    return run_pending(ctx, connect_mongo, disconnect_mongo)
