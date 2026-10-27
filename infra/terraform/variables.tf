variable "catalog_name" {
  description = "Catálogo de Unity Catalog del Volume y de las tablas."
  type        = string
}

variable "volume_schema_name" {
  description = "Schema que contiene el Volume."
  type        = string
}

variable "volume_name" {
  description = "Nombre del Volume."
  type        = string
}

variable "volume_type" {
  description = "MANAGED o EXTERNAL (debe coincidir con el Volume existente)."
  type        = string
  default     = "MANAGED"
}

variable "volume_storage_location" {
  description = "Solo para volúmenes EXTERNAL."
  type        = string
  default     = null
}

variable "aml_folder" {
  description = "Carpeta AML dentro del Volume (contiene A_Procesar, Procesados, Con_Error y Logs)."
  type        = string
}

variable "analytics_schema_name" {
  description = "Schema de las tablas Delta propias (las crea el código con su DDL)."
  type        = string
}

variable "compliance_group_name" {
  description = "Grupo de Cumplimiento que deja los Excel en A_Procesar."
  type        = string
}

variable "job_service_principal_application_id" {
  description = "Application ID del service principal que ejecuta el job."
  type        = string
}

variable "secret_scopes" {
  description = <<-EOT
    Secret scopes que usa el proceso (API key y Mongo de dev y prod). Solo se gestiona su existencia
    y el permiso de lectura del job; los valores se cargan fuera de Terraform.
    Para scopes respaldados por Azure Key Vault se informa keyvault_resource_id y keyvault_dns_name.
  EOT
  type = map(object({
    keyvault_resource_id = optional(string)
    keyvault_dns_name    = optional(string)
  }))
}
