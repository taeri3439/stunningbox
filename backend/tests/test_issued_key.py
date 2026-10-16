import os
import main
from pipeline import process


def test_issued_key_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    doc_id, lang = "dkey", "en"
    os.makedirs(process.doc_dir(str(tmp_path), doc_id), exist_ok=True)
    assert main._read_issued_key(doc_id, lang) is None        # absent → None
    main._write_issued_key(doc_id, lang, "abc123")
    assert main._read_issued_key(doc_id, lang) == "abc123"


def test_issued_key_missing_dir_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    main._write_issued_key("ghost", "en", "k")               # no doc dir → best-effort no-op
    assert main._read_issued_key("ghost", "en") is None
