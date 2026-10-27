"""RUT chileno: dígito verificador (módulo 11), normalización y variantes de búsqueda."""

import re


def rut_check_digit(body: str) -> str:
    """Calcula el dígito verificador del cuerpo del RUT."""
    total, factor = 0, 2
    for d in reversed(body):
        total += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    rest = 11 - total % 11
    return {11: "0", 10: "K"}.get(rest, str(rest))


def normalize_rut(raw: str) -> str:
    """Devuelve el RUT como "12345678-5". Lanza ValueError si el formato o el DV no son válidos."""
    text = (raw or "").strip()
    if re.fullmatch(r"\d+\.0", text):  # Excel lo guardó como número
        text = text[:-2]
    cleaned = re.sub(r"[^0-9kK]", "", text).upper()
    body, dv = cleaned[:-1].lstrip("0"), cleaned[-1:]
    if not body.isdigit() or len(body) > 9:
        raise ValueError(f"RUT con formato inválido: {raw!r}")
    if rut_check_digit(body) != dv:
        raise ValueError(f"Dígito verificador inválido: {raw!r}")
    return f"{body}-{dv}"


def rut_variants(rut: str) -> list[str]:
    """metadata.rut se guarda como "11111111-1" (igual al formato normalizado).
    Solo se agrega la variante con 'k' minúscula por si algún documento la trae así."""
    return [rut, rut.lower()] if rut.endswith("K") else [rut]
