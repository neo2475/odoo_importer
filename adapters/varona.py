from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, List

import pandas as pd
import pdfplumber

from . import register
from .base import BaseAdapter

# === Configuración y cabeceras ===

PROVEEDOR = "VARONA 2008, S.L."

# Mapeo de almacén textual -> nombre esperado en Odoo
ALMACEN_MAP = {
    "CENTRAL": "Central",
    "MIRALBAIDA": "Miralbaida",
    "AMARGACENA": "Amargacena",
}

# Números con coma (precio/cantidad), p. ej. "12,34", "-1,00"
NUM_RE = re.compile(r"-?\d+,\d{2}")


def leer_pdf(path: pathlib.Path):
    """Lee el PDF y devuelve las palabras extraídas de la primera página."""
    with pdfplumber.open(path) as pdf:
        return pdf.pages[0].extract_words()


def agrupar_filas(words, tol: float = 1.0):
    """Agrupa palabras que están en la misma línea, según coordenada vertical (top)."""
    words = sorted(words, key=lambda w: w["top"])
    filas, fila, y_ref = [], [], None
    for w in words:
        if y_ref is None or abs(w["top"] - y_ref) <= tol:
            fila.append(w)
            y_ref = y_ref or w["top"]
        else:
            filas.append(fila)
            fila, y_ref = [w], w["top"]
    if fila:
        filas.append(fila)
    return filas


def ref_albaran(txt: str) -> str:
    """Extrae la referencia del albarán (VA02xxxxx) a partir del texto."""
    m = re.search(r"VA02\s+(\d{2}\.\d{3})", txt)
    return "VA02" + m.group(1).replace(".", "") if m else ""


def detectar_almacen(txt: str) -> str:
    """Detecta el almacén de entrega buscando coincidencias."""
    # Detección directa: dirección Central
    if "Ctra. Aeropuerto, Km. 4" in txt:
        return "Central"
    # Por líneas que empiezan por el nombre del almacén
    destino = ""
    for linea in txt.splitlines():
        up = linea.strip().upper()
        for k, v in ALMACEN_MAP.items():
            if up.startswith(k):
                destino = v
    if destino:
        return destino
    # Por presencia en cualquier lugar del texto
    up = txt.upper()
    for k, v in ALMACEN_MAP.items():
        if k in up:
            return v
    return ""


def _join_hyphen_number_if_adjacent(row_words, number_token):
    """
    Si detecta un '-' inmediatamente antes de un número (pegado por layout), prepende el signo.
    """
    x = number_token["x0"]
    candidates = [w for w in row_words if w["text"] == "-" and 0 < (x - w["x0"]) <= 10]
    if candidates:
        return "-" + number_token["text"]
    return number_token["text"]


def parsear(words) -> List[Dict[str, Any]]:
    """Procesa las palabras extraídas del PDF para obtener líneas."""
    res = []
    for fila in agrupar_filas(words):
        fila = sorted(fila, key=lambda w: w["x0"])

        # Heurística para localizar el código: bloque de 5+ caracteres A-Z0-9 en x0 [30..80]
        code_i = next(
            (
                i
                for i, w in enumerate(fila)
                if 30 <= w["x0"] <= 80 and re.fullmatch(r"[0-9A-Z]{5,}", w["text"])
            ),
            None,
        )
        if code_i is None:
            continue

        # Detecta números (candidatos a qty / price / dto) con sus x0
        nums = [
            {"val": m.group(0), "x": w["x0"], "tok": w}
            for w in fila
            if (m := NUM_RE.match(w["text"]))
        ]
        if not nums:
            continue

        # Precio: suele ir a la derecha; si hay varios, el más a la derecha en [300..450], si no, el más a la derecha de todos
        cand_price = [n for n in nums if 300 <= n["x"] <= 450]
        price_tok = (
            max(cand_price, key=lambda n: n["x"])
            if cand_price
            else max(nums, key=lambda n: n["x"])
        )
        price = price_tok["val"].replace(",", ".")

        # Rango horizontal entre código y precio, para buscar qty y descripción
        left_x = fila[code_i]["x0"]
        right_x = price_tok["x"]

        # Cantidad: número intermedio entre código y precio; si no claro, primer número entre left_x y right_x
        qty_candidate = next((n for n in nums if 100 <= n["x"] <= 300), None)
        if qty_candidate is None:
            qty_candidate = next((n for n in nums if left_x < n["x"] < right_x), None)

        # A veces el '-' se pega a la última palabra de la descripción: "XXXX -1,00"
        trailing_neg_match = None
        tokens_between = [w for w in fila[code_i + 1 :] if w["x0"] < right_x]
        for w in tokens_between:
            m = re.match(r"^(.*?)-(\d+,\d{2})$", w["text"])
            if m:
                trailing_neg_match = m

        if qty_candidate:
            qty_txt = _join_hyphen_number_if_adjacent(fila, qty_candidate["tok"])
        elif trailing_neg_match:
            qty_txt = "-" + trailing_neg_match.group(2)
        else:
            qty_txt = "1,00"

        qty = qty_txt.replace(",", ".")

        # Descuentos: suelen ir más a la derecha (x ~ 450-520). Se combinan si hay dos (d1+d2-d1*d2/100).
        dto_vals: List[float] = []
        for n in nums:
            if 450 <= n["x"] <= 520:
                try:
                    v = float(n["val"].replace(",", "."))
                    if v <= 100:
                        dto_vals.append(v)
                except ValueError:
                    pass
        if len(dto_vals) == 0:
            dto = "0.00"
        elif len(dto_vals) == 1:
            dto = f"{dto_vals[0]:.2f}"
        else:
            d1, d2 = dto_vals[-2:]
            dto_eff = 100 * (1 - (1 - d1 / 100) * (1 - d2 / 100))
            dto = f"{dto_eff:.2f}"

        # Descripción = tokens entre código y precio, excluyendo tokens numéricos y el patrón "-\d+,\d{2}" final
        desc_parts: List[str] = []
        for w in tokens_between:
            t = w["text"]
            if t == "-" or NUM_RE.fullmatch(t):
                continue
            t = re.sub(r"-\d+,\d{2}$", "", t)
            if t:
                desc_parts.append(t)
        desc = " ".join(desc_parts).strip()

        # Código final: Varona antepone 3 chars (p.ej. "VA0"); se recortan si procede
        raw = fila[code_i]["text"]
        codigo = raw[3:] if len(raw) > 3 else raw

        res.append(
            {"cod": codigo, "desc": desc, "qty": qty, "price": price, "dto": dto}
        )
    return res


@register
class Varona(BaseAdapter):
    key = "varona"

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

    @staticmethod
    def detect(txt: str, filename: str) -> bool:
        return "VARONA 2008" in txt or bool(re.search(r"VA0\d+", filename.upper()))

    @staticmethod
    def parse(pdf_path: str) -> pd.DataFrame:
        pdfp = pathlib.Path(pdf_path)
        words = leer_pdf(pdfp)
        texto = "\n".join(w["text"] for w in words)

        filas = parsear(words)
        if not filas:
            raise ValueError("⚠️  No se encontraron líneas de artículo.")

        # Construye DataFrame de líneas
        df_lines = pd.DataFrame(
            {
                "Líneas del pedido/Producto": [
                    f"[{r['cod']}] {r['desc']}" for r in filas
                ],
                "Líneas del pedido/Descripción": [r["desc"] for r in filas],
                "Líneas del pedido/Cantidad": [r["qty"] for r in filas],
                "Líneas del pedido/Precio unitario": [r["price"] for r in filas],
                "Líneas del pedido/(%) Descuento": [r["dto"] for r in filas],
            }
        )

        # Línea extra de aportación si aparece el concepto en el PDF
        texto_lower = texto.casefold()
        if ("aportación al servicio de reparto" in texto_lower) or (
            "aportacion al servicio de reparto" in texto_lower
        ):
            df_lines = pd.concat(
                [
                    df_lines,
                    pd.DataFrame(
                        [
                            {
                                "Líneas del pedido/Producto": "APORTACION AL SERVICIO DE REPARTO",
                                "Líneas del pedido/Descripción": "APORTACION AL SERVICIO DE REPARTO",
                                "Líneas del pedido/Cantidad": "1.00",
                                "Líneas del pedido/Precio unitario": "2.67",
                                "Líneas del pedido/(%) Descuento": "0.00",
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

        # Completa cabeceras y valores de proveedor/ref/entregar_a
        partner_ref = ref_albaran(texto)
        almacen = detectar_almacen(texto)
        df = df_lines.copy()
        df["Proveedor"] = PROVEEDOR
        df["Referencia de proveedor"] = partner_ref
        df["Entregar a"] = almacen

        # Orden de columnas y relleno de faltantes
        for col in Varona.HEAD:
            if col not in df.columns:
                df[col] = ""

        df = df[Varona.HEAD]
        df.attrs["HEAD"] = Varona.HEAD
        return df
