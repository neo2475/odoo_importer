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

# Mapeo para identificar almacenes a partir del texto del PDF
ALMACEN_MAP = {
    "CENTRAL": "Central",
    "MIRALBAIDA": "Miralbaida",
    "AMARGACENA": "Amargacena",
}

# Regex numérico: acepta signo negativo, formato “-12,34”
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
    """
    Detecta el almacén de entrega buscando coincidencias con ALMACEN_MAP.
    Prioriza la última coincidencia encontrada. Incluye mapeo de dirección específica.
    """
    # Dirección específica → Central
    if "Ctra. Aeropuerto, Km. 4" in txt:
        return "Central"

    destino = ""
    for linea in txt.splitlines():
        up = linea.strip().upper()
        for k, v in ALMACEN_MAP.items():
            if up.startswith(k):
                destino = v  # Guarda última coincidencia
    if destino:
        return destino
    up = txt.upper()
    for k, v in ALMACEN_MAP.items():
        if k in up:
            return v
    return ""


def _join_hyphen_number_if_adjacent(row_words, number_token):
    """
    Si hay un '-' suelto a la izquierda del número (pegado visualmente),
    anteponer el signo a la cantidad.
    """
    x = number_token["x0"]
    candidates = [w for w in row_words if w["text"] == "-" and 0 < (x - w["x0"]) <= 10]
    if candidates:
        return "-" + number_token["text"]
    return number_token["text"]


def parsear(words) -> List[Dict[str, Any]]:
    """
    Procesa las palabras extraídas del PDF para obtener líneas:
    devuelve dicts con: cod, desc, qty, price, dto
    """
    res = []
    for fila in agrupar_filas(words):
        fila = sorted(fila, key=lambda w: w["x0"])

        # Código de artículo (x entre 30–80, alfanumérico ≥5)
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

        # Números “[-]nn,nn” con su X
        nums = [
            {"val": m.group(0), "x": w["x0"], "tok": w}
            for w in fila
            if (m := NUM_RE.match(w["text"]))
        ]
        if not nums:
            continue

        # Precio unitario (columna aprox. 300–450 pt)
        cand_price = [n for n in nums if 300 <= n["x"] <= 450]
        price_tok = (
            max(cand_price, key=lambda n: n["x"])
            if cand_price
            else max(nums, key=lambda n: n["x"])
        )
        price = price_tok["val"].replace(",", ".")

        # Región entre código y precio
        left_x = fila[code_i]["x0"]
        right_x = price_tok["x"]

        # Cantidad: buscar 100–300; si no, antes del precio
        qty_candidate = next((n for n in nums if 100 <= n["x"] <= 300), None)
        if qty_candidate is None:
            qty_candidate = next((n for n in nums if left_x < n["x"] < right_x), None)

        # Cantidad pegada al final de la descripción: “...TEXTO-1,00”
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

        # Descuentos en 450–520 pt (≤100%). Si hay dos → descuento efectivo.
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

        # Descripción entre código y precio (limpia números y “-d,dd” final)
        desc_parts: List[str] = []
        for w in tokens_between:
            t = w["text"]
            if t == "-" or NUM_RE.fullmatch(t):
                continue
            t = re.sub(r"-\d+,\d{2}$", "", t)  # recorta sufijo '-d,dd' si existe
            if t:
                desc_parts.append(t)
        desc = " ".join(desc_parts).strip()

        # Código sin los 3 primeros chars (p.ej., “185-4200348-5KG” → “4200348-5KG”)
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
        # Señales: texto del proveedor o patrón VA0xxxx en nombre
        return "VARONA 2008" in txt or bool(re.search(r"VA0\d+", filename.upper()))

    @staticmethod
    def parse(pdf_path: str) -> pd.DataFrame:
        # 1) Extraer palabras y texto lineal
        pdfp = pathlib.Path(pdf_path)
        words = leer_pdf(pdfp)
        texto = "\n".join(w["text"] for w in words)

        # 2) Parsear líneas
        filas = parsear(words)
        if not filas:
            raise ValueError("⚠️  No se encontraron líneas de artículo.")

        # 3) Construir DF de líneas (sin cabeceras globales aún)
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

        # 4) Línea opcional: Aportación al servicio de reparto (1 uds, 2.67€)
        t = texto.casefold()
        if ("aportación al servicio de reparto" in t) or (
            "aportacion al servicio de reparto" in t
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

        # 5) Cabecera: referencia y almacén
        partner_ref = ref_albaran(texto)
        almacen = detectar_almacen(texto)

        # 6) Añadir columnas globales y ordenar según HEAD
        df = df_lines.copy()
        df["Proveedor"] = PROVEEDOR
        df["Referencia de proveedor"] = partner_ref
        df["Entregar a"] = almacen

        # Asegurar todas las columnas HEAD y su orden
        for col in Varona.HEAD:
            if col not in df.columns:
                df[col] = ""

        df = df[Varona.HEAD]
        df.attrs["HEAD"] = Varona.HEAD  # para que el writer respete el orden
        return df
