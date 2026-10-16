import base64
import json
import time

import jwt
import pytest
from fastapi import HTTPException

import auth
import documents_store

SECRET = "test-secret"


def _token(claims=None, exp_delta=3600):
    payload = {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + exp_delta}
    if claims:
        payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_verify_valid(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert auth._verify_jwt(_token()) == "user-1"


def test_verify_bad_signature(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    bad = jwt.encode({"sub": "x", "aud": "authenticated"}, "other", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        auth._verify_jwt(bad)
    assert e.value.status_code == 401


def test_verify_expired(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException):
        auth._verify_jwt(_token(exp_delta=-10))


def _es256_token(signing_key, exp_delta=3600):
    return jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + exp_delta},
        signing_key,
        algorithm="ES256",
        headers={"kid": "k1"},
    )


def _fake_jwk_client(public_key):
    class _Key:
        key = public_key

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key()

    return lambda _url: _Client()


def test_verify_es256_valid(monkeypatch):
    # Supabase's current default: tokens signed with an asymmetric ES256 key, verified via JWKS.
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(auth, "_jwk_client", _fake_jwk_client(priv.public_key()))
    assert auth._verify_jwt(_es256_token(priv)) == "user-1"


def test_verify_es256_wrong_key(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    signer = ec.generate_private_key(ec.SECP256R1())
    impostor = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(auth, "_jwk_client", _fake_jwk_client(impostor.public_key()))
    with pytest.raises(HTTPException) as e:
        auth._verify_jwt(_es256_token(signer))
    assert e.value.status_code == 401


def test_verify_es256_unconfigured(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(HTTPException) as e:
        auth._verify_jwt(_es256_token(priv))
    assert e.value.status_code == 500


def test_token_from_cookies_single():
    sess = json.dumps({"access_token": "AT", "token_type": "bearer"})
    val = "base64-" + base64.urlsafe_b64encode(sess.encode()).decode().rstrip("=")
    cookies = {"sb-abc-auth-token": val}
    assert auth._token_from_cookies(cookies, "abc") == "AT"


def test_token_from_cookies_chunked():
    sess = json.dumps({"access_token": "AT2"})
    full = "base64-" + base64.urlsafe_b64encode(sess.encode()).decode().rstrip("=")
    mid = len(full) // 2
    cookies = {"sb-abc-auth-token.0": full[:mid], "sb-abc-auth-token.1": full[mid:]}
    assert auth._token_from_cookies(cookies, "abc") == "AT2"


def test_require_owner_ok(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: "user-1")
    assert auth.require_owner("doc1", "user-1") == "user-1"


def test_require_owner_mismatch_404(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: "someone-else")
    with pytest.raises(HTTPException) as e:
        auth.require_owner("doc1", "user-1")
    assert e.value.status_code == 404


def test_require_owner_missing_404(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: None)
    with pytest.raises(HTTPException) as e:
        auth.require_owner("doc1", "user-1")
    assert e.value.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/whatever/status?lang=en",       # owner_bearer
        "/api/documents/whatever/result?lang=en",        # owner_bearer
        "/api/documents/whatever/segments?lang=en",      # owner_bearer
        "/api/documents/whatever/glossary-report?lang=en",  # owner_bearer
        "/api/documents/whatever/editor-config?lang=en",  # owner_bearer
        "/api/documents/whatever/download?lang=en",      # owner_any (nav GET)
        "/api/documents/whatever/preview?lang=en",       # owner_any (nav GET)
        "/api/documents/whatever/view?lang=en",          # owner_any (nav GET)
        "/api/documents/whatever/assets/x.png",          # owner_any (nav GET)
    ],
)
def test_doc_endpoints_require_auth(path):
    """No bearer + no cookie → 401 on every gated doc endpoint (regression guard against a
    dependency being dropped)."""
    from fastapi.testclient import TestClient
    import main
    main.app.dependency_overrides.clear()  # undo the autouse bypass for this assertion
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get(path).status_code == 401
