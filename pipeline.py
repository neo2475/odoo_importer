# pipeline.py
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import List

from dotenv import load_dotenv


def sh(cmd: List[str], *, env: dict | None = None) -> int:
    print(f">>> Ejecutando: {' '.join(cmd)}")
    res = subprocess.run(cmd, env=env)
    return res.returncode


def scan_inbox(inbox_dir: Path) -> list[Path]:
    """
    Escanea la carpeta de entrada aceptando nombres Unicode y extensión .pdf
    sin importar mayúsculas/minúsculas (incluye .PDF).
    """
    files: list[Path] = []
    if not inbox_dir.exists():
        return files
    for p in inbox_dir.iterdir():
        if not p.is_file():
            continue
        # Normaliza Unicode y comprueba extensión en minúsculas
        name_norm = unicodedata.normalize("NFC", p.name)
        if name_norm.lower().endswith(".pdf"):
            files.append(p)
    return sorted(files)


def list_csvs(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*.csv"))


def main():
    load_dotenv(override=True)

    env_inbox = os.getenv("INPUT_DIR", "./inbox")
    env_out = os.getenv("OUTPUT_DIR", "./out")
    env_do_import = os.getenv("PIPELINE_IMPORT", "true").lower() in {"1", "true", "yes"}
    env_only = os.getenv("PIPELINE_ONLY", "").strip()
    env_force = os.getenv("ODOO_IMPORT_FORCE", "false").lower() in {
        "1",
        "true",
        "yes",
        "y",
    }

    ap = argparse.ArgumentParser(
        description="Pipeline: (opcional) fetch mail + parse PDFs -> CSV -> (opcional) importar Odoo"
    )
    ap.add_argument("--inbox", default=env_inbox, help="Carpeta PDFs (inbox)")
    ap.add_argument("--out", default=env_out, help="Carpeta de salida CSV")
    ap.add_argument(
        "--provider", default=env_only, help='Forzar proveedor (ej. "michelin")'
    )
    ap.add_argument(
        "--no-fetch-mail", action="store_true", help="No descargar de Gmail"
    )
    ap.add_argument("--no-import", action="store_true", help="No importar a Odoo")
    ap.add_argument(
        "--force-import", action="store_true", help="Fuerza importación (ignora dedupe)"
    )
    args = ap.parse_args()

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Logs de contexto para evitar confusiones entre proyectos/clones
    print(f"[CTX] cwd={Path.cwd().resolve()}")
    print(f"[CTX] INBOX={inbox.resolve()}")
    print(f"[CTX] OUT={out_dir.resolve()}")
    ve = os.getenv("VIRTUAL_ENV")
    if ve:
        print(f"[CTX] VIRTUAL_ENV={ve}")

    do_import = False if args.no_import else env_do_import
    only = (args.provider or "").strip()
    force_import = args.force_import or env_force

    # Escaneo previo de PDFs locales (Unicode + .PDF)
    pdfs_local = scan_inbox(inbox)
    if pdfs_local:
        print(f"[LOCAL] Detectados {len(pdfs_local)} PDFs en {inbox.resolve()}")
        for p in pdfs_local[:10]:
            print(f" - {p.name}")
        if len(pdfs_local) > 10:
            print(f"   … y {len(pdfs_local) - 10} más")
    else:
        print("[LOCAL] No hay PDFs en la carpeta inbox.")

    # Construcción del comando principal
    base_cmd = ["uv", "run", "python", "cli.py", str(inbox), "--out", str(out_dir)]
    if do_import:
        base_cmd.append("--import")
    if only:
        base_cmd += ["--provider", only]

    # Primera pasada: respetando el flag --no-fetch-mail
    cmd = base_cmd.copy()
    if not args.no_fetch_mail:
        cmd.append("--fetch-mail")

    # Tomamos snapshot de CSVs para medir nuevos generados
    csvs_before = {p.name for p in list_csvs(out_dir)}

    child_env = os.environ.copy()
    if force_import:
        child_env["ODOO_IMPORT_FORCE"] = "1"

    rc = sh(cmd, env=child_env)
    if rc != 0:
        print("❌ Falló cli.py")
        sys.exit(rc)

    csvs_after = list_csvs(out_dir)
    new_csvs = [p for p in csvs_after if p.name not in csvs_before]

    # Si no se generaron CSVs y SÍ hay PDFs locales, forzamos pasada LOCAL sin fetch
    if not new_csvs and pdfs_local and not args.no_fetch_mail:
        print(
            "[FALLBACK] No se generaron CSVs con Gmail. Reintentando en modo LOCAL (sin --fetch-mail)..."
        )
        cmd_local = base_cmd.copy()  # sin --fetch-mail
        rc2 = sh(cmd_local, env=child_env)
        if rc2 != 0:
            print("❌ Falló cli.py en modo LOCAL")
            sys.exit(rc2)
        csvs_after = list_csvs(out_dir)
        new_csvs = [p for p in csvs_after if p.name not in csvs_before]

    # Reporte final
    print(f"✅ Pipeline OK. CSVs generados: {len(new_csvs)} en {out_dir.resolve()}")
    for p in new_csvs[:10]:
        print(f" - {p.name}")
    if len(new_csvs) > 10:
        print(f"   … y {len(new_csvs) - 10} más")


if __name__ == "__main__":
    main()
