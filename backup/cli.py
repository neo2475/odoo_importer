# cli.py
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

from core.csv_writer import write_csv
from core.gmail_downloader import fetch_from_labels
from core.logger import get_logger, setup_logging
from core.pdf import read_pdf_text

# Adapters: asumo paquete 'adapters' con registry
from adapters import detect_provider, get_adapter, registry  # type: ignore


def _call_odoo_importer(csv_path: Path, log) -> None:
    """Intenta importar a Odoo con dos estrategias:
    A) Modulo externo "importar_csv_odoo" si existe.
    B) Fallback: nuestro importador "core.odoo_importer.import_csv".
    """
    # Opción A: importador legacy si el usuario lo tiene en el entorno PYTHONPATH
    try:
        import importar_csv_odoo  # type: ignore
        importar_csv_odoo.main(str(csv_path))
        log.info(f"[IMPORT] Odoo OK (importar_csv_odoo.main): {csv_path.name}")
        return
    except ModuleNotFoundError:
        log.debug("[IMPORT] importar_csv_odoo no encontrado, probando core.odoo_importer...")
    except Exception as e:
        log.exception(f"[IMPORT] Error con importar_csv_odoo.main: {e}")
        raise

    try:
        # Opción B: importador modular
        from core.odoo_importer import import_csv
        import_csv(str(csv_path))
        log.info(f"[IMPORT] Odoo OK (core.odoo_importer.import_csv): {csv_path.name}")
    except Exception as e:
        log.exception(f"[IMPORT] Error con core.odoo_importer.import_csv: {e}")
        raise


def _detect_provider_safe(text: str, filename: str | None, log):
    """Compat con detect_provider(text, filename) y detect_provider(text)."""
    # 1) Preferimos firma nueva: (text, filename)
    try:
        prov = detect_provider(text, filename)  # type: ignore[arg-type]
        if prov:
            return prov
    except TypeError:
        # Firma antigua
        try:
            prov = detect_provider(text)  # type: ignore[call-arg]
            if prov:
                return prov
        except Exception as e:
            log.debug(f"[ADAPTERS] detect_provider(text) falló: {e}")
    except Exception as e:
        log.debug(f"[ADAPTERS] detect_provider(text, filename) falló: {e}")

    # 2) Heurística mínima por nombre/contenido
    fname = (filename or "").lower()
    low = text.lower()
    if "michelin" in low or "michelin" in fname:
        return "michelin"
    if "grupo peña" in low or "gp automoción" in low or "gp automocion" in low or "gpa" in fname:
        return "grupo_pena"  # adapta al key de tu registry
    if "varona" in low or "varona" in fname:
        return "varona"
    return ""


def main():
    load_dotenv()
    setup_logging()  # activar sinks (consola + logs/app.log)
    log = get_logger()

    # Registrar adapters (asegúrate de tenerlos en tu proyecto)
    import adapters.gpautomocion  # noqa: F401
    import adapters.michelin      # noqa: F401
    import adapters.varona        # noqa: F401

    log.debug(f"[ADAPTERS] Registrados: {list(registry.keys())}")

    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="Ruta a PDF o carpeta (si no se usa --fetch-mail)")
    ap.add_argument("--provider", help="Forzar proveedor (opcional)")
    ap.add_argument("--import", dest="do_import", action="store_true", help="Importar a Odoo")
    ap.add_argument("--out", default="out", help="Carpeta de salida CSV")
    ap.add_argument("--fetch-mail", action="store_true", help="Descargar PDFs de Gmail a INPUT_DIR/--inbox")
    ap.add_argument("--inbox", default=os.getenv("INPUT_DIR", "inbox"), help="Carpeta destino de PDFs (inbox)")
    args = ap.parse_args()

    # 1) Descarga previa desde Gmail (opcional)
    if args.fetch_mail:
        inbox = Path(args.inbox)
        inbox.mkdir(parents=True, exist_ok=True)
        saved = fetch_from_labels(str(inbox))
        print(f"[GMAIL] Guardados {len(saved)} PDFs en {inbox.resolve()}")

    # 2) Detectar PDFs a procesar
    to_process: list[Path] = []
    if args.input:
        p = Path(args.input)
        if p.is_file() and p.suffix.lower() == ".pdf":
            to_process = [p]
        elif p.is_dir():
            to_process = sorted(p.glob("*.pdf"))
        else:
            print(f"[ERROR] input inválido: {p}")
            return
    else:
        # sin input explícito: usar inbox por defecto
        inbox = Path(args.inbox)
        inbox.mkdir(parents=True, exist_ok=True)
        to_process = sorted(inbox.glob("*.pdf"))

    if not to_process:
        print("[INFO] No hay PDFs que procesar.")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 3) Procesar uno a uno
    for pdf in to_process:
        try:
            # Detectar proveedor
            text = read_pdf_text(pdf)
            provider_name = args.provider or _detect_provider_safe(text, pdf.name, log)
            if not provider_name:
                msg = f"[SKIPPED] Proveedor no reconocido para {pdf.name}"
                print(msg)
                log.warning(msg)
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

