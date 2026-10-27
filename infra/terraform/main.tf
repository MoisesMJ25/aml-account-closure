# Solo lo que el bundle no cubre. El job vive en databricks.yml y las tablas Delta las crea el código.

locals {
  volume_full_name = "${var.catalog_name}.${var.volume_schema_name}.${var.volume_name}"
  aml_dir          = "/Volumes/${var.catalog_name}/${var.volume_schema_name}/${var.volume_name}/${var.aml_folder}"

  # Procesados/Con_Error/Logs son HERMANAS de A_Procesar (no hijas): mover el archivo no re-dispara el trigger.
  folders = ["A_Procesar", "Procesados", "Con_Error", "Logs"]
}

# _____Volume_____ (existente y compartido: se importa, ver imports.tf)
resource "databricks_volume" "aml" {
  catalog_name     = var.catalog_name
  schema_name      = var.volume_schema_name
  name             = var.volume_name
  volume_type      = var.volume_type
  storage_location = var.volume_storage_location

  lifecycle {
    prevent_destroy = true
  }
}

# _____Carpetas_____
# Unity Catalog no tiene un recurso "carpeta": se crea con un archivo .keep. El proceso ignora los archivos
# que empiezan con "." y además crea Procesados/Con_Error/Logs si faltan.
resource "databricks_file" "folder_keep" {
  for_each       = toset(local.folders)
  path           = "${local.aml_dir}/${each.key}/.keep"
  content_base64 = base64encode("Carpeta gestionada por Terraform (aml-account-closure)\n")
}

# _____Grants_____
# databricks_grant (singular) NO es autoritativo: agrega permisos sin borrar los que ya existen en
# objetos compartidos. Unity Catalog otorga permisos a nivel de Volume, no por carpeta.

resource "databricks_grant" "catalog_compliance" {
  catalog    = var.catalog_name
  principal  = var.compliance_group_name
  privileges = ["USE_CATALOG"]
}

resource "databricks_grant" "catalog_job" {
  catalog    = var.catalog_name
  principal  = var.job_service_principal_application_id
  privileges = ["USE_CATALOG"]
}

resource "databricks_grant" "volume_schema_compliance" {
  schema     = "${var.catalog_name}.${var.volume_schema_name}"
  principal  = var.compliance_group_name
  privileges = ["USE_SCHEMA"]
}

resource "databricks_grant" "volume_schema_job" {
  schema     = "${var.catalog_name}.${var.volume_schema_name}"
  principal  = var.job_service_principal_application_id
  privileges = ["USE_SCHEMA"]
}

# Cumplimiento: deja el Excel en A_Procesar.
resource "databricks_grant" "volume_compliance" {
  volume     = databricks_volume.aml.id
  principal  = var.compliance_group_name
  privileges = ["READ_VOLUME", "WRITE_VOLUME"]
}

# Job: lee la entrada, mueve a Procesados/Con_Error y escribe Logs.
resource "databricks_grant" "volume_job" {
  volume     = databricks_volume.aml.id
  principal  = var.job_service_principal_application_id
  privileges = ["READ_VOLUME", "WRITE_VOLUME"]
}

# Job: crea (CREATE TABLE IF NOT EXISTS) y escribe (MERGE) sus tablas Delta.
resource "databricks_grant" "analytics_schema_job" {
  schema     = "${var.catalog_name}.${var.analytics_schema_name}"
  principal  = var.job_service_principal_application_id
  privileges = ["USE_SCHEMA", "CREATE_TABLE", "SELECT", "MODIFY"]
}

# _____Secret scopes_____ (solo existencia + lectura del job; nunca databricks_secret)
resource "databricks_secret_scope" "this" {
  for_each = var.secret_scopes
  name     = each.key

  dynamic "keyvault_metadata" {
    for_each = each.value.keyvault_resource_id == null ? [] : [each.value]
    content {
      resource_id = keyvault_metadata.value.keyvault_resource_id
      dns_name    = keyvault_metadata.value.keyvault_dns_name
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "databricks_secret_acl" "job_read" {
  for_each   = var.secret_scopes
  scope      = databricks_secret_scope.this[each.key].name
  principal  = var.job_service_principal_application_id
  permission = "READ"
}
