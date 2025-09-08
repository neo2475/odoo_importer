from __future__ import annotations

from typing import Optional


def clean_code(raw: str) -> str:
    return raw.strip()


def parse_decimal(s: str, default: float = 0.0) -> float:
    if s is None:
        return default
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def map_warehouse(address: Optional[str], mapping: dict[str, str], default: str) -> str:
    if not address:
        return default
    for k, v in mapping.items():
        if k.lower() in address.lower():
            return v
    return default
