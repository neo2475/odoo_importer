# core/odoo_importer.py
from __future__ import annotations

import math
import os
import re
import xmlrpc.client
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv


def _env():
    load_dotenv()
    return {
        "url": os.getenv("ODOO_URL", "").rstrip("/"),
        "db": os.getenv("ODOO_DB", ""),
        "user": os.getenv("ODOO_USER", ""),
        "password": os.getenv("ODOO_PASSWORD", ""),
        "company_id": int(os.getenv("ODOO_COMPANY_ID", "1")),
    }


def _connect(e):
    common = xmlrpc.client.ServerProxy(f"{e['url']}/xmlrpc/2/common")
    uid = common.authenticate(e["db"], e["user"], e["password"], {})
    if not uid:
        raise RuntimeError("❌ Autenticación Odoo fallida")
    models = xmlrpc.client.ServerProxy(f"{e['url']}/xmlrpc/2/object")
    return uid, models


def _extract_code(prod_field: str) -> str:
    # Si viene como "[CODE] Descripción" extrae CODE; si no, devuelve tal cual
    m = re.match(r"\s*\[([^\]]+)\]", str(prod_field))
    return m.group(1).strip() if m else str(prod_field).strip()


def _find_partner(models, e, uid, name: str) -> Optional[int]:
    args = [[("name", "=", name), ("supplier_rank", ">", 0)]]
    ids = models.execute_kw(
        e["db"], uid, e["password"], "res.partner", "search", args, {"limit": 1}
    )
    if not ids:
        args = [[("name", "ilike", name), ("supplier_rank", ">", 0)]]
        ids = models.execute_kw(
            e["db"], uid, e["password"], "res.partner", "search", args, {"limit": 1}
        )
    return ids[0] if ids else None


def _find_receipts_type(models, e, uid, warehouse_name: str) -> Optional[int]:
    if not warehouse_name:
        return None
    wids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "stock.warehouse",
        "search",
        [[("name", "ilike", warehouse_name)]],
        {"limit": 1},
    )
    if not wids:
        return None
    pt = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "stock.picking.type",
        "search",
        [[("code", "=", "incoming"), ("warehouse_id", "=", wids[0])]],
        {"limit": 1},
    )
    return pt[0] if pt else None


def _to_float(x, default: float = 0.0) -> float:
    """Convierte a float aceptando '', 'nan', None y coma decimal."""
    if x is None:
        return default
    if isinstance(x, float) and math.isnan(x):
        return default
    s = str(x).strip()
    if s == "" or s.lower() == "nan":
        return default
    # si hay una sola coma decimal, cámbiala por punto
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return default


def _find_product(models, e, uid, code: str) -> Optional[Tuple[int, int]]:
    """
    Búsqueda general (proveedores no Michelin):
    - Exacta por default_code
    - Fallback por ilike
    Devuelve (product_id, uom_po_id)
    """
    if not code:
        return None
    ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.product",
        "search",
        [[("default_code", "=", code)]],
        {"limit": 1},
    )
    if not ids:
        ids = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "product.product",
            "search",
            [[("default_code", "ilike", code)]],
            {"limit": 1},
        )
    if not ids:
        return None
    rec = models.execute_kw(
        e["db"], uid, e["password"], "product.product", "read", [ids, ["uom_po_id"]]
    )[0]
    uom_po_id = rec["uom_po_id"][0] if rec.get("uom_po_id") else None
    return ids[0], uom_po_id


def _find_product_vendor_cai(
    models, e, uid, partner_id: int, cai: str
) -> Optional[Tuple[int, int]]:
    """
    Búsqueda limitada al proveedor (para Michelin):
    - Filtra productos cuyo template tenga seller del partner dado
    - Busca default_code ILIKE CAI
    - Prioriza los que acaban en CAI (convención '...MIC<CAI>')
    Devuelve (product_id, uom_po_id)
    """
    if not cai or not partner_id:
        return None

    domain = [
        ("product_tmpl_id.seller_ids.name", "=", partner_id),
        ("default_code", "ilike", cai),
    ]
    res: List[Dict] = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.product",
        "search_read",
        [domain],
        {"fields": ["id", "default_code", "uom_po_id"], "limit": 50},
    )

    if res:
        ending = [
            r
            for r in res
            if (r.get("default_code") or "").upper().endswith(cai.upper())
        ]
        pick = ending[0] if ending else res[0]
        uom_po_id = pick["uom_po_id"][0] if pick.get("uom_po_id") else None
        return pick["id"], uom_po_id

    # Fallback general
    return _find_product(models, e, uid, cai)


# ---------- utilidades de precios ----------


def _get_company_currency(models, e, uid) -> int:
    comp = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "res.company",
        "read",
        [[e["company_id"]], ["currency_id"]],
    )[0]
    return comp["currency_id"][0]


def _currency_convert(
    models,
    e,
    uid,
    amount: float,
    from_currency_id: int,
    to_currency_id: int,
    date_str: str,
) -> float:
    """Convierte divisa en Odoo; si falla, devuelve el mismo amount."""
    try:
        res = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "res.currency",
            "_convert",
            [
                [from_currency_id],
                amount,
                to_currency_id,
                e["company_id"],
                date_str,
                True,
            ],
        )
        return float(res)
    except Exception:
        return amount


def _get_purchase_price_for_line(
    models,
    e,
    uid,
    product_id: int,
    partner_id: int,
    qty: float,
    date_planned: str,
) -> float:
    """
    Precio de compra en moneda de compañía:
    1) product.supplierinfo del partner (vigente y con min_qty <= qty)
    2) standard_price del producto
    """
    prec = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.product",
        "read",
        [[product_id], ["product_tmpl_id", "standard_price"]],
    )[0]
    tmpl_id = prec["product_tmpl_id"][0]
    standard_price = float(prec.get("standard_price") or 0.0)

    si_domain = [
        ("name", "=", partner_id),
        ("product_tmpl_id", "=", tmpl_id),
        "|",
        ("date_start", "=", False),
        ("date_start", "<=", date_planned),
        "|",
        ("date_end", "=", False),
        ("date_end", ">=", date_planned),
        ("min_qty", "<=", qty or 0),
    ]
    si_ids = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "product.supplierinfo",
        "search",
        [si_domain],
        {"limit": 1},
    )

    if si_ids:
        si = models.execute_kw(
            e["db"],
            uid,
            e["password"],
            "product.supplierinfo",
            "read",
            [si_ids, ["price", "currency_id"]],
        )[0]
        price = float(si.get("price") or 0.0)
        if price > 0:
            comp_cur = _get_company_currency(models, e, uid)
            cur = si.get("currency_id")
            cur_id = cur[0] if isinstance(cur, (list, tuple)) else (cur or comp_cur)
            if cur_id != comp_cur:
                price = _currency_convert(
                    models, e, uid, price, cur_id, comp_cur, date_planned
                )
            return price

    return standard_price


# ---------- Import principal ----------


def import_csv(csv_path: str | Path) -> None:
    e = _env()
    missing = [k for k in ("url", "db", "user", "password") if not e[k]]
    if missing:
        print(f"[ODOO] Falta .env: {', '.join(missing)}. No se importa: {csv_path}")
        return

    uid, models = _connect(e)
    df = pd.read_csv(csv_path)

    proveedor = str(df.iloc[0].get("Proveedor", "")).strip()

    # Limpiar NaN en referencia
    ref_cell = df.iloc[0].get("Referencia de proveedor", "")
    if pd.isna(ref_cell) or str(ref_cell).strip().lower() == "nan":
        ref = ""
    else:
        ref = str(ref_cell).strip()

    destino = str(df.iloc[0].get("Entregar a", "")).strip()

    # Evitar duplicados por partner_ref (usa Nº de albarán en Michelin)
    exists = models.execute_kw(
        e["db"],
        uid,
        e["password"],
        "purchase.order",
        "search",
        [[("partner_ref", "=", ref), ("state", "!=", "cancel")]],
        {"limit": 1},
    )
    if exists:
        print(f"[ODOO] PO ya existe con ref '{ref}' (id {exists[0]}). Saltado.")
        return

    partner_id = _find_partner(models, e, uid, proveedor)
    if not partner_id:
        print(f"[ODOO] Proveedor no encontrado: '{proveedor}'. Saltado.")
        return

    picking_type_id = _find_receipts_type(models, e, uid, destino)
    date_planned = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    lines, missing_codes = [], set()
    is_michelin = proveedor.strip().upper().startswith("MICHELIN")

    for _, r in df.iterrows():
        code = _extract_code(r.get("Líneas del pedido/Producto", ""))
        desc = str(r.get("Líneas del pedido/Descripción", "") or "")
        qty = _to_float(r.get("Líneas del pedido/Cantidad", 0))
        price = _to_float(r.get("Líneas del pedido/Precio unitario", 0))
        disc = _to_float(r.get("Líneas del pedido/(%) Descuento", 0))

        if is_michelin:
            # 'code' es el CAI del PDF -> buscar dentro del catálogo del proveedor
            found = _find_product_vendor_cai(models, e, uid, partner_id, code)
        else:
            found = _find_product(models, e, uid, code)

        if not found:
            missing_codes.add(code)
            continue

        product_id, uom_po_id = found

        # Rellenar precio si viene vacío
        if price <= 0:
            price = _get_purchase_price_for_line(
                models,
                e,
                uid,
                product_id=product_id,
                partner_id=partner_id,
                qty=qty,
                date_planned=date_planned,
            )

        vals = {
            "product_id": product_id,
            "name": desc or code,
            "date_planned": date_planned,
            "product_qty": qty,
            "price_unit": price,
            "discount": disc,
        }
        if uom_po_id:
            # Clave: usar unidad de compra para que respete la cantidad
            vals["product_uom"] = uom_po_id
        lines.append((0, 0, vals))

    if missing_codes:
        print(
            f"[ODOO] Productos no encontrados ({len(missing_codes)}): {', '.join(sorted(missing_codes))}. PO no creado."
        )
        return
    if not lines:
        print("[ODOO] No hay líneas válidas. PO no creado.")
        return

    order_vals: Dict[str, Any] = {
        "partner_id": partner_id,
        "partner_ref": ref,
        "company_id": e["company_id"],
        "order_line": lines,
    }
    if picking_type_id:
        order_vals["picking_type_id"] = picking_type_id

    po_id = models.execute_kw(
        e["db"], uid, e["password"], "purchase.order", "create", [order_vals]
    )
    print(f"[ODOO] Orden de compra creada ID {po_id} (ref {ref})")
