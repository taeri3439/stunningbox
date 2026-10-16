"""OnlyOffice Document Server integration helpers.

The browser embeds the OnlyOffice editor (an iframe served by the Document Server, DS)
and edits the translated `.docx` directly — so the DS, not our JS, owns pagination and
WYSIWYG. Two trust-sensitive URLs flow through here:

  * document.url   — where the DS FETCHES the docx to open (must be reachable *from the
                     DS container*, not the browser).
  * callbackUrl    — where the DS POSTS edited-document notifications on save.

Both are signed into a JWT (HS256) so the DS only honours configs/callbacks we issued.
Recent DS images enable JWT by default; the shared secret is ONLYOFFICE_JWT_SECRET.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt

# Shared secret with the Document Server (its JWT_SECRET / JWT env). Empty disables
# signing (only for a DS started with JWT_ENABLED=false — not recommended).
JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "")

# Base URL the DS uses to reach THIS backend (docx fetch + save callback). Inside Docker
# the DS can't see the host's localhost, so the default targets the host gateway.
BACKEND_URL = os.environ.get("ONLYOFFICE_BACKEND_URL", "http://host.docker.internal:8000")

# Browser-facing DS URL (where the editor's api.js + iframe load from). Returned to the
# frontend so it doesn't need its own build-time env.
PUBLIC_URL = os.environ.get("ONLYOFFICE_PUBLIC_URL", "http://localhost:8080")

# DS URL reachable FROM THIS backend (to download the edited file named in a save
# callback). The callback's `url` often carries the DS's own internal host, so we swap
# its scheme+host for this. Defaults to the published port on the host.
DS_INTERNAL_URL = os.environ.get("ONLYOFFICE_DS_INTERNAL_URL", "http://localhost:8080")


def ds_fetch_url(url: str) -> str:
    """Rewrite a DS-provided file URL to a host the backend can actually reach,
    preserving the path/query (the cache token) but swapping scheme+host."""
    base = urlsplit(DS_INTERNAL_URL.rstrip("/"))
    u = urlsplit(url)
    return urlunsplit((base.scheme, base.netloc, u.path, u.query, u.fragment))


def sign(payload: dict) -> str:
    """Sign a config/payload for the DS. Empty string if JWT is disabled."""
    if not JWT_SECRET:
        return ""
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify(token: str) -> dict:
    """Decode+verify a DS-issued token (raises on bad signature)."""
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def document_key(docx_path: str) -> str:
    """A version key the DS uses to cache/reload a document. MUST change whenever the
    file content changes (so a re-save reloads), be unique per (doc, version), ≤128 chars
    and use only [0-9a-zA-Z._=-]. We hash the path + mtime + size."""
    try:
        st = os.stat(docx_path)
        seed = f"{docx_path}:{int(st.st_mtime_ns)}:{st.st_size}"
    except OSError:
        seed = docx_path
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()  # 40 hex chars


def build_editor_config(
    doc_id: str, lang: str, title: str | None, docx_path: str, bookmark: str | None = None
) -> dict:
    """The config object passed to `new DocsAPI.DocEditor(...)`, with a signed token.

    `lang` is the target language (also the docx suffix). `docx_path` is the on-disk file
    that backs the /docx endpoint — used only to derive the version key.
    """
    base = BACKEND_URL.rstrip("/")
    config: dict = {
        "documentType": "word",
        "document": {
            "fileType": "docx",
            "key": document_key(docx_path),
            "title": f"{(title or 'document')[:60]}.{lang}.docx",
            "url": f"{base}/api/documents/{doc_id}/docx?lang={lang}",
        },
        "editorConfig": {
            "mode": "edit",
            "lang": lang,
            "callbackUrl": f"{base}/api/documents/{doc_id}/onlyoffice-callback?lang={lang}",
            # Trim chrome irrelevant to a "proofread the translation" flow. All of these
            # are honoured by the free (Community) edition; branding keys (logo/about/
            # customer) are NOT and so are intentionally omitted.
            "customization": {
                "anonymous": {"request": False, "label": "Guest"},  # no name prompt
                "chat": False,
                "comments": False,
                "plugins": False,
                "feedback": False,
                "help": False,
                "goback": False,  # we have our own "새 문서" link
                "compactHeader": True,
                "hideRightMenu": True,  # collapsed by default; user can reopen
                "uiTheme": "theme-light",
                # Open the doc fit to the editor's width so the gray "desk" margins around
                # the page shrink to fill our column. -2 = fit-page-width (not a fixed %);
                # honoured by Community 9.4.0 for "word". NOTE: a user-changed zoom is saved
                # to the browser's localStorage and overrides this — so -2 is the first-load
                # DEFAULT on a clean profile, not a forced value.
                "zoom": -2,
            },
        },
    }
    # Scroll the editor to a bookmark on open (e.g. a just-re-translated block). The
    # bookmark must exist in the docx (the renderer emits 'blk{i}' per block).
    if bookmark:
        config["editorConfig"]["actionLink"] = {
            "action": {"type": "bookmark", "data": bookmark}
        }
    token = sign(config)
    if token:
        config["token"] = token
    return config


def verify_callback(headers, body: dict) -> dict:
    """Validate an incoming save callback. The DS puts the JWT either in the
    Authorization header (payload wrapped under `payload`) or in the body's `token`.
    Returns the trusted body. Raises if JWT verification fails OR the secret is unset —
    the save callback overwrites the stored docx, so it must fail CLOSED on misconfig
    (an empty secret would otherwise accept an unauthenticated overwrite)."""
    if not JWT_SECRET:
        raise ValueError("OnlyOffice JWT secret not configured")
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        decoded = verify(auth[len("Bearer "):])
        return decoded.get("payload", decoded)
    if body.get("token"):
        return verify(body["token"])
    raise ValueError("Missing OnlyOffice callback token")


def forcesave(docx_path: str, key: str | None = None) -> dict:
    """Tell the Document Server to immediately save the live editing session for this doc
    (without closing it) — it responds, then POSTs a status-6 callback that we download +
    sync. `key` MUST match the key the editor opened with (persisted by the editor-config
    route); if None we recompute from the file, which is correct only while the on-disk file
    is unchanged since open. Returns the CommandService response {"key", "error"}:
    0 = accepted, 4 = no unsaved changes, 1 = no live session for key, 6 = invalid token."""
    cmd: dict = {"c": "forcesave", "key": key or document_key(docx_path)}
    token = sign(cmd)
    if token:
        cmd["token"] = token
    url = DS_INTERNAL_URL.rstrip("/") + "/coauthoring/CommandService.ashx"
    r = httpx.post(url, json=cmd, timeout=30)
    r.raise_for_status()
    return r.json()
