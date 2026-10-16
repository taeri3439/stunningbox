import jwt
import onlyoffice


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_forcesave_builds_signed_command(monkeypatch):
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "testsecret")
    monkeypatch.setattr(onlyoffice, "DS_INTERNAL_URL", "http://ds:8080")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({"key": json["key"], "error": 0})

    monkeypatch.setattr(onlyoffice.httpx, "post", fake_post)

    res = onlyoffice.forcesave("/x/output.en.docx", key="abc123")

    assert res == {"key": "abc123", "error": 0}
    assert captured["url"] == "http://ds:8080/coauthoring/CommandService.ashx"
    assert captured["json"]["c"] == "forcesave"
    assert captured["json"]["key"] == "abc123"
    decoded = jwt.decode(captured["json"]["token"], "testsecret", algorithms=["HS256"])
    assert decoded["c"] == "forcesave" and decoded["key"] == "abc123"


def test_forcesave_recomputes_key_when_none_and_omits_token_without_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "")  # JWT disabled
    p = tmp_path / "output.en.docx"
    p.write_bytes(b"docxbytes")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _FakeResp({"key": json["key"], "error": 4})

    monkeypatch.setattr(onlyoffice.httpx, "post", fake_post)

    res = onlyoffice.forcesave(str(p), key=None)

    assert captured["json"]["key"] == onlyoffice.document_key(str(p))
    assert "token" not in captured["json"]  # secret unset → unsigned command
    assert res["error"] == 4
