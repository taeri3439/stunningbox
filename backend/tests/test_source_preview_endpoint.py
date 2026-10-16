import os
from fastapi.testclient import TestClient

os.environ["DEV_NO_AUTH"] = "1"  # bypass owner guard for the test client
import main  # noqa: E402

client = TestClient(main.app)


def _seed_doc(tmp_path, monkeypatch):
    """Create a minimal source.json under a temp STORAGE and point main.STORAGE at it."""
    import json, models
    doc = models.SemanticDocument(
        source_lang="ko",
        blocks=[models.ParagraphBlock(text="원문 문장", source_span_ids=[])],
    )
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    d = os.path.join(str(tmp_path), "docX")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh)
    return "docX"


def test_source_preview_returns_wrapped_html(tmp_path, monkeypatch):
    doc_id = _seed_doc(tmp_path, monkeypatch)
    r = client.get(f"/api/documents/{doc_id}/source-preview")
    assert r.status_code == 200
    assert 'data-seg="' in r.text   # seg-id present (stable seg-id format, not integer)
    assert "원문 문장" in r.text


def test_preview_segments_flag_toggles_spans(tmp_path, monkeypatch):
    doc_id = _seed_doc(tmp_path, monkeypatch)
    # preview needs a translation; with lang==source_lang get_export_model returns source.
    plain = client.get(f"/api/documents/{doc_id}/preview?lang=ko")
    seg = client.get(f"/api/documents/{doc_id}/preview?lang=ko&segments=1")
    assert "data-seg" not in plain.text
    assert 'data-seg="' in seg.text  # seg-id present (stable seg-id format, not integer)
