import pytest
import native_api


@pytest.fixture(autouse=True)
def isolate_native_storage(tmp_path, monkeypatch):
    # Tests must never list, mutate or depend on real uploaded native documents.
    monkeypatch.setattr(native_api, 'DATA', tmp_path / 'native-documents')
