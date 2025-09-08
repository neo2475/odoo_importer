# adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

import pandas as pd

# Columnas estándar que deben devolver TODOS los adaptadores (mantenido)
CSV_COLUMNS = [
    "Líneas del pedido/Producto",
    "Líneas del pedido/Descripción",
    "Líneas del pedido/Cantidad",
    "Líneas del pedido/Precio unitario",
    "Líneas del pedido/(%) Descuento",
]


@dataclass
class ImportDoc:
    """
    Documento intermedio unificado para TODOS los adapters.
    df   -> DataFrame listo para exportar a CSV (con CSV_COLUMNS).
    meta -> Diccionario con metadatos (proveedor, fecha, partner_ref, almacén, etc.).
    """

    df: pd.DataFrame
    meta: Dict[str, Any]


class BaseAdapter(ABC):
    key: str
    # Menor = más prioridad en detección. Por defecto 100 (mantenido)
    priority: int = 100

    @staticmethod
    @abstractmethod
    def detect(txt: str, filename: str) -> bool:
        """Debe devolver True si el adapter reconoce el PDF."""
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def parse(pdf_path: str) -> ImportDoc:
        """Debe devolver ImportDoc(df=<DataFrame con CSV_COLUMNS>, meta=<dict>)."""
        raise NotImplementedError
