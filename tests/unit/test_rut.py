"""Unitaria 1: normalización y validación de RUT."""

import pytest

from aml_account_closure.domain.rut import normalize_rut, rut_check_digit, rut_variants

# RUTs ficticios con DV calculado a mano (módulo 11).
RUT_DV_5 = "12345678-5"
RUT_DV_K = "1000005-K"
RUT_DV_0 = "1000013-0"


@pytest.mark.parametrize(
    ("body", "dv"),
    [("12345678", "5"), ("1000005", "K"), ("1000013", "0"), ("11111111", "1")],
)
def test_check_digit_modulo_11(body: str, dv: str) -> None:
    assert rut_check_digit(body) == dv


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.345.678-5", RUT_DV_5),  # puntos y guion
        ("12345678-5", RUT_DV_5),
        ("123456785", RUT_DV_5),  # sin guion
        ("0012.345.678-5", RUT_DV_5),  # ceros a la izquierda
        ("  12345678-5  ", RUT_DV_5),
        ("123456785.0", RUT_DV_5),  # Excel lo guardó como número
        ("1.000.005-K", RUT_DV_K),
        ("1000005-k", RUT_DV_K),  # DV en minúscula
        ("1000005k", RUT_DV_K),
        ("1.000.013-0", RUT_DV_0),
    ],
)
def test_normalize_valid_formats(raw: str, expected: str) -> None:
    assert normalize_rut(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "-", "abc", "1234567890-1"])
def test_normalize_rejects_invalid_format(raw: str) -> None:
    with pytest.raises(ValueError, match="formato inválido"):
        normalize_rut(raw)


@pytest.mark.parametrize("raw", ["12345678-9", "1000005-1", "123456780"])
def test_normalize_rejects_wrong_check_digit(raw: str) -> None:
    with pytest.raises(ValueError, match="Dígito verificador inválido"):
        normalize_rut(raw)


def test_variants_add_lowercase_k_only_when_dv_is_k() -> None:
    assert rut_variants(RUT_DV_K) == ["1000005-K", "1000005-k"]
    assert rut_variants(RUT_DV_5) == [RUT_DV_5]
