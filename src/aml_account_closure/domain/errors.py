"""Taxonomía de errores: define si un paso se reintenta, se concilia o se da por fallido."""


class TransientError(Exception):
    """Reintentar es seguro (no se aplicó, o la operación es idempotente)."""


class AmbiguousError(Exception):
    """No se sabe si se aplicó (ej: timeout en un RETIRO). Nunca reintentar: conciliar."""


class BusinessError(Exception):
    """Dato o regla de negocio (4xx, RUT sin cuenta...). Reintentar no sirve."""


class InvalidFileError(Exception):
    """El Excel no se puede leer o no tiene la estructura esperada."""


class DuplicateFileError(Exception):
    """El mismo contenido (hash) ya se procesó en un run anterior."""


def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"
