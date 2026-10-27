output "aml_dir" {
  description = "Valor para la variable aml_dir del bundle."
  value       = local.aml_dir
}

output "inbox_url" {
  description = "Carpeta vigilada por el file arrival trigger."
  value       = "${local.aml_dir}/A_Procesar/"
}
