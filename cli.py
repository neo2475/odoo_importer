# cli.py
import argparse
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

from adapters import detect_provider, get_adapter, registry
from core.csv_writer import write_csv
from core.logger import get_logger, setup_logging
from core.pdf import read_pdf_text


def _call_odoo_importer(csv_path: Path, log) -> None:
    """
    Llama al importador real de Odoo.
    Intenta primero importar desde `importar_csv_odoo.main`,
    y si no existe, prueba `core.odoo_importer.import_csv`.
    """
    try:
        # Opción A: script clásico
        from importar_csv_odoo import main as importar_csv_odoo_main  # type: ignore

        importar_csv_odoo_main(str(csv_path))
        log.info(f"[IMPORT] Odoo OK (importar_csv_odoo.main): {csv_path.name}")
        return
    except ModuleNotFoundError:
        log.debug(
            "[IMPORT] importar_csv_odoo no encontrado, probando core.odoo_importer..."
        )
    except Exception as e:
        log.exception(f"[IMPORT] Error con importar_csv_odoo.main: {e}")
        raise

    try:
        # Opción B: importador modular
        from core.odoo_importer import import_csv  # type: ignore

        import_csv(str(csv_path))
        log.info(f"[IMPORT] Odoo OK (core.odoo_importer.import_csv): {csv_path.name}")
    except ModuleNotFoundError:
        log.error(
            "[IMPORT] No se encontró ningún importador (ni importar_csv_odoo ni core.odoo_importer)."
        )
        raise
    except Exception as e:
        log.exception(f"[IMPORT] Error con core.odoo_importer.import_csv: {e}")
        raise


def main():
    load_dotenv()
    setup_logging()  # activar sinks (consola + logs/app.log)
    log = get_logger()
    log.info("Logger inicializado")

    # Registrar adaptadores (forzar carga explícita)
    import adapters.gpautomocion  # noqa: F401
    import adapters.michelin  # noqa: F401  # NECESARIO para registrar 'michelin'
    import adapters.varona  # noqa: F401

    log.debug(f"[ADAPTERS] Registrados: {list(registry.keys())}")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "input", nargs="?", help="Ruta a PDF o carpeta (si no se usa --fetch-mail)"
    )
    ap.add_argument("--provider", help="Forzar proveedor (opcional)")
    ap.add_argument(
        "--import", dest="do_import", action="store_true", help="Importar a Odoo"
    )
    ap.add_argument("--out", default="out", help="Carpeta de salida CSV")
    ap.add_argument(
        "--fetch-mail",
        action="store_true",
        help="Descargar PDFs de Gmail a INPUT_DIR/--inbox",
    )
    ap.add_argument(
        "--inbox",
        default=os.getenv("INPUT_DIR", "inbox"),
        help="Carpeta destino de PDFs (inbox)",
    )
    args = ap.parse_args()

    # 1) Descarga previa desde Gmail (opcional)
    if args.fetch_mail:
        # <<< IMPORTA SOLO SI SE USA --fetch-mail >>>
        from core.gmail_downloader import fetch_all  # importa tras setup_logging

        saved = fetch_all(args.inbox)
        print(f"Descargados {len(saved)} PDFs en {args.inbox}")
        log.info(f"Descargados {len(saved)} PDFs en {args.inbox}")

    # 2) Determinar origen a procesar
    target = Path(args.input) if args.input else Path(args.inbox)
    if not target.exists():
        msg = f"❌ Ruta no encontrada: {target}"
        print(msg)
        log.error(msg)
        raise SystemExit(msg)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Admite PDFs con extensión en mayúsculas/minúsculas
    if target.is_file():
        pdfs = [target] if target.suffix.lower() == ".pdf" else []
    else:
        pdfs = sorted(
            [p for p in target.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        )

    if not pdfs:
        msg = f"⚠️ No hay PDFs para procesar en: {target}"
        print(msg)
        log.warning(msg)
        raise SystemExit(msg)

    # 3) Procesar PDFs → CSV (+ importar si se solicita)
    for pdf in pdfs:
        try:
            text = read_pdf_text(pdf)
            provider_name = args.provider or detect_provider(text, filename=pdf.name)
            if not provider_name:
                msg = f"❌ No se pudo detectar proveedor para: {pdf.name}. Usa --provider."
                print(msg)
                log.error(msg)
                # Continúa con el siguiente PDF en vez de abortar todo
                continue

            Adapter = get_adapter(provider_name)
            doc = Adapter.parse(str(pdf))  # DataFrame/ImportDoc según adapter

            csv_path = out_dir / f"{pdf.stem}.csv"
            write_csv(doc, csv_path)
            print(f"CSV generado: {csv_path.name}")
            log.info(f"CSV generado: {csv_path}")

            if args.do_import:
                _call_odoo_importer(csv_path, log)

            # === Mover PDF procesado a carpeta processed/ ===
            processed_dir = Path("processed")
            processed_dir.mkdir(parents=True, exist_ok=True)
            dest = processed_dir / pdf.name
            try:
                shutil.move(str(pdf), dest)
                print(f"[PIPELINE] PDF movido a: {dest}")
                log.info(f"[PIPELINE] PDF movido a {dest}")
            except Exception as e:
                print(f"[PIPELINE] Error moviendo {pdf}: {e}")
                log.error(f"[PIPELINE] Error moviendo {pdf}: {e}")

        except Exception as e:
            # Manejo por fichero: loguea y continúa
            err = f"[ERROR] Procesando {pdf.name}: {e}"
            print(err)
            log.exception(err)
            continue


if __name__ == "__main__":
    main()
