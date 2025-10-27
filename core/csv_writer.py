# core/csv_writer.py
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Union

import pandas as pd

from core.logger import get_logger


def _df_from_doc(doc: Union[pd.DataFrame, Any]) -> pd.DataFrame:
    if isinstance(doc, pd.DataFrame):
        return doc.copy()
    df = getattr(doc, "df", None)
    if df is None or not isinstance(df, pd.DataFrame):
        raise ValueError("El adapter no ha devuelto DataFrame ni ImportDoc válido (.df).")
    return df.copy()


def write_csv(doc: Union[pd.DataFrame, Any], csv_path: Union[str, Path]) -> None:
    log = get_logger()
    df = _df_from_doc(doc)

    head = getattr(doc, "HEAD", None)
    if not head:
        head_lower = getattr(doc, "head", None)
        if callable(head_lower):
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
        other_cols = [c for c in df.columns if c not in head]
        df = df[[*head, *other_cols]]

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    df.to_csv(
        csv_path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    log.info(f"[CSV] Escrito: {csv_path}")

