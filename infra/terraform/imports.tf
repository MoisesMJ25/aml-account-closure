# El Volume y los secret scopes ya existen y los usan otros procesos: se adoptan con import en vez de crearlos.
# Si alguno no existiera todavía, elimina su bloque import y Terraform lo creará.

import {
  to = databricks_volume.aml
  id = local.volume_full_name
}

import {
  for_each = var.secret_scopes
  to       = databricks_secret_scope.this[each.key]
  id       = each.key
}
