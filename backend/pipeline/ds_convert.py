"""OnlyOffice ConvertService client: output.{lang}.docx -> output.{lang}.pdf.

The DS converter (x2t) is the same layout engine the embedded editor uses, so
the produced PDF paginates exactly like the editor screen — viewers built on
this PDF match the editor pixel-for-pixel. Freshness is keyed on the docx
version hash so a saved edit or re-render triggers exactly one reconversion.
"""
from __future__ import annotations

import os
import threading
import uuid

import httpx

import onlyoffice


class DsConvertError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"OnlyOffice conversion failed (error {code})")
        self.code = code


def pdf_path(storage_dir: str, doc_id: str, lang: str) -> str:
    return os.path.join(storage_dir, doc_id, f"output.{lang}.pdf")


# Backend routes now fire paired concurrent ensure_pdf calls for the same (doc, lang)
# (e.g. tpages + tsegments fetched together), so a stale-cache miss must be serialized per
# key: otherwise both callers convert and race writing the same tmp file (os.replace can
# raise PermissionError on Windows) or interleave writes, leaving a corrupt PDF next to a
# valid .key sidecar. One lock per (storage_dir, doc_id, lang), guarded by a meta-lock that
# only protects dict access (never held during the actual conversion/IO).
_locks_meta = threading.Lock()
_locks: dict[tuple[str, str, str], threading.Lock] = {}


def _lock_for(storage_dir: str, doc_id: str, lang: str) -> threading.Lock:
    key = (storage_dir, doc_id, lang)
    with _locks_meta:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def ensure_pdf(storage_dir: str, doc_id: str, lang: str) -> str:
    """Convert the translated docx to PDF via the DS ConvertService iff stale."""
    docx = os.path.join(storage_dir, doc_id, f"output.{lang}.docx")
    if not os.path.exists(docx):
        raise FileNotFoundError(docx)
    out = pdf_path(storage_dir, doc_id, lang)
    key_file = out + ".key"

    def _fresh(version: str) -> bool:
        if not os.path.exists(out):
            return False
        try:
            with open(key_file, encoding="utf-8") as fh:
                return fh.read().strip() == version
        except OSError:
            return False

    # Fast path: skip locking entirely when the cache is already fresh.
    if _fresh(onlyoffice.document_key(docx)):
        return out
    with _lock_for(storage_dir, doc_id, lang):
        # Re-check freshness INSIDE the lock: a concurrent caller may have just finished
        # converting this exact version while we were waiting on the lock, in which case we
        # cache-hit instead of reconverting.
        version = onlyoffice.document_key(docx)
        if _fresh(version):
            return out
        data = _convert(doc_id, lang, version)
        # Unique-per-attempt tmp name (pid + random hex) as defense in depth: even if two
        # processes/threads ever raced past the lock (e.g. separate worker processes), they
        # would no longer clobber each other's in-flight write.
        tmp = f"{out}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, out)
        with open(key_file, "w", encoding="utf-8") as fh:
            fh.write(version)
        return out


def _convert(doc_id: str, lang: str, version: str) -> bytes:
    base = onlyoffice.BACKEND_URL.rstrip("/")
    body = {
        # Measured ~2.2s for a 21-page docx on the local DS -> synchronous is fine.
        "async": False,
        "filetype": "docx",
        "outputtype": "pdf",
        # DS caches conversion results by key: unique per docx version, and the
        # "-pdf" suffix keeps it distinct from the editor session key.
        "key": f"{version}-pdf",
        "url": f"{base}/api/documents/{doc_id}/docx?lang={lang}",
        "title": f"output.{lang}.docx",
    }
    token = onlyoffice.sign(body)
    if token:
        body["token"] = token
    url = onlyoffice.DS_INTERNAL_URL.rstrip("/") + "/ConvertService.ashx"
    r = httpx.post(url, json=body, headers={"Accept": "application/json"}, timeout=120)
    r.raise_for_status()
    data = r.json()
    if data.get("error") is not None:
        raise DsConvertError(data["error"])
    if not data.get("endConvert") or not data.get("fileUrl"):
        raise DsConvertError("incomplete")
    got = httpx.get(onlyoffice.ds_fetch_url(data["fileUrl"]), timeout=120)
    got.raise_for_status()
    return got.content
