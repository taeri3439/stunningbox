"""Supabase auth for the FastAPI backend.

Verifies the Supabase access token and enforces per-user document ownership. Supabase's
current default signs tokens with an asymmetric key (ES256/RS256), verified against the
project's published JWKS; the legacy symmetric HS256 secret is still accepted for back-compat
/ key rotation. Token sources:
  - Authorization: Bearer <access_token>   (JSON / mutating endpoints; CSRF-immune)
  - sb-<ref>-auth-token cookie             (read-only navigation GETs: iframe/img/anchor)
The @supabase/ssr cookie value is `base64-`+base64url(JSON session); large sessions are
chunked into `…-auth-token.0/.1/…`. Env is read at call time (module imports without it)."""
from __future__ import annotations

import base64
import functools
import json
import os
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request

import documents_store

# Require these claims so a forged token can't omit exp (→ never expires), sub, or aud.
_DECODE_OPTS = {"require": ["exp", "sub", "aud"]}

# Fixed uid for local POC when DEV_NO_AUTH=1 (no Supabase login).
_DEV_UID = "00000000-0000-4000-8000-000000000001"


def _dev_no_auth() -> bool:
    return os.environ.get("DEV_NO_AUTH") == "1"


@functools.lru_cache(maxsize=4)
def _jwk_client(jwks_url: str) -> jwt.PyJWKClient:
    """Cached per URL so the JWKS isn't refetched on every request."""
    return jwt.PyJWKClient(jwks_url)


def _verify_jwt(token: str) -> str:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    try:
        if alg in ("ES256", "RS256"):
            # Asymmetric signing key: verify against the project's public JWKS — never a secret.
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            if not base:
                raise HTTPException(500, "Auth not configured")
            key = _jwk_client(f"{base}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token, key, algorithms=["ES256", "RS256"], audience="authenticated", options=_DECODE_OPTS,
            )
        elif alg == "HS256":
            # Legacy symmetric secret. Verified with the shared secret (not any public key), so
            # there's no RS/HS algorithm-confusion path here.
            secret = os.environ.get("SUPABASE_JWT_SECRET", "")
            if not secret:
                raise HTTPException(500, "Auth not configured")
            payload = jwt.decode(
                token, secret, algorithms=["HS256"], audience="authenticated", options=_DECODE_OPTS,
            )
        else:
            raise HTTPException(401, "Invalid or expired token")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(401, "Token missing subject")
    return uid


def _project_ref() -> str:
    host = urlparse(os.environ.get("SUPABASE_URL", "")).hostname or ""
    return host.split(".")[0]


def _b64url_json(value: str) -> dict | None:
    if value.startswith("base64-"):
        raw = value[len("base64-"):]
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            return json.loads(decoded)
        except Exception:  # noqa: BLE001
            return None
    try:  # raw (unprefixed) JSON
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return None


def _token_from_cookies(cookies: dict[str, str], ref: str) -> str | None:
    base = f"sb-{ref}-auth-token"
    if base in cookies:
        value = cookies[base]
    else:  # reassemble chunks .0, .1, … in order
        chunks = sorted(
            (k for k in cookies if k.startswith(base + ".")),
            key=lambda k: int(k.rsplit(".", 1)[1]),
        )
        if not chunks:
            return None
        value = "".join(cookies[k] for k in chunks)
    session = _b64url_json(value)
    if not session:
        return None
    return session.get("access_token")


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h and h.startswith("Bearer "):
        return h[len("Bearer "):]
    return None


def current_user_bearer(request: Request) -> str:
    """Identity from the Authorization header only (JSON/mutating endpoints)."""
    if _dev_no_auth():
        return _DEV_UID
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Missing bearer token")
    return _verify_jwt(token)


def current_user(request: Request) -> str:
    """Identity from the Authorization header OR the Supabase cookie (read-only nav GETs)."""
    if _dev_no_auth():
        return _DEV_UID
    token = _bearer(request) or _token_from_cookies(request.cookies, _project_ref())
    if not token:
        raise HTTPException(401, "Not authenticated")
    return _verify_jwt(token)


def require_owner(doc_id: str, uid: str) -> str:
    """404 (not 403) if the doc has no owner row or is owned by someone else."""
    owner = documents_store.get_owner(doc_id)
    if owner is None or owner != uid:
        raise HTTPException(404, "Unknown document")
    return uid


def owner_bearer(doc_id: str, request: Request) -> str:
    return require_owner(doc_id, current_user_bearer(request))


def owner_any(doc_id: str, request: Request) -> str:
    return require_owner(doc_id, current_user(request))
