from __future__ import annotations

import re
from typing import Dict, List

import pandas as pd

from core.pdf import extract_text

from . import register
from .base import BaseAdapter

try:
    import pdfplumber  # type: ignore

    _HAS_PDFPLUMBER = True
except Exception:
    _HAS_PDFPLUMBER = False


@register
class Michelin(BaseAdapter):
    key = "michelin"

    SUPPLIER_NAME = "MICHELIN ESPAÑA PORTUGAL, S.A."
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
    ALMACEN_MAP: Dict[str, str] = {
        "H0064309": "Central",
        "H0064310": "Miralbaida",
        "H0123390": "Amargacena",
    }

    # ---------- Detección ----------
    @staticmethod
    def detect(txt: str, filename: str) -> bool:
        t = txt.upper()
        return "MICHELIN" in t and ("ENTREGAS DIARIAS" in t or "CAI :" in t)

    # ---------- Cabeceras texto ----------
    @classmethod
    def _extract_entregar_a(cls, txt: str) -> str:
        m = re.search(
            r"ENTREGAS\s+DIARIAS.*?\n\s*(H\d{7})", txt, flags=re.IGNORECASE | re.DOTALL
        )
        code = m.group(1).strip() if m else ""
        if not code:
            m2 = re.search(r"\b(H\d{7})\b", txt)
            code = m2.group(1).strip() if m2 else ""
        return cls.ALMACEN_MAP.get(code, code)

    @staticmethod
    def _extract_ref_albaran(txt: str) -> str:
        m = re.search(
            r"N\W*de\W*albar[aá]n\s*\n\s*([A-Z0-9\-\/]+)", txt, flags=re.IGNORECASE
        )
        if m:
            return m.group(1).strip()
        m2 = re.search(r"\b1[A-Z0-9]{7,8}\b", txt)
        return m2.group(0) if m2 else ""

    # ---------- Utilidades ----------
    @staticmethod
    def _norm_ws(s: str) -> str:
        if s is None:
            return ""
        s = str(s).replace("\u00a0", " ")
        s = re.sub(r"[ \t]+", " ", s)
        return s.strip()

    @staticmethod
    def _looks_like_tyre(line: str) -> bool:
        return bool(
            re.search(r"\b\d{3}/\d{2}\s*(?:R|ZR)?\s*\d{2}\b", line, flags=re.IGNORECASE)
            or re.search(r"\bTL\b", line)
            or re.search(
                r"\b(PILOT|ENERGY|PRIMACY|ALPIN|CROSSCLIMATE)\b",
                line,
                flags=re.IGNORECASE,
            )
        )

    # ---------- Parser texto (fallback) ----------
    @classmethod
    def _extract_items_from_text(cls, txt: str) -> List[dict]:
        items: List[dict] = []

        # Caso A: CANTIDAD\n<qty>\n<desc> ... CAI : <cai>
        pat1 = re.compile(
            r"CANTIDAD\s*\n\s*(?P<qty>\d+(?:[.,]\d+)?)\s*\n\s*(?P<desc>.+?)\s*CAI\s*:\s*(?P<cai>[A-Z0-9\-\./_]+)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        for m in pat1.finditer(txt):
            qty = m.group("qty").strip()
            desc = cls._norm_ws(m.group("desc").splitlines()[0])
            cai = m.group("cai").strip()
            items.append(
                {
                    "Líneas del pedido/Producto": cai,
                    "Líneas del pedido/Descripción": desc,
                    "Líneas del pedido/Cantidad": qty,
                }
            )

        # Caso B: genérico → por cada CAI mirar hacia atrás cantidad y desc
        for mc in re.finditer(r"CAI\s*:\s*([A-Z0-9\-\./_]+)", txt, flags=re.IGNORECASE):
            cai = mc.group(1).strip()
            window = txt[max(0, mc.start() - 1000) : mc.start()]
            raw_lines = window.splitlines()
            lines = [cls._norm_ws(ln) for ln in raw_lines if cls._norm_ws(ln)]
            try:
                head_idx = max(
                    i for i, ln in enumerate(lines) if "CANTIDAD" in ln.upper()
                )
            except ValueError:
                head_idx = 0

            qty = "1"
            for ln in reversed(lines[head_idx:]):
                if re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", ln):
                    continue
                mqty = re.search(r"\b(\d{1,3})\b", ln)
                if mqty:
                    qty = mqty.group(1)
                    break

            desc = "SIN DESCRIPCIÓN"
            skip = {"MI", "MICHELIN", "LP", "MARCA", "CAR"}
            for ln in reversed(lines[head_idx:]):
                if ln.upper() in skip or len(ln) <= 2:
                    continue
                if cls._looks_like_tyre(ln):
                    desc = ln
                    break
            if desc == "SIN DESCRIPCIÓN":
                for ln in reversed(lines[head_idx:]):
                    if ln.upper() not in skip and len(ln) > 2:
                        desc = ln
                        break

            items.append(
                {
                    "Líneas del pedido/Producto": cai,
                    "Líneas del pedido/Descripción": desc,
                    "Líneas del pedido/Cantidad": qty,
                }
            )
        return items

    # ---------- pdfplumber: CAI → Cantidad con chars (soporta “C a n t i d a d”) ----------
    @classmethod
    def _quantities_with_pdfplumber(cls, pdf_path: str) -> Dict[str, str]:
        if not _HAS_PDFPLUMBER:
            return {}

        qty_by_cai: Dict[str, str] = {}

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # 1) Palabras por línea (para localizar header y líneas CAI, aunque estén espaciadas)
                words = (
                    page.extract_words(
                        keep_blank_chars=False,
                        use_text_flow=True,
                        extra_attrs=["x0", "x1", "top", "bottom"],
                    )
                    or []
                )
                lines: Dict[int, List[dict]] = {}
                for w in words:
                    y = int(round(w["top"]))
                    lines.setdefault(y, []).append(w)
                for y in lines:
                    lines[y].sort(key=lambda w: w["x0"])

                # 1.a) Header “Cantidad” aunque venga letra a letra
                header = None
                for y, toks in lines.items():
                    seq = [t["text"] for t in toks]
                    for i in range(len(seq) - 7):
                        if "".join(seq[i : i + 8]).lower() == "cantidad":
                            header = {
                                "y": y,
                                "x0": toks[i]["x0"],
                                "x1": toks[i + 7]["x1"],
                                "top": min(t["top"] for t in toks),
                                "bottom": max(t["bottom"] for t in toks),
                            }
                            break
                    if header:
                        break
                if not header:
                    # fallback: palabra completa
                    for w in words:
                        if w["text"].strip().lower() == "cantidad":
                            header = {
                                "y": int(round(w["top"])),
                                "x0": w["x0"],
                                "x1": w["x1"],
                                "top": w["top"],
                                "bottom": w["bottom"],
                            }
                            break
                if not header:
                    continue  # sin header no podemos inferir la banda con fiabilidad

                xcenter = (header["x0"] + header["x1"]) / 2.0
                band_x0 = xcenter - 120.0
                band_x1 = xcenter + 120.0

                # 1.b) Localiza líneas que contengan “CAI” (compactando espacios)
                cai_lines: List[int] = []
                line_text_map: Dict[int, str] = {}
                for y, toks in lines.items():
                    text = " ".join(t["text"] for t in toks)
                    compact = text.replace(" ", "")
                    if "CAI" in text or "C A I" in text or "CAI" in compact:
                        cai_lines.append(y)
                        line_text_map[y] = compact  # ej. "CAI:214522"
                cai_lines.sort()
                if not cai_lines:
                    continue

                # 2) Construye CAI → qty usando chars (no words)
                chars = page.chars  # lista de caracteres con x/y
                # Agrupa dígitos por línea (y redondeado) dentro de la banda de cantidad
                digits_by_y: Dict[int, List[dict]] = {}
                for c in chars:
                    y = int(round(c["top"]))
                    if (
                        c["x0"] >= band_x0
                        and c["x1"] <= band_x1
                        and c["text"].isdigit()
                    ):
                        digits_by_y.setdefault(y, []).append(c)
                for y in digits_by_y:
                    digits_by_y[y].sort(key=lambda c: c["x0"])

                for y_cai in cai_lines:
                    # CAI code de la propia línea
                    compact = line_text_map.get(y_cai, "")
                    mcode = re.search(
                        r"CAI[:\s]*([A-Z0-9\-\/._]+)", compact, re.IGNORECASE
                    )
                    if not mcode:
                        continue
                    cai = mcode.group(1)

                    # Busca la línea de dígitos más cercana por encima del CAI y por debajo del header
                    candidates = [
                        yy for yy in digits_by_y.keys() if header["bottom"] < yy < y_cai
                    ]
                    if not candidates:
                        continue
                    yy = max(candidates)  # la más cercana al CAI
                    num = "".join(ch["text"] for ch in digits_by_y[yy])
                    mqty = re.search(r"(\d{1,3})$", num)
                    if mqty:
                        qty_by_cai[cai] = mqty.group(1)

        return qty_by_cai

    # ---------- Entrada principal ----------
    @classmethod
    def parse(cls, pdf_path: str) -> pd.DataFrame:
        txt = extract_text(pdf_path)
        entregar_a = cls._extract_entregar_a(txt)
        ref_albaran = cls._extract_ref_albaran(txt)

        # 1) Ítems base por texto (CAI + descripción). Cantidad se corrige después.
        items = cls._extract_items_from_text(txt)

        # 2) Mejorar cantidades con pdfplumber (soporta headers/valores “espaciados”)
        try:
            qty_map = cls._quantities_with_pdfplumber(pdf_path)
        except Exception:
            qty_map = {}
        if qty_map:
            for it in items:
                cai = it.get("Líneas del pedido/Producto", "")
                if cai in qty_map:
                    it["Líneas del pedido/Cantidad"] = str(qty_map[cai])

        # 3) Fallback si no hay líneas
        if not items:
            items = [
                {
                    "Líneas del pedido/Producto": "",
                    "Líneas del pedido/Descripción": "NO SE DETECTARON LÍNEAS",
                    "Líneas del pedido/Cantidad": "1",
                }
            ]

        # 4) DF final
        rows: List[dict] = []
        for it in items:
            rows.append(
                {
                    "Proveedor": cls.SUPPLIER_NAME,
                    "Referencia de proveedor": ref_albaran,
                    "Líneas del pedido/Producto": it["Líneas del pedido/Producto"],
                    "Líneas del pedido/Descripción": it[
                        "Líneas del pedido/Descripción"
                    ],
                    "Líneas del pedido/Cantidad": it["Líneas del pedido/Cantidad"],
                    "Líneas del pedido/Precio unitario": "",
                    "Líneas del pedido/(%) Descuento": "",
                    "Entregar a": entregar_a,
                }
            )

        df = pd.DataFrame(rows)
        for col in cls.HEAD:
            if col not in df.columns:
                df[col] = ""
        df = df[cls.HEAD]
        df.attrs["HEAD"] = cls.HEAD
        return df
