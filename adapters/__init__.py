# adapters/__init__.py
from __future__ import annotations

from typing import Dict, List, Optional, Type

from .base import BaseAdapter

# Registro global de adapters
registry: Dict[str, Type[BaseAdapter]] = {}


def register(cls: Type[BaseAdapter]) -> Type[BaseAdapter]:
    """Decorador para registrar adapters usando su atributo 'key'."""
    key = getattr(cls, "key", None)
    if not key or not isinstance(key, str):
        raise ValueError(f"{cls.__name__} debe definir 'key' str")
    if key in registry:
        raise ValueError(f"Adapter duplicado para key='{key}'")
    registry[key] = cls
    return cls


# --- Funciones solicitadas por tu CLI ---
def get_adapter(key: str) -> Type[BaseAdapter]:
    try:
        return registry[key]
    except KeyError as e:
        raise KeyError(
            f"No existe adapter para key='{key}'. Registrados: {list(registry.keys())}"
        ) from e


def detect_provider(txt: str, filename: str) -> Optional[str]:
    """
    Devuelve la 'key' del adapter que detecta el documento.
    Si hay 0 o >1 coincidencias, devuelve None (desconocido/ambiguo).
    """
    hits: List[str] = []
    for key, Adapter in registry.items():
        try:
            if Adapter.detect(txt, filename):
                hits.append(key)
        except Exception:
            # No tirar el proceso si un detect() falla
            continue
    if len(hits) == 1:
        return hits[0]
    return None


def detect_provider_from_path(pdf_path: str) -> Optional[str]:
    """Atajo: extrae el texto del PDF y aplica detect_provider."""
    from pathlib import Path

    from core.pdf import extract_text

    p = Path(pdf_path)
    txt = extract_text(str(p))
    return detect_provider(txt, p.name)


# Importa los módulos concretos para que se auto-registren con @register
from . import (
    gpautomocion,  # noqa: F401  (key suele ser 'grupo_pena')
    michelin,  # noqa: F401
    varona,  # noqa: F401
)

__all__ = [
    "registry",
    "register",
    "get_adapter",
    "detect_provider",
    "detect_provider_from_path",
]
