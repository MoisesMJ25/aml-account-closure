# Observaciones de la migración

Todo lo que sigue se **detectó pero no se cambió**: el comportamiento es el del notebook `accounts_ops`.

## Posibles bugs o riesgos de negocio

1. **Un cierre fallido de una cuenta ENABLED la deja habilitada.** La compensación solo re-bloquea si el
   proceso desbloqueó la cuenta (`was_unblocked`). Si la cuenta estaba ENABLED y falla el retiro, las
   tarjetas o el cierre, queda ENABLED. Para un caso AML conviene evaluar bloquearla siempre ante una falla.
2. **Un archivo procesado en dry-run queda en `A_Procesar`.** El dry-run no mueve el archivo. Como
   `list_pending` procesa *todos* los pendientes, el próximo run real (disparado por otro archivo) también
   lo procesará **en serio**. Aplica a dev y a prod, porque comparten la carpeta.
3. **Dev y prod comparten `A_Procesar`.** Un run de dev con `dry_run=false` movería archivos destinados a
   prod. Por eso el trigger de dev queda pausado; además conviene no ejecutar dev con `dry_run=false`
   sobre esa carpeta.
4. **RUT con una cuenta activa y otras cerradas.** Solo se gestiona la activa: las tarjetas de las cuentas
   DELETED del mismo RUT no se bloquean (sí se bloquean cuando *todas* están cerradas).
5. **El retiro también se reintenta con HTTP 429.** Se asume que un 429 garantiza que el retiro no se
   aplicó. Conviene confirmarlo con el equipo del backoffice.
6. **Tarjetas BLOCKED.** Se envían igual a `PUT card/{id}` con `status=DISABLED` (solo se omiten las
   DISABLED). El docstring original decía "las BLOCKED/DISABLED se registran como omitidas", lo que no
   coincidía con el código. Se corrigió solo el docstring; el código no cambió.
7. **Choque de nombres al archivar.** El destino lleva marca de tiempo al segundo. Dos archivos con el
   mismo nombre archivados en el mismo segundo se sobrescribirían.
8. **Contadores del Teams y del correo.** "Bloqueos" y "Cierres" cuentan todos los casos de esa gestión,
   incluidos los fallidos. Las filas con gestión inválida no aparecen en ninguno de los dos.
9. **Doble espacio** antes de ` (DRY-RUN)` en el título del Teams. Se mantuvo.
10. **`processed_at`** se fija al crear el resultado, no al terminar la cuenta.
11. **Un archivo FAILED se puede reenviar sin `force`**: la detección de duplicados solo mira runs
    SUCCESS/PARTIAL. Parece intencional.

## Cambios de forma (sin efecto en el comportamiento)

- `CFG` global → `Config` inyectado. `handle_close` recibe `dry_run` por parámetro.
- `MONGO` pasó de dict a dataclass congelada (mismos valores). `WITHDRAW` se dividió en tres constantes.
- `StrEnum` para `Action`, `Outcome` y `RunStatus`: comparan igual que los strings. En Delta se escriben
  como `str` simple (`_plain`), para que Spark no reciba clases propias.
- `DryRunApi`: métodos explícitos en lugar de `__getattr__`, con la misma línea de log (para el tipado).
- `mongo_call` usa `functools.wraps`. El lambda con default de las tarjetas pasó a `functools.partial`.
- `datetime.UTC` en lugar de `timezone.utc` (es el mismo objeto).
- `time.sleep` de los reintentos pasa por `retry.sleep`, para que los tests no esperen.
- El logger se sigue llamando `accounts_ops` y el log local mantiene el mismo nombre de archivo.
- El ejemplo del docstring de `rut_variants` decía `11111111-6`, que tiene el DV incorrecto. Se cambió a
  `11111111-1` (solo el comentario).

## Datos sensibles que había en el notebook

No había credenciales en el código: la API key ya se leía con `dbutils.secrets` y Mongo usa
`conectar_mongo`. Sí estaban hardcodeados y ahora son variables:

| Antes (en el notebook) | Ahora |
|---|---|
| Correos personales en `email_recipients` / `success_recipients` | secrets `EMAIL_RECIPIENTS` / `SUCCESS_RECIPIENTS` |
| `operator_user` | secret `OPERATOR_USER` |
| Chats de Teams | secret `TEAMS_CHAT_ID` |
| Cuentas bancarias del correo de tesorería | secrets `TREASURY_SOURCE_ACCOUNT` / `TREASURY_TARGET_ACCOUNT` |
| URL del logo, URLs de la API, nombres de scopes, ruta del Volume, rutas de los JSON de dev | variables del Environment |

El chat de Teams de dev era un valor de relleno ("Falta Talento"). Si no existe, el envío falla y solo
queda en el log.

## Pendientes de infraestructura y operación

- **Spark/Delta no se prueba en CI** (`SparkDeltaStore`). Las filas que escribe sí se prueban (`report_rows`).
  Conviene hacer un smoke test en dev con `dry_run=true`.
- **`dynamic_version`** del artifact requiere una versión reciente del CLI de Databricks. Confirmar que
  `0.250.0` lo soporta o subir la versión.
- **El service principal** necesita:
  - permiso sobre el cluster existente (CAN_ATTACH_TO / CAN_RESTART), y que el modo de acceso del cluster
    permita ejecutar como SP (un cluster *single user* asignado a una persona no sirve);
  - lectura de `/Workspace/Procesos_Automaticos/Funciones/Funciones_Conexion_MongoDB_MI` y de la carpeta de `utils`;
  - el grupo de los secretos que use `conectar_mongo`.
- **El `%run` va literal** en el notebook: la magia `%run` no acepta variables.
- **Alertas nativas:** el bundle no define `email_notifications`. Si el job actual tiene alertas de falla,
  hay que agregarlas al job (en Databricks o como variable del bundle).
- **El `.keep` en `A_Procesar`** dispara el trigger una vez al aplicar Terraform. Es un run vacío:
  "No hay archivos pendientes".
- **Las actions** están fijadas por tag. Por seguridad conviene fijarlas por SHA.
- **Mypy en tests:** por simplicidad, mypy solo revisa `src/`.
