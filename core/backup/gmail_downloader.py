# core/gmail_downloader.py
from __future__ import annotations

import email
import imaplib
import os
import re
from typing import Iterable, List

from dotenv import load_dotenv

from core.logger import get_logger

load_dotenv(override=True)
log = get_logger()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
GMAIL_DEBUG = os.getenv("GMAIL_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
GMAIL_UNSEEN_ONLY = os.getenv("GMAIL_UNSEEN_ONLY", "true").strip().lower() in {"1", "true", "yes"}
GMAIL_MARK_SEEN = os.getenv("GMAIL_MARK_SEEN", "true").strip().lower() in {"1", "true", "yes"}

def _split_labels(raw: str) -> list[str]:
    parts = []
    for token in re.split(r"[,\n;]+", raw or ""):
        t = token.strip()
        if t:
            parts.append(t)
    return parts

_raw_labels = os.getenv("GMAIL_LABELS", "")
GMAIL_LABELS = _split_labels(_raw_labels)

for key in ("GMAIL_LABEL_VARONA","GMAIL_LABEL_GPA","GMAIL_LABEL_GP",
            "GMAIL_LABEL_GRUPOPENA","GMAIL_LABEL_MICHELIN"):
    v = os.getenv(key, "").strip()
    if v:
        GMAIL_LABELS.append(v)
if not GMAIL_LABELS:
    GMAIL_LABELS = ["INBOX"]

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

def _debug_env():
    msg = (f"[GMAIL] host={IMAP_HOST} | unseen_only={GMAIL_UNSEEN_ONLY} | mark_seen={GMAIL_MARK_SEEN} | "
           f"labels={', '.join(GMAIL_LABELS)}")
    print(msg); log.debug(msg)

def _sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    return SAFE_NAME_RE.sub("_", name) or "adjunto.pdf"

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

def _save_pdf(payload: bytes, download_dir: str, filename: str) -> str:
    safe = _sanitize_filename(filename)
    path = os.path.join(download_dir, safe)
    with open(path, "wb") as f:
        f.write(payload)
    log.info(f"[GMAIL] PDF guardado: {path}")
    return path

def fetch_pdfs_from_label(label: str, download_dir: str) -> List[str]:
    saved: List[str] = []
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_USER, GMAIL_PASSWORD)
        log.info(f"[GMAIL] LOGIN OK en {IMAP_HOST}")
    except Exception as e:
        print(f"[GMAIL] Error IMAP: {e}")
        log.exception("[GMAIL] Error de conexión/login IMAP")
        return saved

    try:
        ok, selected = _select_mailbox(imap, label)
        if not ok:
            print(f"[GMAIL] Etiqueta no encontrada: {label}")
            return saved

        os.makedirs(download_dir, exist_ok=True)

        criterion = "UNSEEN" if GMAIL_UNSEEN_ONLY else "ALL"
        status, data = imap.search(None, criterion)
        if status != "OK":
            print(f"[GMAIL] SEARCH falló en '{selected}'")
            return saved

        ids = data[0].split()
        for num in ids:
            try:
                _, data = imap.fetch(num, "(RFC822)")
                msg_obj = email.message_from_bytes(data[0][1])
            except Exception as e:
                print(f"[GMAIL] FETCH fallo {num!r}: {e}")
                continue

            attach_count = 0
            for part in msg_obj.walk():
                ct = (part.get_content_type() or "").lower()
                disp = (part.get("Content-Disposition") or "").lower()
                fname = part.get_filename()
                is_pdf = (fname and fname.lower().endswith(".pdf")) or (ct == "application/pdf")
                is_attachment = ("attachment" in disp) or bool(fname)
                if is_pdf and is_attachment:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    path = _save_pdf(payload, download_dir, fname or "adjunto.pdf")
                    saved.append(path)
                    attach_count += 1

            if attach_count == 0 and GMAIL_DEBUG:
                print(f"[GMAIL] Mensaje {num!r} sin PDF.")

            if GMAIL_MARK_SEEN:
                try:
                    imap.store(num, "+FLAGS", "\\Seen")
                except Exception:
                    pass
        return saved
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass

def fetch_from_labels(download_dir: str, labels: Iterable[str] | None = None) -> List[str]:
    _debug_env()
    labels = list(labels) if labels else list(GMAIL_LABELS) or ["INBOX"]
    all_saved: List[str] = []
    for label in labels:
        print(f"[GMAIL] Procesando etiqueta: {label}")
        saved = fetch_pdfs_from_label(label, download_dir)
        all_saved.extend(saved)
    print(f"[GMAIL] Total PDFs guardados: {len(all_saved)}")
    return all_saved

