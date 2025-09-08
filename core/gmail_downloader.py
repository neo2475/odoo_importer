# core/gmail_downloader.py
from __future__ import annotations

import email
import imaplib
import os
import re
from typing import Iterable, List, Tuple

from dotenv import load_dotenv

from core.logger import get_logger

# Cargar .env con override ANTES de leer variables
load_dotenv(override=True)
log = get_logger()

# --- Config básica ---
GMAIL_DEBUG = os.getenv("GMAIL_DEBUG", "false").lower() in {"1", "true", "yes"}
IMAP_HOST = os.getenv("GMAIL_IMAP", "imap.gmail.com")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
# Solo correos no leídos
GMAIL_UNSEEN_ONLY = os.getenv("GMAIL_UNSEEN_ONLY", "true").lower() in {
    "1",
    "true",
    "yes",
}
# Marcar como leído los mensajes procesados
GMAIL_MARK_SEEN = os.getenv("GMAIL_MARK_SEEN", "false").lower() in {"1", "true", "yes"}


# --- Etiquetas desde .env ---
def _split_labels(raw: str) -> list[str]:
    # Admite comas, punto y coma y saltos de línea; quita espacios extra
    parts = []
    for token in re.split(r"[,\n;]+", raw or ""):
        t = token.strip()
        if t:
            parts.append(t)
    return parts


_raw_labels = os.getenv("GMAIL_LABELS", "")
GMAIL_LABELS = _split_labels(_raw_labels)

# Variables por proveedor opcionales (se añaden si existen)
for key in (
    "GMAIL_LABEL_VARONA",
    "GMAIL_LABEL_GPA",
    "GMAIL_LABEL_GP",
    "GMAIL_LABEL_GRUPOPENA",
    "GMAIL_LABEL_MICHELIN",  # soporte explícito Michelin
):
    v = os.getenv(key, "").strip()
    if v:
        GMAIL_LABELS.append(v)

# Fallback si no hay nada configurado
if not GMAIL_LABELS:
    GMAIL_LABELS = [
        "Albaranes compra Varona",
        "Albaranes compra gpautomocion",
        "Albaranes compra Michelin",
    ]

# Quita duplicados preservando orden
GMAIL_LABELS = list(dict.fromkeys(GMAIL_LABELS))

# --- Debug de configuración ---
if GMAIL_DEBUG:
    msg = (
        "[GMAIL] Debug ON | "
        f"host={IMAP_HOST} | unseen_only={GMAIL_UNSEEN_ONLY} | mark_seen={GMAIL_MARK_SEEN} | "
        f"env.GMAIL_LABELS_raw={_raw_labels!r} | labels={', '.join(GMAIL_LABELS)}"
    )
    print(msg)
    log.debug(msg)

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _decode_folder_line(line: bytes) -> str:
    """
    Gmail devuelve líneas tipo: b'(\\HasNoChildren) "/" "Albaranes compra Varona"'
    Extraemos la última parte entre comillas.
    """
    s = line.decode(errors="ignore")
    parts = s.split(' "/" ')
    if len(parts) >= 2:
        return parts[-1].strip().strip('"')
    # Fallback: última comilla
    m = re.findall(r'"([^"]+)"', s)
    return m[-1] if m else s


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    log.debug(f"[GMAIL] Ensure dir: {path}")


def _sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = SAFE_NAME_RE.sub("_", name)
    return name


def _save_pdf(payload: bytes, dest_dir: str, filename: str) -> str:
    _ensure_dir(dest_dir)
    base = _sanitize_filename(filename)
    target = os.path.join(dest_dir, base)
    # Evitar sobreescritura
    if os.path.exists(target):
        i = 1
        stem, ext = os.path.splitext(base)
        while True:
            candidate = os.path.join(dest_dir, f"{stem}_{i}{ext}")
            if not os.path.exists(candidate):
                log.debug(
                    f"[GMAIL] Renombrado para evitar colisión: {base} -> {os.path.basename(candidate)}"
                )
                target = candidate
                break
            i += 1
    with open(target, "wb") as f:
        f.write(payload)
    print(f"[GMAIL] Guardado: {os.path.basename(target)}")
    log.info(f"[GMAIL] Guardado PDF en {target}")
    return target


def _select_mailbox(imap: imaplib.IMAP4_SSL, label_hint: str) -> Tuple[bool, str]:
    # Buscar carpeta que contenga label_hint (case-insensitive)
    status, boxes = imap.list()
    if status != "OK":
        print("[GMAIL] Error listando buzones.")
        log.error(f"[GMAIL] LIST status != OK ({status})")
        return False, ""
    label_hint_up = label_hint.upper()
    chosen = ""
    for b in boxes or []:
        folder = _decode_folder_line(b)
        if label_hint_up in folder.upper():
            chosen = folder
            break
    if not chosen:
        log.warning(f"[GMAIL] No se encontró carpeta que contenga: {label_hint}")
        return False, ""
    status, _ = imap.select(f'"{chosen}"')
    ok = status == "OK"
    if ok:
        if GMAIL_DEBUG:
            print(f"[GMAIL] Seleccionada carpeta: {chosen}")
        log.info(f"[GMAIL] SELECT '{chosen}' -> OK")
    else:
        print(f"[GMAIL] No se pudo seleccionar carpeta: {chosen}")
        log.error(f"[GMAIL] SELECT '{chosen}' -> {status}")
    return ok, chosen


def fetch_pdfs_from_label(label: str, download_dir: str) -> List[str]:
    saved: List[str] = []
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_USER, GMAIL_PASSWORD)
        log.info(f"[GMAIL] LOGIN OK en {IMAP_HOST}")
    except Exception as e:
        print(f"[GMAIL] Error de conexión/login IMAP: {e}")
        log.exception("[GMAIL] Error de conexión/login IMAP")
        return saved

    try:
        ok, selected = _select_mailbox(imap, label)
        if not ok:
            print(f"[GMAIL] Etiqueta no encontrada: {label}")
            log.warning(f"[GMAIL] Etiqueta no encontrada: {label}")
            if GMAIL_DEBUG:
                tip = "[GMAIL] Sugerencia: uv run python -c 'from core.gmail_downloader import debug_list; debug_list()'"
                print(tip)
                log.debug(tip)
            imap.logout()
            return saved

        # 1ª pasada: UNSEEN; si 0 y debug activo, probar ALL para diagnóstico
        criterion = "(UNSEEN)" if GMAIL_UNSEEN_ONLY else "ALL"
        status, result = imap.search(None, criterion)
        if status != "OK":
            print(f"[GMAIL] Error al buscar mensajes en: {selected}")
            log.error(f"[GMAIL] SEARCH {criterion} -> {status} en {selected}")
            imap.logout()
            return saved

        ids = result[0].split()
        if GMAIL_DEBUG:
            print(f"[GMAIL] Mensajes encontrados con {criterion}: {len(ids)}")
        log.info(f"[GMAIL] {selected}: {len(ids)} mensajes con {criterion}")

        # fallback de diagnóstico
        if GMAIL_UNSEEN_ONLY and not ids and GMAIL_DEBUG:
            status2, result2 = imap.search(None, "ALL")
            ids2 = result2[0].split() if status2 == "OK" else []
            msg = f"[GMAIL] Diagnóstico: con ALL hay {len(ids2)} mensajes (UNSEEN=0). ¿Están marcados como leídos?"
            print(msg)
            log.debug(msg)

        for num in ids:
            try:
                _, data = imap.fetch(num, "(RFC822)")
                msg_obj = email.message_from_bytes(data[0][1])
            except Exception as e:
                print(f"[GMAIL] Error al recuperar mensaje {num!r}: {e}")
                log.exception(f"[GMAIL] FETCH fallo en msg {num!r}")
                continue

            attach_count = 0
            for part in msg_obj.walk():
                # Detección de adjunto PDF más tolerante
                ct = (part.get_content_type() or "").lower()
                disp = (part.get("Content-Disposition") or "").lower()
                fname = part.get_filename()

                is_pdf = (fname and fname.lower().endswith(".pdf")) or (
                    ct == "application/pdf"
                )
                is_attachment = ("attachment" in disp) or bool(fname)

                if is_pdf and is_attachment:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        log.debug("[GMAIL] Parte PDF sin payload, se ignora.")
                        continue
                    name = fname or "adjunto.pdf"
                    path = _save_pdf(payload, download_dir, name)
                    saved.append(path)  # <-- AÑADIR AL LISTADO
                    attach_count += 1

            if attach_count == 0 and GMAIL_DEBUG:
                print("[GMAIL] Mensaje sin adjuntos PDF válidos.")
                log.debug("[GMAIL] Mensaje sin adjuntos PDF válidos.")

            if GMAIL_MARK_SEEN and attach_count > 0:
                imap.store(num, "+FLAGS", "\\Seen")
                log.debug(
                    f"[GMAIL] Marcado como leído msg {num.decode(errors='ignore')}"
                )

    except Exception as e:
        print(f"[GMAIL] Error procesando etiqueta '{label}': {e}")
        log.exception(f"[GMAIL] Error procesando etiqueta '{label}'")
    finally:
        try:
            imap.logout()
            log.debug("[GMAIL] LOGOUT")
        except Exception:
            pass

    return saved


def fetch_all(download_dir: str, labels: Iterable[str] | None = None) -> List[str]:
    labels = (
        list(labels) if labels else [l for l in (x.strip() for x in GMAIL_LABELS) if l]
    )
    all_saved: List[str] = []
    if not labels:
        msg = "[GMAIL] No hay etiquetas configuradas. Define GMAIL_LABELS o GMAIL_LABEL_* en .env"
        print(msg)
        log.info(msg)
        return all_saved
    for label in labels:
        print(f"[GMAIL] Procesando etiqueta: {label}")
        log.info(f"[GMAIL] Procesando etiqueta: {label}")
        saved = fetch_pdfs_from_label(label, download_dir)
        all_saved.extend(saved)
    print(f"[GMAIL] Total PDFs guardados: {len(all_saved)}")
    log.info(f"[GMAIL] Total PDFs guardados: {len(all_saved)}")
    return all_saved


def debug_list() -> None:
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_USER, GMAIL_PASSWORD)
        status, boxes = imap.list()
        print(f"[GMAIL] list status={status}")
        log.info(f"[GMAIL] LIST status={status}")
        for b in boxes or []:
            folder = _decode_folder_line(b)
            print("  ", folder)
            log.debug(f"[GMAIL] Carpeta: {folder}")
        imap.logout()
    except Exception as e:
        print(f"[GMAIL] Error listando buzones: {e}")
        log.exception("[GMAIL] Error listando buzones")
