# core/odoo_importer.py
from __future__ import annotations

import hashlib
import os
import re
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from dotenv import load_dotenv

# ---------- Utilidades ----------
SKU_BRACKET_RE = re.compile(r"\[(.*?)\]")
SKU_LEADING_RE = re.compile(r"^([A-Za-z0-9\-\._]+)")
WHITES_RE = re.compile(r"\s+")


def _normalize_default_code(s: str) -> str:
    """
    Normaliza la referencia interna para homogeneizar:
    - Quita espacios repetidos
    - Mantiene mayúsculas/tildes
    - No elimina guiones ni puntos (muy usados en ref. proveedor)
    """
    if s is None:
        return ""
    s = str(s).strip()
    s = WHITES_RE.sub("", s)
    return s


def _parse_qty(val: str, default: float = 0.0) -> float:
    try:
        s = str(val).strip()
        if not s:
            return default
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and has_dot:
            if s.rfind(".") > s.rfind(","):
                s = s.replace(",", "")
            else:
                s = s.replace(".", "").replace(",", ".")
        elif has_comma and not has_dot:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default


def _parse_price(val: str, default: float = 0.0) -> float:
    try:
        s = str(val).strip()
        if not s:
            return default
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and has_dot:
            if s.rfind(".") > s.rfind(","):
                s = s.replace(",", "")
            else:
                s = s.replace(".", "").replace(",", ".")
        elif has_comma and not has_dot:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default


def _parse_disc(val: str, default: float = 0.0) -> float:
    try:
        s = str(val).strip()
        if not s:
            return default
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and has_dot:
            if s.rfind(".") > s.rfind(","):
                s = s.replace(",", "")
            else:
                s = s.replace(".", "").replace(",", ".")
        elif has_comma and not has_dot:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default


def _parse_discounts_chain(val: str) -> float:
    """
    Convierte una cadena de descuentos encadenados en un % efectivo.
    Ej.: "65 15" => 1 - (1-0.65)*(1-0.15) = 70.25
    Acepta separadores por espacio/coma y el símbolo %.
    Devuelve el porcentaje efectivo con 6 decimales.
    """
    s = str(val or "").strip()
    if not s:
        return 0.0
    # extrae números (enteros o decimales) y normaliza coma decimal
    tokens = re.findall(r"[-+]?\d+[.,]?\d*", s.replace("%", " "))
    eff_factor = 1.0
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        try:
            d = float(tok.replace(",", "."))
        except Exception:
            continue
        eff_factor *= 1.0 - d / 100.0
    eff_pct = (1.0 - eff_factor) * 100.0
    return round(eff_pct, 6)


def _extract_code(val: str) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _extract_sku(raw: str) -> str:
    """
    Obtiene el SKU 'puro' del campo Producto del CSV.
    Ej.: '[LP3260PASTILLAS...]' -> 'LP3260'
    """
    if not raw:
        return ""
    m = SKU_BRACKET_RE.search(raw)
    if m:
        inside = m.group(1).strip()
        mld = re.match(r"^([A-Za-z]{1,10}\d{2,15})", inside)
        if mld:
            return _normalize_default_code(mld.group(1))
        mtok = re.match(r"^([A-Za-z0-9]+)", inside)
        if mtok:
            return _normalize_default_code(mtok.group(1))
        return _normalize_default_code(inside)
    m2 = SKU_LEADING_RE.search(str(raw))
    if m2:
        return _normalize_default_code(m2.group(1))
    return _normalize_default_code(str(raw))


def _env() -> Dict[str, Any]:
    load_dotenv()
    return {
        "url": os.getenv("ODOO_URL", "").strip(),
        "db": os.getenv("ODOO_DB", "").strip(),
        "user": os.getenv("ODOO_USER", "").strip(),
        "password": os.getenv("ODOO_PASSWORD", "").strip(),
        "price_is_net": os.getenv("IMPORT_PRICE_IS_NET", "1").strip()
        in ("1", "true", "True"),
        "dedup_by_partner_ref": os.getenv("DEDUP_BY_PARTNER_REF", "1").strip()
        in ("1", "true", "True"),
        "force": os.getenv("FORCE_IMPORT", "0").strip() in ("1", "true", "True"),
    }


def _connect(e: Dict[str, Any]) -> Tuple[int, Any]:
    common = xmlrpc.client.ServerProxy(f"{e['url']}/xmlrpc/2/common")
    uid = common.authenticate(e["db"], e["user"], e["password"], {})
    if not uid:
        raise RuntimeError("No se pudo autenticar en Odoo. Revisa credenciales.")
    models = xmlrpc.client.ServerProxy(f"{e['url']}/xmlrpc/2/object")
    return uid, models


# ---------- Búsquedas ----------
def _find_partner(models, e, uid, name: str) -> Optional[int]:
    """Primero intenta como proveedor; si no, relaja el filtro."""
    if not name:
        return None
    domain_strict = [
        "&",
        "|",
        ("name", "ilike", name),
        ("display_name", "ilike", name),
        ("supplier_rank", ">", 0),
    ]
    ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "res.partner",
        "search",
        [domain_strict],
        {"limit": 1},
    )
    if ids:
        return ids[0]
    domain_relaxed = ["|", ("name", "ilike", name), ("display_name", "ilike", name)]
    ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "res.partner",
        "search",
        [domain_relaxed],
        {"limit": 1},
    )
    return ids[0] if ids else None


def _find_uom_ids(models, e, uid) -> Tuple[Optional[int], Optional[int]]:
    uom_ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "uom.uom",
        "search",
        [[("category_id.name", "=", "Unit")]],
        {"limit": 1},
    )
    uom_id = uom_ids[0] if uom_ids else None
    return uom_id, uom_id


def _find_product_by_default_code(
    models, e, uid, code: str
) -> Optional[Tuple[int, Optional[int]]]:
    if not code:
        return None
    tmpl_ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.template",
        "search",
        [[("default_code", "=", code)]],
        {"limit": 1},
    )
    if tmpl_ids:
        tmpl = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "product.template",
            "read",
            [tmpl_ids, ["uom_po_id"]],
        )[0]
        product_ids = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "product.product",
            "search",
            [[("product_tmpl_id", "=", tmpl_ids[0])]],
            {"limit": 1},
        )
        if product_ids:
            uom_po_id = tmpl["uom_po_id"][0] if tmpl.get("uom_po_id") else None
            return product_ids[0], uom_po_id
    return None


def _find_product_by_default_code_partial(
    models, e, uid, needles: List[str]
) -> Optional[Tuple[int, Optional[int]]]:
    """Búsqueda por coincidencia parcial en product.template.default_code.

    Estrategia:
      - Para cada "needle" (posibles códigos de proveedor), buscar templates cuyo default_code ILIKE %needle%.
      - Ranquear: mejor si default_code termina en needle; luego si lo contiene.
      - Empate: default_code más corto y write_date más reciente (según orden).
    Retorna (product_id, uom_po_id) si hay candidato.
    """
    # Normaliza agujas: quitar espacios, mayúsculas
    clean_needles: List[str] = []
    for n in needles or []:
        if not n:
            continue
        ns = str(n).strip()
        if not ns:
            continue
        clean_needles.append(ns.upper())
    if not clean_needles:
        return None

    best = (
        None  # (score, -len(default_code), write_date, tmpl_id, uom_po_id, product_id)
    )
    seen = set()

    def _score(dc: str, needle: str) -> int:
        sdc = (dc or "").upper()
        if not sdc or not needle:
            return 0
        if sdc == needle:
            return 100
        if sdc.endswith(needle):
            return 90
        if needle in sdc:
            return 80
        return 0

    for needle in clean_needles:
        try:
            tmpl_recs = models.execute_kw(
                e["db"],
                uid,
                e["password"],
                "product.template",
                "search_read",
                [[("default_code", "ilike", needle)]],
                {
                    "fields": ["id", "default_code", "uom_po_id", "write_date"],
                    "limit": 50,
                    "order": "write_date desc",
                },
            )
        except Exception:
            tmpl_recs = []
        for rec in tmpl_recs or []:
            tid = rec.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            dc = rec.get("default_code") or ""
            sc = _score(dc, needle)
            if sc <= 0:
                continue
            try:
                product_ids = models.execute_kw(
                    e["db"],
                    uid,
                    e["password"],
                    "product.product",
                    "search",
                    [[("product_tmpl_id", "=", tid)]],
                    {"limit": 1},
                )
            except Exception:
                product_ids = []
            if not product_ids:
                continue
            uom_po_id = rec["uom_po_id"][0] if rec.get("uom_po_id") else None
            key = (
                sc,
                -len(dc),
                rec.get("write_date") or "",
                tid,
                uom_po_id,
                product_ids[0],
            )
            if best is None or key > best:
                best = key

    if best:
        _, _, _, _tid, uom_po_id, product_id = best
        return product_id, uom_po_id
    return None


def _create_product(
    models, e, uid, default_code: str, name: str, supplier_id: int
) -> Tuple[int, Optional[int]]:
    uom_id, uom_po_id = _find_uom_ids(models, e, uid)
    tmpl_vals = {
        "name": name or default_code,
        "default_code": default_code,
        "type": "product",
        "purchase_ok": True,
        "sale_ok": True,
    }
    if uom_id:
        tmpl_vals["uom_id"] = uom_id
    if uom_po_id:
        tmpl_vals["uom_po_id"] = uom_po_id

    tmpl_id = models.execute_kw(
        e["db"], uid, e["password"], "product.template", "create", [tmpl_vals]
    )

    product_ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.product",
        "search",
        [[("product_tmpl_id", "=", tmpl_id)]],
        {"limit": 1},
    )
    product_id = product_ids[0] if product_ids else None

    try:
        models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "product.supplierinfo",
            "create",
            [
                {
                    "name": supplier_id,
                    "product_tmpl_id": tmpl_id,
                    "product_name": name or default_code,
                    "product_code": default_code,
                }
            ],
        )
    except Exception:
        pass

    return product_id, uom_po_id


def _find_incoming_picking(models, e, uid, entregar_a: str) -> Optional[int]:
    """
    Usa el picking de entrada del almacén indicado en "Entregar a".
    Si no se encuentra, hace fallback al primer picking de entrada disponible.
    """
    if entregar_a:
        wh_ids = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "stock.warehouse",
            "search",
            [[("name", "ilike", entregar_a)]],
            {"limit": 1},
        )
        if wh_ids:
            wh = models.execute_kw(
                e["db"],
                uid,
                e["password"],
                "stock.warehouse",
                "read",
                [wh_ids, ["in_type_id"]],
            )[0]
            in_type = wh.get("in_type_id")
            if in_type:
                return in_type[0]

        pt_ids = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "stock.picking.type",
            "search",
            [[("name", "ilike", entregar_a), ("code", "=", "incoming")]],
            {"limit": 1},
        )
        if pt_ids:
            return pt_ids[0]

    pt_ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "stock.picking.type",
        "search",
        [[("code", "=", "incoming")]],
        {"limit": 1},
    )
    return pt_ids[0] if pt_ids else None


# ---------- Hash de import ----------
def _compute_import_hash(
    csv_path: Path, partner_id: int, ref: str, df: pd.DataFrame
) -> str:
    h = hashlib.sha256()
    h.update(str(partner_id).encode())
    h.update((ref or "").encode())
    h.update(str(csv_path).encode())
    for _, row in df.iterrows():
        base = "|".join(
            [
                (row.get("Líneas del pedido/Producto") or "").strip(),
                (row.get("Líneas del pedido/Descripción") or "").strip(),
                (row.get("Líneas del pedido/Cantidad") or "").strip(),
                (row.get("Líneas del pedido/Precio unitario") or "").strip(),
                (row.get("Líneas del pedido/(%) Descuento") or "").strip(),
            ]
        )
        h.update(base.encode())
    return h.hexdigest()


# ---------- Import principal ----------
def import_csv(csv_path: str) -> None:
    e = _env()
    if not all([e["url"], e["db"], e["user"], e["password"]]):
        raise RuntimeError("Faltan ODOO_URL/DB/USER/PASSWORD en entorno.")

    uid, models = _connect(e)
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"No existe: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, sep=",")
    df.fillna("", inplace=True)

    needed = [
        "Proveedor",
        "Referencia de proveedor",
        "Entregar a",
        "Líneas del pedido/Producto",
        "Líneas del pedido/Descripción",
        "Líneas del pedido/Cantidad",
        "Líneas del pedido/Precio unitario",
        "Líneas del pedido/(%) Descuento",
    ]
    for col in needed:
        if col not in df.columns:
            print(f"[SKIPPED] CSV inválido: falta columna '{col}'")
            return

    proveedor = (df["Proveedor"].iloc[0] or "").strip()
    ref = (df["Referencia de proveedor"].iloc[0] or "").strip()
    entregar_a = (df["Entregar a"].iloc[0] or "").strip()

    partner_id = _find_partner(models, e, uid, proveedor)
    if not partner_id:
        print(f"[SKIPPED] Proveedor no encontrado: '{proveedor}'")
        return

    force = e["force"]
    if e["dedup_by_partner_ref"] and ref:
        exists = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "purchase.order",
            "search",
            [
                [
                    ("partner_ref", "=", ref),
                    ("partner_id", "=", partner_id),
                    ("state", "!=", "cancel"),
                ]
            ],
            {"limit": 1},
        )
        if exists and not force:
            print(f"[SKIPPED] Ya existe PO con partner_ref='{ref}' (id {exists[0]}).")
            return

    # Dedupe por hash de contenido
    try:
        import_hash = _compute_import_hash(csv_path, partner_id, ref, df)
        dup_by_hash = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "purchase.order",
            "search",
            [
                [
                    ("x_import_hash", "=", import_hash),
                    ("partner_id", "=", partner_id),
                    ("state", "!=", "cancel"),
                ]
            ],
            {"limit": 1},
        )
        if dup_by_hash and not force:
            print(
                f"[SKIPPED] Ya existe PO con el mismo x_import_hash (id {dup_by_hash[0]})."
            )
            return
        elif dup_by_hash:
            print(
                f"[WARN] Duplicado por x_import_hash (id {dup_by_hash[0]}). Continuo igualmente."
            )
    except Exception:
        import_hash = ""

    picking_type_id = _find_incoming_picking(models, e, uid, entregar_a)

    lines: List[Tuple[int, int, Dict[str, Any]]] = []
    missing_codes: set[str] = set()
    date_planned = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # CAMBIO: detectar si el pedido contiene al menos una línea con cantidad negativa (abono)
    is_refund_order = any(
        _parse_qty(x, 0.0) < 0 for x in df["Líneas del pedido/Cantidad"]
    )

    for _, row in df.iterrows():
        raw_prod = _extract_code(row["Líneas del pedido/Producto"])
        desc = _extract_code(row["Líneas del pedido/Descripción"])
        sku = _extract_sku(raw_prod)
        qty_raw = _parse_qty(row["Líneas del pedido/Cantidad"], 0.0)
        price = _parse_price(row["Líneas del pedido/Precio unitario"])
        # disc simple (por compatibilidad con CSV antiguos)
        disc = _parse_disc(row["Líneas del pedido/(%) Descuento"])
        # NUEVO: descuento efectivo por cadena (soporta "65 15", "65, 15%", etc.)
        eff_disc = _parse_discounts_chain(row["Líneas del pedido/(%) Descuento"])

        # aceptamos negativas; solo descartamos 0 o SKU vacío
        if not sku or qty_raw == 0.0:
            continue

        product_id = None
        uom_po_id: Optional[int] = None

        prod = _find_product_by_default_code(models, e, uid, sku)
        if prod:
            product_id, uom_po_id = prod
        else:
            alt_code = sku.replace(" ", "")
            prod = _find_product_by_default_code(models, e, uid, alt_code)
            if prod:
                product_id, uom_po_id = prod
            else:
                # Búsqueda por coincidencia parcial en default_code con agujas derivadas
                needles: List[str] = [sku]
                # Añadir tokens numéricos largos (≥5 dígitos) detectados en Producto/Descripción
                num_tokens: Set[str] = set()
                for src in (raw_prod, desc):
                    for m in re.findall(r"\d{5,}", str(src or "")):
                        num_tokens.add(m)
                needles.extend(sorted(num_tokens))
                prod = _find_product_by_default_code_partial(models, e, uid, needles)
                if prod:
                    product_id, uom_po_id = prod

        if not product_id:
            try:
                product_id, uom_po_id = _create_product(
                    models,
                    e,
                    uid,
                    default_code=sku,
                    name=(desc or sku).strip(),
                    supplier_id=partner_id,
                )
            except Exception as ex:
                print(f"[ERROR] Creando producto '{sku}': {ex}")
                missing_codes.add(sku)
                continue

        # --- CAMBIO DE LÓGICA DE PRECIOS/DTO ---
        # Antes: si IMPORT_PRICE_IS_NET==False, neteábamos price con 'disc'.
        # Ahora: Enviamos precio BRUTO y el % de descuento efectivo en 'discount'.
        # (Las líneas de abono mantienen cantidad negativa y price_unit positivo).
        is_refund_line = qty_raw < 0
        qty = qty_raw
        price_unit = abs(price)  # siempre bruto, positivo

        line_vals = {
            "name": desc or sku,
            "product_id": product_id,
            "product_qty": qty,
            "price_unit": price_unit,
            "date_planned": date_planned,
        }
        if uom_po_id:
            line_vals["product_uom"] = uom_po_id

        # si hay descuento efectivo, lo informamos
        if eff_disc > 0:
            line_vals["discount"] = eff_disc
        elif disc > 0:
            # fallback por compatibilidad (si no venía encadenado)
            line_vals["discount"] = disc

        lines.append((0, 0, line_vals))

    if not lines:
        print("[SKIPPED] Sin líneas válidas tras procesar CSV.")
        return

    # Crear la compra
    po_vals = {
        "partner_id": partner_id,
        "partner_ref": ref or "",
        "date_order": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "order_line": lines,
    }
    if picking_type_id:
        po_vals["picking_type_id"] = picking_type_id
    if import_hash:
        po_vals["x_import_hash"] = import_hash

    po_id = models.execute_kw(
        e["db"], uid, e["password"], "purchase.order", "create", [po_vals]
    )

    # (DESACTIVADO) Confirmación automática del pedido.
    # Dejamos el PO en estado borrador (presupuesto/RFQ) y NO llamamos a button_confirm.
    # try:
    #     models.execute_kw(
    #         e["db"], uid, e["password"], "purchase.order", "button_confirm", [[po_id]]
    #     )
    # except Exception:
    #     pass

    # Mantener marca informativa de abono si existen campos x_*
    if is_refund_order:
        try:
            models.execute_kw(
                e["db"],
                uid,
                e["password"],
                "purchase.order",
                "write",
                [[po_id], {"x_is_refund": True}],
            )
        except Exception:
            pass

    print(
        f"[CREATED] purchase.order id={po_id} partner_id={partner_id} ref='{ref}' source='{Path(csv_path).name}'"
    )


if __name__ == "__main__":
    import_csv(os.getenv("IMPORT_CSV_PATH", "data.csv"))
