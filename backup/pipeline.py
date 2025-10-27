# pipeline.py
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def sh(cmd: list[str], *, env: dict | None = None) -> int:
    print(f">>> Ejecutando: {' '.join(cmd)}")
    res = subprocess.run(cmd, env=env)
    return res.returncode


def main():
    load_dotenv(override=True)

    env_inbox = os.getenv("INPUT_DIR", "./inbox")
    env_out = os.getenv("OUTPUT_DIR", "./out")
    env_do_import = os.getenv("PIPELINE_IMPORT", "true").lower() in {"1", "true", "yes"}
    env_only = os.getenv("PIPELINE_ONLY", "").strip()
    env_force = os.getenv("ODOO_IMPORT_FORCE", "false").lower() in {"1", "true", "yes", "y"}

    ap = argparse.ArgumentParser(
        description="Pipeline: (opcional) fetch mail + parse PDFs -> CSV -> (opcional) importar Odoo"
    )
    ap.add_argument("--inbox", default=env_inbox, help="Carpeta PDFs (inbox)")
    ap.add_argument("--out", default=env_out, help="Carpeta de salida CSV")
    ap.add_argument("--provider", default=env_only, help='Forzar proveedor (ej. "michelin")')
    ap.add_argument("--no-fetch-mail", action="store_true", help="No descargar de Gmail")
    ap.add_argument("--no-import", action="store_true", help="No importar a Odoo")
    ap.add_argument("--force-import", action="store_true", help="Fuerza importación (ignora dedupe)")
    args = ap.parse_args()

    inbox = Path(args.inbox); inbox.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    do_import = False if args.no_import else env_do_import
    only = (args.provider or "").strip()
    force_import = args.force_import or env_force

    cmd = ["uv", "run", "python", "cli.py", str(inbox), "--out", str(out_dir)]
    if not args.no_fetch_mail:
        cmd.append("--fetch-mail")
    if do_import:
        cmd.append("--import")
    if only:
        cmd += ["--provider", only]

    child_env = os.environ.copy()
    if force_import:
        child_env["ODOO_IMPORT_FORCE"] = "1"

    rc = sh(cmd, env=child_env)
    if rc != 0:
        print("❌ Falló cli.py")
        sys.exit(rc)

    csvs = sorted(out_dir.glob("*.csv"))
    print(f"✅ Pipeline OK. CSVs generados: {len(csvs)} en {out_dir.resolve()}")
    for p in csvs[:10]:
        print(f" - {p.name}")
    if len(csvs) > 10:
        print(f"   … y {len(csvs) - 10} más")


if __name__ == "__main__":
    main()

