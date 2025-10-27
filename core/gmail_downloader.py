# core/gmail_downloader.py
from __future__ import annotations

import email
import imaplib
import mimetypes
import os
import re
import time
from email.header import decode_header
from typing import Iterable, List, Optional, Tuple

from dotenv import load_dotenv

from core.logger import get_logger

# =========================
# Carga de entorno y logger
# =========================
load_dotenv(override=True)
log = get_logger()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")

GMAIL_DEBUG = os.getenv("GMAIL_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
GMAIL_UNSEEN_ONLY = os.getenv("GMAIL_UNSEEN_ONLY", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
GMAIL_MARK_SEEN = os.getenv("GMAIL_MARK_SEEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}

INPUT_DIR = os.getenv("INPUT_DIR", "./inbox")

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _split_labels(raw: str) -> list[str]:
    parts = []
    for token in re.split(r"[,\n;]+", raw or ""):
        t = token.strip()
        if t:
            parts.append(t)
    return parts


_raw_labels = os.getenv("GMAIL_LABELS", "")
GMAIL_LABELS = _split_labels(_raw_labels)

# Compat con variables antiguas
for key in (
    "GMAIL_LABEL_VARONA",
    "GMAIL_LABEL_GPA",
    "GMAIL_LABEL_GP",
    "GMAIL_LABEL_GRUPOPENA",
    "GMAIL_LABEL_MICHELIN",
):
    v = os.getenv(key, "").strip()
    if v:
        GMAIL_LABELS.append(v)
if not GMAIL_LABELS:
    GMAIL_LABELS = ["INBOX"]


# =========================
# Utilidades
# =========================
def _debug_env():
    msg = (
        f"[GMAIL] host={IMAP_HOST} | user={GMAIL_USER} | unseen_only={GMAIL_UNSEEN_ONLY} | "
        f"mark_seen={GMAIL_MARK_SEEN} | labels={', '.join(GMAIL_LABELS)} | inbox={os.path.abspath(INPUT_DIR)}"
    )
    log.debug(msg)


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip().replace(" ", "_")
    return SAFE_NAME_RE.sub("_", name) or "adjunto.pdf"


def _decode_rfc_filename(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        parts = decode_header(raw)
        decoded = "".join(
            (b.decode(enc or "utf-8", errors="ignore") if isinstance(b, bytes) else b)
            for b, enc in parts
        ).strip()
        return decoded or None
    except Exception:
        return raw


def _list_folders(imap: imaplib.IMAP4_SSL) -> list[str]:
    try:
        status, data = imap.list()
        if status != "OK":
            return []
        out = []
        for raw in data:
            if not raw:
                continue
            s = raw.decode(errors="ignore")
            m = re.findall(r'"([^"]+)"', s)
            out.append(m[-1] if m else s)
        return out
    except Exception:
        return []


def _find_matching_label(folders: list[str], label_hint: str) -> str | None:
    for f in folders:
        if label_hint.lower() in f.lower():
            return f
    return None


def _select_mailbox(imap: imaplib.IMAP4_SSL, label_hint: str) -> tuple[bool, str]:
    folders = _list_folders(imap)
    chosen = _find_matching_label(folders, label_hint) or label_hint
    if chosen not in folders and label_hint not in folders:
        log.warning(f"[GMAIL] Carpeta no encontrada: {label_hint}")
        return False, ""
    status, _ = imap.select(f'"{chosen}"')
    ok = status == "OK"
    if ok:
        log.info(f"[GMAIL] SELECT '{chosen}' -> OK")
    else:
        log.error(f"[GMAIL] SELECT '{chosen}' -> {status}")
    return ok, chosen


def _unique_path(download_dir: str, filename: str) -> str:
    base = _sanitize_filename(filename)
    root, ext = os.path.splitext(base)
    if (not ext) or ext.lower() != ".pdf":
        ext = ".pdf"
    path = os.path.join(download_dir, f"{root}{ext}")
    i = 1
    while os.path.exists(path):
        path = os.path.join(download_dir, f"{root}-{i}{ext}")
        i += 1
    return path


def _part_filename(part: email.message.Message) -> Optional[str]:
    # 1) get_filename() (RFC2231/RFC2047 aware en parte)
    fname = part.get_filename()
    fname = _decode_rfc_filename(fname) if fname else None
    if fname:
        return fname
    # 2) Content-Disposition
    cd = part.get("Content-Disposition", "")
    m = re.search(r'filename\*?="?([^";]+)', cd, flags=re.I)
    if m:
        return _decode_rfc_filename(m.group(1))
    # 3) Content-Type name=
    ct = part.get("Content-Type", "")
    m = re.search(r'name\*?="?([^";]+)', ct, flags=re.I)
    if m:
        return _decode_rfc_filename(m.group(1))
    return None


def _ensure_pdf_extension(filename: str, content_type: str) -> str:
    root, ext = os.path.splitext(filename)
    if content_type.lower() == "application/pdf":
        return f"{root}.pdf" if ext.lower() != ".pdf" else filename
    # Si el MIME no es pdf pero el nombre termina en .pdf, lo dejamos.
    if ext.lower() == ".pdf":
        return filename
    # Si no hay extensión y parece PDF por MIME secundario
    guessed = (
        mimetypes.guess_extension(content_type or "", strict=False) or ""
    ).lower()
    if guessed == ".pdf":
        return f"{root}.pdf"
    return filename


def _is_pdf_part(part: email.message.Message) -> bool:
    ct = (part.get_content_type() or "").lower()
    fname = (_part_filename(part) or "").lower()
    if ct == "application/pdf":
        return True
    if ct == "application/octet-stream" and fname.endswith(".pdf"):
        return True
    return fname.endswith(".pdf")


def _save_pdf(payload: bytes, download_dir: str, filename: str) -> Optional[str]:
    os.makedirs(download_dir, exist_ok=True)
    target_path = _unique_path(download_dir, filename or "adjunto.pdf")
    with open(target_path, "wb") as f:
        f.write(payload)
    log.info(f"[GMAIL] PDF guardado: {target_path}")
    return target_path


def _extract_pdfs_from_msg(
    msg_obj: email.message.Message, download_dir: str
) -> list[str]:
    saved: list[str] = []
    had_any_attachment = False

    for part in msg_obj.walk():
        if part.get_content_maintype() == "multipart":
            continue

        # .eml anidados
        if part.get_content_type() == "message/rfc822":
            try:
                inner = part.get_payload(0)
                saved.extend(_extract_pdfs_from_msg(inner, download_dir))
            except Exception as e:
                log.debug(f"[GMAIL] message/rfc822 parse error: {e}")
            continue

        disp = (part.get("Content-Disposition") or "").lower()
        is_attachment_like = ("attachment" in disp) or bool(_part_filename(part))
        if is_attachment_like:
            had_any_attachment = True

        if _is_pdf_part(part):
            payload = part.get_payload(decode=True)
            if not payload:
                if GMAIL_DEBUG:
                    log.debug("[GMAIL] Parte PDF sin payload decodificable.")
                continue
            fname = _part_filename(part) or "adjunto.pdf"
            fname = _ensure_pdf_extension(fname, part.get_content_type())
            path = _save_pdf(payload, download_dir, fname)
            if path:
                saved.append(path)

    if had_any_attachment and not saved and GMAIL_DEBUG:
        log.debug("[GMAIL] Mensaje con adjuntos pero ninguno PDF válido para guardar.")
    if not had_any_attachment and GMAIL_DEBUG:
        log.debug("[GMAIL] Mensaje sin adjuntos.")
    return saved


def _gmail_search_with_pdf_hint(imap: imaplib.IMAP4_SSL) -> list[bytes]:
    """
    Busca mensajes NO LEÍDOS con adjuntos PDF.
    Prioriza X-GM-RAW de Gmail, con comillas para evitar 'Could not parse command'.
    """
    try:
        raw = "has:attachment filename:pdf"
        if GMAIL_UNSEEN_ONLY:
            raw = f"({raw}) is:unread"
        # Importante: envolver entre comillas la query completa
        status, data = imap.uid("SEARCH", None, "X-GM-RAW", f'"{raw}"')
        if status == "OK":
            return data[0].split()
    except Exception as e:
        log.debug(f"[GMAIL] X-GM-RAW fallback por: {e}")

    # Fallback a UNSEEN y filtrado manual
    criterion = "UNSEEN" if GMAIL_UNSEEN_ONLY else "ALL"
    status, data = imap.search(None, criterion)
    return data[0].split() if status == "OK" else []


# =========================
# Flujo principal por etiqueta
# =========================
def fetch_pdfs_from_label(label: str, download_dir: str) -> Tuple[List[str], int]:
    """
    Descarga PDFs de una etiqueta concreta.
    Retorna (lista_de_rutas_guardadas, total_mensajes_no_leidos_encontrados)
    """
    saved: List[str] = []
    unread_count = 0
    start_ts = time.time()

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_USER, GMAIL_PASSWORD)
        log.info(f"[GMAIL] LOGIN OK en {IMAP_HOST} como {GMAIL_USER}")
    except Exception as e:
        log.exception(f"[GMAIL] Error de conexión/login IMAP: {e}")
        return saved, unread_count

    try:
        ok, selected = _select_mailbox(imap, label)
        if not ok:
            log.error(f"[GMAIL] Etiqueta no encontrada: {label}")
            return saved, unread_count

        os.makedirs(download_dir, exist_ok=True)

        ids = _gmail_search_with_pdf_hint(imap)
        unread_count = len(ids)
        log.info(
            f"[GMAIL] Etiqueta '{selected}': NO LEÍDOS con PDFs encontrados: {unread_count}"
        )

        for num in ids:
            try:
                typ, data = imap.uid("FETCH", num, "(RFC822)")
                if typ != "OK" or not data or not data[0]:
                    if GMAIL_DEBUG:
                        log.debug(f"[GMAIL] UID FETCH falló para {num!r}")
                    continue
                msg_obj = email.message_from_bytes(data[0][1])

                # Descargar solo PDFs
                paths = _extract_pdfs_from_msg(msg_obj, download_dir)
                for p in paths:
                    log.info(f"[GMAIL] Descargado: {os.path.basename(p)}")

                # Marcar como leído solo si hubo descarga de al menos un PDF
                if GMAIL_MARK_SEEN and paths:
                    try:
                        imap.uid("STORE", num, "+FLAGS", r"(\Seen)")
                        if GMAIL_DEBUG:
                            log.debug(f"[GMAIL] Marcado como leído UID={num.decode()}")
                    except Exception as e:
                        log.warning(
                            f"[GMAIL] No se pudo marcar como leído UID={num.decode()}: {e}"
                        )

                saved.extend(paths)

            except Exception as e:
                log.exception(f"[GMAIL] Error procesando mensaje UID={num!r}: {e}")
                # Continuar con el resto

        elapsed = time.time() - start_ts
        log.info(
            f"[GMAIL] Etiqueta '{selected}' completada. PDFs nuevos: {len(saved)} | "
            f"Correos no leídos inspeccionados: {unread_count} | t={elapsed:.2f}s"
        )
        return saved, unread_count

    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass


# =========================
# Flujo multi-etiqueta
# =========================
def fetch_from_labels(
    download_dir: str, labels: Iterable[str] | None = None
) -> List[str]:
    """
    Punto de entrada multi-etiqueta. Descarga ÚNICAMENTE PDFs.
    """
    _debug_env()
    labels = list(labels) if labels else list(GMAIL_LABELS) or ["INBOX"]

    os.makedirs(download_dir, exist_ok=True)

    total_saved: List[str] = []
    total_pdfs = 0
    total_unread = 0

    log.info("[GMAIL] Inicio de proceso multi-etiqueta")
    for label in labels:
        log.info(f"[GMAIL] Procesando etiqueta: {label}")
        saved, unread = fetch_pdfs_from_label(label, download_dir)
        total_unread += unread
        total_pdfs += len(saved)
        total_saved.extend(saved)
        log.info(
            f"[GMAIL] Resumen parcial '{label}': no leídos={unread}, PDFs descargados={len(saved)}"
        )

    log.info(
        f"[GMAIL] RESUMEN FINAL: etiquetas={len(labels)}, no leídos totales={total_unread}, PDFs descargados={total_pdfs}"
    )
    return total_saved


# Atajo CLI sencillo si se ejecuta directamente
if __name__ == "__main__":
    try:
        fetch_from_labels(INPUT_DIR, GMAIL_LABELS)
    except Exception as e:
        log.exception(f"[GMAIL] Error no controlado en ejecución principal: {e}")
