# core/csv_writer.py
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Union

import pandas as pd

from .logger import get_logger


def _df_from_doc(doc: Union[pd.DataFrame, Any]) -> pd.DataFrame:
    """
    Acepta un DataFrame o un objeto tipo ImportDoc y devuelve un DataFrame.
    - Si es DataFrame: se clona tal cual (sin forzar tipos).
    - Si es ImportDoc: intenta respetar su HEAD si existe.
    """
    if isinstance(doc, pd.DataFrame):
        return doc.copy()

    # Caso ImportDoc (o similar)
    head = getattr(doc, "HEAD", None)
    head_lower = getattr(doc, "head", None)
    # Evita confundir métodos tipo DataFrame.head con tu cabecera
    if callable(head_lower):
        head_lower = None
    head = head or head_lower

    rows = getattr(doc, "rows", None)
    if rows is None:
        raise ValueError(
            "write_csv: objeto sin 'rows' (no es DataFrame ni ImportDoc válido)"
        )

    df = pd.DataFrame(rows)

    if head:
        # Asegura columnas HEAD y orden
        for col in head:
            if col not in df.columns:
                df[col] = ""
        df = df[head]

    return df


def write_csv(doc: Union[pd.DataFrame, Any], csv_path: Union[str, Path]) -> None:
    """
    Escribe el CSV respetando el DataFrame tal cual:
    - No convierte vacíos a 0.0 (los deja como cadena vacía).
    - No reordena columnas salvo que venga un HEAD explícito
      (como atributo en el objeto o en df.attrs["HEAD"]).
    - No fuerza tipos numéricos.
    """
    log = get_logger()

    df = _df_from_doc(doc)

    # Si el objeto trae HEAD o el DataFrame trae df.attrs["HEAD"], respétalo
    head = getattr(doc, "HEAD", None)
    if not head:
        head_lower = getattr(doc, "head", None)
        if callable(head_lower):  # evita DataFrame.head
            head_lower = None
        head = head_lower
    if not head:
        try:
            head = df.attrs.get("HEAD")
        except Exception:
            head = None

    if head:
        for col in head:
            if col not in df.columns:
                df[col] = ""
        df = df[head]

    # Sustituye NaN por vacío y mantiene todo como str para no colar "0.0"
    df = df.fillna("").astype(str)

    # Asegura carpeta de salida
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Escritura sin tocar formatos
    df.to_csv(
        csv_path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )

    log.info(f"[CSV] Escrito: {csv_path}")
