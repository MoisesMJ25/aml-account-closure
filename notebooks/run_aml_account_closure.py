# Databricks notebook source
# MAGIC %md
# MAGIC # aml-account-closure: bloqueo y cierre de cuentas AML
# MAGIC Notebook de entrada delgado. Lo dispara el *file arrival trigger* sobre la carpeta `A_Procesar` del Volume.
# MAGIC Solo resuelve las dependencias de Databricks (widgets, `%run` corporativo, `utils`, `dbutils`, `spark`)
# MAGIC y se las inyecta al paquete `aml_account_closure`, que contiene toda la lógica.

# COMMAND ----------

# _____Parámetros_____: los valores llegan como parámetros del job (variables del bundle).
dbutils.widgets.dropdown("env", "dev", ["dev", "prod"])
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])
dbutils.widgets.dropdown("force", "false", ["true", "false"])
dbutils.widgets.text("utils_dir", "")  # carpeta del módulo corporativo `utils` (vacío: ya está en sys.path)

from aml_account_closure.config import EnvSettings  # noqa: E402

for name in EnvSettings.param_names():
    dbutils.widgets.text(name, "")

ENV = dbutils.widgets.get("env")
DRY_RUN = dbutils.widgets.get("dry_run") == "true"
FORCE = dbutils.widgets.get("force") == "true"
print(f"env={ENV} | dry_run={DRY_RUN} | force={FORCE}")

# COMMAND ----------

# MAGIC %run /Workspace/Procesos_Automaticos/Funciones/Funciones_Conexion_MongoDB_MI

# COMMAND ----------

import sys

import pandas as pd

UTILS_DIR = dbutils.widgets.get("utils_dir").strip()
if UTILS_DIR and UTILS_DIR not in sys.path:
    sys.path.append(UTILS_DIR)

from utils import send_email, send_message

from aml_account_closure.application.run import raise_if_failed, summary_rows
from aml_account_closure.composition import main
from aml_account_closure.config import load_config

CFG = load_config(ENV, DRY_RUN, FORCE, EnvSettings.from_params(dbutils.widgets.get), secrets=dbutils.secrets)

reports = main(
    CFG,
    spark=spark,
    connect_mongo=conectar_mongo,
    disconnect_mongo=desconectar_mongo,
    send_email=send_email,
    send_message=send_message,
)

if reports:
    display(pd.DataFrame(summary_rows(reports)))

# Un run FAILED hace fallar el job (activa alertas nativas). PARTIAL no.
raise_if_failed(reports)
