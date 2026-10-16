"""Test-wide auth bypass: endpoint tests exercise document logic, not auth. This overrides
the auth dependencies to a fixed test uid and stubs documents_store so no network/secret is
needed. Auth's own behavior is unit-tested directly in test_auth.py / test_documents_store.py."""
import pytest

import auth
import documents_store
import main

TEST_UID = "test-uid"


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Avoid hitting real LLMs when backend/.env is loaded via main import."""
    monkeypatch.setenv("DEV_NO_AUTH", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


@pytest.fixture(autouse=True)
def _bypass_auth(request, monkeypatch):
    main.app.dependency_overrides[auth.current_user] = lambda: TEST_UID
    main.app.dependency_overrides[auth.current_user_bearer] = lambda: TEST_UID
    main.app.dependency_overrides[auth.owner_bearer] = lambda: TEST_UID
    main.app.dependency_overrides[auth.owner_any] = lambda: TEST_UID
    # Skip documents_store stubs for tests that exercise documents_store directly.
    # request.module.__name__ may be "tests.test_documents_store" or "test_documents_store".
    if not request.module.__name__.endswith("test_documents_store"):
        monkeypatch.setattr(documents_store, "create", lambda *a, **k: None)
        monkeypatch.setattr(documents_store, "get_owner", lambda d: TEST_UID)
        monkeypatch.setattr(documents_store, "set_status", lambda *a, **k: None)
    yield
    main.app.dependency_overrides.clear()
