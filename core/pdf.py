# core/pdf.py
from __future__ import annotations

from pathlib import Path

import pdfplumber


def _page_text(page) -> str:
    txt = page.extract_text() or ""
    if txt.strip():
        return txt
    # Fallback: si extract_text() devuelve None, ensamblar con extract_words()
    try:
        words = page.extract_words() or []
        # Agrupado simple por líneas usando la coordenada 'top'
        words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
        lines = []
        cur_top = None
        cur = []
        for w in words:
            top = round(w["top"])
            if cur_top is None or top == cur_top:
                cur.append(w["text"])
                cur_top = top if cur_top is None else cur_top
            else:
                lines.append(" ".join(cur))
                cur = [w["text"]]
                cur_top = top
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)
    except Exception:
        return ""


def extract_text(pdf_path: str | Path) -> str:
    """Texto plano de TODO el PDF (todas las páginas)."""
    pdf_path = Path(pdf_path)
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            parts.append(_page_text(page))
    return "\n".join(parts).strip()


def read_pdf_text(pdf_path: str | Path) -> str:
    """Alias usado por cli.py para detección de proveedor."""
    return extract_text(pdf_path)
