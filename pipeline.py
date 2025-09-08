# pipeline.py
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def sh(cmd: list[str]) -> int:
    """Ejecuta un comando mostrando stdout/stderr en vivo. Devuelve el rc."""
    print(f">>> Ejecutando: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    return res.returncode


def main():
    load_dotenv(override=True)

    # Defaults por .env (si existen)
    env_inbox = os.getenv("INPUT_DIR", "./inbox")
    env_out = os.getenv("OUTPUT_DIR", "./out")
    env_do_import = os.getenv("PIPELINE_IMPORT", "true").lower() in {"1", "true", "yes"}
    env_only = os.getenv("PIPELINE_ONLY", "").strip()  # p.ej. "michelin"

    ap = argparse.ArgumentParser(
        description="Pipeline completo: (opcional) fetch mail + parse PDFs -> CSV -> (opcional) importar Odoo"
    )
    ap.add_argument(
        "--inbox",
        default=env_inbox,
        help="Carpeta destino de PDFs (inbox). Por defecto INPUT_DIR o ./inbox",
    )
    ap.add_argument(
        "--out",
        default=env_out,
        help="Carpeta de salida CSV. Por defecto OUTPUT_DIR o ./out",
    )
    ap.add_argument(
        "--provider",
        default=env_only,
        help='Forzar proveedor (p.ej. "michelin"). Vacío = autodetección',
    )
    ap.add_argument(
        "--no-fetch-mail",
        action="store_true",
        help="No descargar de Gmail (usa lo que ya esté en --inbox)",
    )
    ap.add_argument(
        "--no-import", action="store_true", help="No importar a Odoo (solo generar CSV)"
    )
    args = ap.parse_args()

    inbox = Path(args.inbox)
    out_dir = Path(args.out)
    only = (args.provider or "").strip()
    do_import = False if args.no_import else env_do_import

    # Asegura carpetas
    inbox.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # === ÚNICA ejecución de cli.py combinando flags ===
    cmd = ["uv", "run", "python", "cli.py", str(inbox), "--out", str(out_dir)]
    if not args.no_fetch_mail:
        cmd.append("--fetch-mail")
    if do_import:
        cmd.append("--import")
    if only:
        cmd += ["--provider", only]

    rc = sh(cmd)
    if rc != 0:
        # Si falla por inbox vacío, muestra mensaje claro y termina con el mismo rc
        print(
            "❌ Falló la ejecución de cli.py. Posible causa: no hay PDFs nuevos en inbox."
        )
        sys.exit(rc)

    # Resumen
    csvs = sorted(out_dir.glob("*.csv"))
    print(f"✅ Pipeline OK. CSVs generados: {len(csvs)} en {out_dir.resolve()}")
    for p in csvs[:10]:
        print(f" - {p.name}")
    if len(csvs) > 10:
        print(f"   … y {len(csvs) - 10} más")


if __name__ == "__main__":
    main()
