"""Notificaciones: correo a Operaciones (siempre), correo de éxito (solo con retiro) y Teams."""

from __future__ import annotations

import html
import logging
from decimal import Decimal

from aml_account_closure.config import Config
from aml_account_closure.domain.models import RunReport, RunStatus
from aml_account_closure.ports import EmailSender, TeamsSender

log = logging.getLogger("accounts_ops")

ICON = {RunStatus.SUCCESS: "✅", RunStatus.PARTIAL: "⚠️", RunStatus.FAILED: "❌"}
LABEL = {RunStatus.SUCCESS: "Exitoso", RunStatus.PARTIAL: "Parcialmente exitoso", RunStatus.FAILED: "Con errores"}


def clp(amount: Decimal) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


def _counts(r: RunReport, action: str) -> str:
    return f"{r.count('SUCCESS', action) + r.count('SKIPPED', action) + r.count('FAILED', action)}"


def build_teams_message(r: RunReport, cfg: Config) -> str:
    e = html.escape
    invalid = sum(x.failed_step == "validate_input" for x in r.results)
    extra = (
        (f"<li><b>Filas inválidas:</b> {invalid}</li>" if invalid else "")
        + (f"<li><b>Error general:</b> {e(r.fatal_error)}</li>" if r.fatal_error else "")
        + "".join(f"<li><b>Advertencia:</b> {e(w)}</li>" for w in r.warnings)
    )
    return f"""
        <p><b>{ICON[r.status]} Gestión de cuentas AML | {LABEL[r.status]} {' (DRY-RUN)' if cfg.dry_run else ''}</b></p>
        <hr>
        <ul>
            <li><b>Archivo:</b> {e(r.file_name)}</li>
            <li><b>Total casos:</b> {len(r.results)}</li>
            <li><b>Bloqueos:</b> {_counts(r, 'bloqueo')}</li>
            <li><b>Cierres:</b> {_counts(r, 'cierre')}</li>
            <li><b>Fallidos:</b> {r.count('FAILED', 'bloqueo') + r.count('FAILED', 'cierre')}</li>
            <li><b>Total retirado:</b> {clp(r.total_withdrawn)}</li>{extra}
        </ul>
        <hr>
        <p><b>run_id:</b> {r.run_id}</p>"""


def build_success_email(r: RunReport, cfg: Config) -> tuple[str, str]:
    e = html.escape
    subject = f"🔐 Cierre automático de cuentas {LABEL[r.status].lower()} - {r.file_name}"
    withdrawn = [x for x in r.results if x.amount_withdrawn > 0]  # solo cuentas con saldo retirado
    rows = "".join(
        f"<tr><td>{x.row_number}</td><td>{e(x.rut)}</td><td>{e(x.holder_name)}</td><td>{e(x.action)}</td>"
        f"<td>{e(x.account_id or '-')}</td><td>{x.outcome}</td>"
        f"<td>{clp(x.amount_withdrawn)}</td></tr>"
        for x in withdrawn
    )

    body = f"""<html><body>
        {f'''<h4>Gestión automática de cuentas por AML</h4>
        <h4>Total retirado: <u>{clp(r.total_withdrawn)}</u></h4>
        <em>Equipo tesorería, favor transferir el total retirado desde la cuenta operativa {cfg.treasury_source_account} a la cuenta administrativa {cfg.treasury_target_account}. Luego, emitir el Vale Vista desde esta última cuenta a nombre del usuario.</em><br><br>
        <table border="1" cellpadding="4" cellspacing="0">
            <tr><th>Fila</th><th>RUT</th><th>Nombre</th><th>Gestión</th><th>accountId</th><th>Resultado</th><th>Retirado</th></tr>
            {rows}
        </table>'''}
        <br><br>Saludos,<br>Equipo de Operaciones<br><br>
        <img src='{cfg.logo_url}' width='150' alt='Logo'/>
        </body></html>"""  # noqa: E501

    return subject, body


def build_email(r: RunReport, cfg: Config) -> tuple[str, str]:
    e = html.escape
    subject = (
        f"{'[DRY-RUN] ' if cfg.dry_run else ''}{ICON[r.status]} Gestión de cuentas AML "
        f"{LABEL[r.status].lower()} - {r.file_name}"
    )
    rows = "".join(
        f"<tr><td>{x.row_number}</td><td>{e(x.rut)}</td><td>{e(x.action)}</td>"
        f"<td>{e(x.account_id or '-')}</td><td>{x.outcome}</td>"
        f"<td>{clp(x.amount_withdrawn) if x.amount_withdrawn else '-'}</td></tr>"
        for x in r.results[:300]
    )
    body = f"""<html><body>
        <h4>Resumen de la gestión</h4>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><td>Estado</td><td><b>{LABEL[r.status]}</b></td></tr>
          <tr><td>Ambiente</td><td>{cfg.env}{' (DRY-RUN, sin cambios reales)' if cfg.dry_run else ''}</td></tr>
          <tr><td>Archivo</td><td>{e(r.file_name)}</td></tr>
          <tr><td>Bloqueos</td><td>{_counts(r, 'bloqueo')}</td></tr>
          <tr><td>Cierres</td><td>{_counts(r, 'cierre')}</td></tr>
          <tr><td>Total retirado</td><td>{clp(r.total_withdrawn)}</td></tr>
          <tr><td>run_id</td><td>{r.run_id}</td></tr>
        </table>
        {f'<p><b>Error general:</b> {e(r.fatal_error)}</p>' if r.fatal_error else ''}
        {''.join(f'<p><b>Advertencia:</b> {e(w)}</p>' for w in r.warnings)}
        {f'''<h4>Detalle por cuenta</h4>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><th>Fila</th><th>RUT</th><th>Gestión</th><th>accountId</th><th>Resultado</th><th>Retirado</th></tr>
          {rows}
        </table>
        <p>Detalle completo: <code>{cfg.results_table}</code></p>'''
        }
        </body></html>"""

    return subject, body


class Notifier:
    def __init__(self, cfg: Config, send_email: EmailSender, send_message: TeamsSender):
        self.cfg = cfg
        self._send_email = send_email
        self._send_message = send_message

    def _send_emails(self, recipients: tuple[str, ...], subject: str, body: str) -> None:
        for email in recipients:
            try:
                self._send_email(email, subject, body)
            except Exception as exc:
                log.error("Fallo al enviar correo a %s: %s", email, exc)

    def notify(self, report: RunReport) -> None:
        """Un fallo al notificar nunca interrumpe el proceso."""
        r, cfg = report, self.cfg
        subject, body = build_email(r, cfg)
        self._send_emails(cfg.email_recipients, subject, body)  # siempre

        if r.status == RunStatus.SUCCESS and r.total_withdrawn > 0 and not cfg.dry_run:
            try:
                self._send_emails(cfg.success_recipients, *build_success_email(r, cfg))
            except Exception as exc:
                log.error("Fallo al construir el correo de éxito: %s", exc)

        try:
            self._send_message(build_teams_message(r, cfg), cfg.teams_chat_id)
        except Exception as exc:
            log.error("Fallo al enviar notificación Teams: %s", exc)
