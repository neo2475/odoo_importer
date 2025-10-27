from __future__ import annotations

import pathlib
import re
import unicodedata
from typing import Dict, List

import pandas as pd
import pdfplumber

from . import register
from .base import BaseAdapter

PROVEEDOR = "GRUPO PEÑA AUTOMOCION, S.L."
NUM_RE_2_4 = re.compile(r"-?\d+[.,]\d{2,4}")

# Cabeceras del CSV de salida (orden exacto)
HEAD = [
    "Proveedor",
    "Referencia de proveedor",
    "Líneas del pedido/Producto",
    "Líneas del pedido/Descripción",
    "Líneas del pedido/Cantidad",
    "Líneas del pedido/Precio unitario",
    "Líneas del pedido/(%) Descuento",
    "Entregar a",
]


def _leer_palabras(path: pathlib.Path):
    with pdfplumber.open(path) as pdf:
        return pdf.pages[0].extract_words()


def _agrupar_por_filas(words: List[dict], tol: float = 1.2) -> List[List[dict]]:
    words = sorted(words, key=lambda w: w["top"])
    filas, fila, y_ref = [], [], None
    for w in words:
        if y_ref is None or abs(w["top"] - y_ref) <= tol:
            fila.append(w)
            if y_ref is None:
                y_ref = w["top"]
        else:
            filas.append(sorted(fila, key=lambda w: w["x0"]))
            fila, y_ref = [w], w["top"]
    if fila:
        filas.append(sorted(fila, key=lambda w: w["x0"]))
    return filas


def _es_ref(t: str) -> bool:
    return (
        bool(re.fullmatch(r"[A-Z0-9]{5,}", t))
        and any(c.isalpha() for c in t)
        and any(c.isdigit() for c in t)
    )


def _ref_albaran(words: List[dict]) -> str:
    # 1) Buscar palabra 'albar' y en la vecindad un token tipo ref
    idxs = [i for i, w in enumerate(words) if w["text"].lower().startswith("albar")]
    for i in idxs:
        for j in range(max(0, i - 5), min(len(words), i + 6)):
            t = words[j]["text"]
            if _es_ref(t):
                return t
    # 2) Fallback: tokens con asterisco final
    for w in words:
        if "*" in w["text"]:
            s = w["text"].strip("*")
            m = re.search(r"([A-Z0-9]{5,})$", s)
            if m and _es_ref(m.group(1)):
                return re.sub(r"^\d+", "", m.group(1))
    return ""


def _detectar_destino(texto: str) -> str:
    t = texto.replace("\n", " ")
    if "Ctra. Aeropuerto, Km. 4" in t:
        return "Central"
    if "Calle Ingeniero Ribera" in t:
        return "Amargacena"
    if "MIRALBAIDA" in t:
        return "Miralbaida"
    return ""


def _parsear_lineas(words: List[dict]) -> List[Dict[str, str]]:
    lineas = []
    for fila in _agrupar_por_filas(words):
        # Código con patrón AAA-XXXX... en x0 ~ [30..120]
        code_tok = next(
            (
                w
                for w in fila
                if 30 <= w["x0"] <= 120
                and re.fullmatch(r"[0-9A-Za-z]{3}-[0-9A-Za-z\-]+", w["text"])
            ),
            None,
        )
        if not code_tok:
            continue

        # Descripción entre x0 [130..300]
        desc = " ".join(w["text"] for w in fila if 130 <= w["x0"] < 300).strip()

        # Cantidad en [300..360]
        qty_tok = next(
            (
                w
                for w in fila
                if 300 <= w["x0"] <= 360 and NUM_RE_2_4.fullmatch(w["text"])
            ),
            None,
        )
        if not qty_tok:
            continue
        qty = qty_tok["text"].replace(",", ".")

        # Precio en [470..510]; si hay '-', precio 0; si no lo vemos, el número más a la derecha
        price_tok = next(
            (
                w
                for w in fila
                if 470 <= w["x0"] <= 510
                and (NUM_RE_2_4.fullmatch(w["text"]) or w["text"] == "-")
            ),
            None,
        )
        if price_tok:
            price = (
                "0" if price_tok["text"] == "-" else price_tok["text"].replace(",", ".")
            )
        else:
            candidatos = [w for w in fila if NUM_RE_2_4.fullmatch(w["text"])]
            if candidatos:
                price = max(candidatos, key=lambda w: w["x0"])["text"].replace(",", ".")
            elif any(470 <= w["x0"] <= 510 and w["text"] == "-" for w in fila):
                price = "0"
            else:
                continue

        # Código final: quitar prefijo "AAA-"
        raw = code_tok["text"]
        codigo = raw.split("-", 1)[1] if "-" in raw else raw

        lineas.append({"cod": codigo, "desc": desc, "qty": qty, "price": price})
    return lineas


def _contiene_aportacion(texto: str) -> bool:
    t = unicodedata.normalize("NFD", texto)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").lower()
    return "aportacion al servicio de reparto" in t


@register
class GrupoPenaAdapter(BaseAdapter):
    key = "grupo_pena"
    HEAD = HEAD

    @staticmethod
    def detect(txt: str, filename: str) -> bool:
        # 1) Atajo por nombre de archivo
        fn = filename.upper()
        if fn.startswith("GPA_") or "GPA-" in fn:
            return True
        # 2) Por contenido (normalizando tildes)
        t = unicodedata.normalize("NFD", txt)
        t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").upper()
        return (
            ("GRUPO PENA" in t)
            or ("GP AUTOMOCION" in t)
            or ("GRUPO PEÑA" in txt.upper())
            or ("GP AUTOMOCIÓN" in txt.upper())
        )

    @staticmethod
    def parse(pdf_path: str) -> pd.DataFrame:
        pdfp = pathlib.Path(pdf_path)
        palabras = _leer_palabras(pdfp)
        texto = " ".join(w["text"] for w in palabras)

        ref = _ref_albaran(palabras)
        destino = _detectar_destino(texto)
        filas = _parsear_lineas(palabras)

        if not filas:
            raise ValueError("⚠️  No se encontraron líneas de artículo.")

        # Construye DataFrame con las líneas detectadas
        df_lines = pd.DataFrame(
            {
                "Líneas del pedido/Producto": [
                    f"[{r['cod']}] {r['desc']}" for r in filas
                ],
                "Líneas del pedido/Descripción": [r["desc"] for r in filas],
                "Líneas del pedido/Cantidad": [str(r["qty"]) for r in filas],
                "Líneas del pedido/Precio unitario": [str(r["price"]) for r in filas],
                "Líneas del pedido/(%) Descuento": ["0.00"] * len(filas),
            }
        )

        # Línea extra de "aportación al servicio de reparto" si aparece
        if _contiene_aportacion(texto):
            df_lines = pd.concat(
                [
                    df_lines,
                    pd.DataFrame(
                        [
                            {
                                "Líneas del pedido/Producto": "APORTACION AL SERVICIO DE REPARTO",
                                "Líneas del pedido/Descripción": "APORTACION AL SERVICIO DE REPARTO",
                                "Líneas del pedido/Cantidad": "1",
                                "Líneas del pedido/Precio unitario": "2.67",
                                "Líneas del pedido/(%) Descuento": "0.00",
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

        # Completa cabeceras y valores de proveedor/ref/entregar_a
        df = df_lines.copy()
        df["Proveedor"] = PROVEEDOR
        df["Referencia de proveedor"] = ref
        df["Entregar a"] = destino

        # Orden de columnas y relleno de faltantes
        for col in HEAD:
            if col not in df.columns:
                df[col] = ""
        df = df[HEAD]
        df.attrs["HEAD"] = HEAD
        return df
