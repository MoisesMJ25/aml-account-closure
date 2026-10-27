terraform {
  required_version = "~> 1.9.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "1.58.0"
    }
  }

  # Estado en Azure Storage. Configuración parcial: los valores van en backend.hcl (ver backend.hcl.example).
  #   terraform init -backend-config=backend.hcl
  backend "azurerm" {}
}
