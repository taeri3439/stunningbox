from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

import app as web
from engine.inspect import inspect_pdf
from engine.models import RenderRequest
from engine.service import render


def make_pdf(path: Path, rotated_text=False, scanned=False, rotation=0):
    with fitz.open() as doc:
        page = doc.new_page(width=420, height=595)
        page.draw_rect(fitz.Rect(20, 20, 400, 120), color=None, fill=(0.92, 0.95, 1))
        page.draw_line((20, 125), (400, 125), color=(0.2, 0.3, 0.5))
        if not scanned:
            page.insert_text((35, 58), "Layout preservation", fontsize=18, fontname="hebo", color=(0.2, 0.3, 0.5))
            page.insert_text((35, 90), "Original text stays inside its region.", fontsize=10)
            page.insert_text((35, 165), "A separate paragraph to compare.", fontsize=11)
            if rotated_text:
                page.insert_text((390, 300), "Rotated label", fontsize=12, rotate=90)
        page.set_rotation(rotation)
        doc.save(path)
    return path


def test_replay_preserves_text_geometry_and_background(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    original_bytes = source.read_bytes()
    layout = inspect_pdf(source)
    output = tmp_path / "output.pdf"
    report = render(source, output, layout, RenderRequest())
    assert source.read_bytes() == original_bytes
    assert report["validation"]["geometry_ok"]
    assert report["validation"]["processed"] == layout["region_count"]
    assert all(r["text_check"] for r in report["regions"])
    assert all(p["outside_changed_pixels"] == 0 for p in report["validation"]["pages"])
    with fitz.open(source) as before, fitz.open(output) as after:
        assert len(before[0].get_drawings()) == len(after[0].get_drawings())


def test_fit_replaces_text_and_removes_original(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    layout = inspect_pdf(source)
    region = layout["pages"][0]["regions"][0]
    output = tmp_path / "output.pdf"
    report = render(source, output, layout, RenderRequest(mode="fit", replacements={region["id"]: "New title"}, line_height=1))
    status = next(r for r in report["regions"] if r["id"] == region["id"])
    assert status["status"] != "NEEDS_REVIEW", status
    with fitz.open(output) as doc:
        text = doc[0].get_text()
        assert "New title" in text
        assert "Layout preservation" not in text


def test_overflow_is_reported_and_source_region_retained(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    layout = inspect_pdf(source)
    region = layout["pages"][0]["regions"][0]
    report = render(source, tmp_path / "out.pdf", layout, RenderRequest(mode="fit", replacements={region["id"]: "too much text " * 500}))
    assert report["regions"][0]["status"] == "NEEDS_REVIEW"
    assert report["validation"]["status"] == "NEEDS_REVIEW"
    with fitz.open(tmp_path / "out.pdf") as doc:
        assert "Layout preservation" in doc[0].get_text()


def test_rotation_coordinates_and_unsupported_text(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", rotated_text=True, rotation=90)
    layout = inspect_pdf(source)
    assert layout["pages"][0]["width"] == 595
    report = render(source, tmp_path / "out.pdf", layout, RenderRequest())
    assert report["validation"]["geometry_ok"]
    assert any(r["status"] == "NEEDS_REVIEW" and "회전" in r["reason"] for r in report["regions"])


def test_no_text_is_not_falsely_successful(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", scanned=True)
    report = render(source, tmp_path / "out.pdf", inspect_pdf(source), RenderRequest())
    assert report["validation"]["status"] == "NEEDS_REVIEW"
    assert report["validation"]["total"] == 0


def test_unknown_ids_are_rejected(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    with pytest.raises(ValueError, match="영역 ID"):
        render(source, tmp_path / "out.pdf", inspect_pdf(source), RenderRequest(replacements={"fake": "x"}))


def test_link_region_is_kept(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    with fitz.open(source) as doc:
        doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(30, 35, 260, 65), "uri": "https://example.com"})
        doc.save(tmp_path / "linked.pdf")
    source = tmp_path / "linked.pdf"
    report = render(source, tmp_path / "out.pdf", inspect_pdf(source), RenderRequest())
    assert report["regions"][0]["status"] == "NEEDS_REVIEW"
    with fitz.open(tmp_path / "out.pdf") as doc:
        assert len(doc[0].get_links()) == 1


def test_korean_fit_has_extractable_glyphs(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    layout = inspect_pdf(source)
    region = layout["pages"][0]["regions"][0]
    report = render(source, tmp_path / "out.pdf", layout, RenderRequest(mode="fit", replacements={region["id"]: "복원 확인"}, line_height=1))
    assert report["regions"][0]["status"] != "NEEDS_REVIEW"
    assert report["regions"][0]["text_check"]


def test_cropped_rotated_page_replay(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    with fitz.open(source) as doc:
        doc[0].set_cropbox(fitz.Rect(15, 15, 410, 570))
        doc[0].set_rotation(90)
        doc.save(tmp_path / "cropped.pdf")
    source = tmp_path / "cropped.pdf"
    report = render(source, tmp_path / "out.pdf", inspect_pdf(source), RenderRequest())
    assert report["validation"]["status"] == "CHECKS_PASSED", report


def test_api_upload_render_baseline_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DATA", tmp_path / "data")
    web.DATA.mkdir()
    client = TestClient(web.app)
    source = make_pdf(tmp_path / "source.pdf")
    assert client.get("/").status_code == 200
    bad = client.post("/api/documents", files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 400
    uploaded = client.post("/api/documents", files={"file": ("sample.pdf", source.read_bytes(), "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()
    prefix = f"/api/documents/{document['id']}"
    assert client.get(prefix + "/pages/source/0").headers["content-type"] == "image/png"
    assert client.get(prefix + "/pages/source/20").status_code == 404
    assert client.post(prefix + "/render", json={"min_scale": 0.1}).status_code == 422
    result = client.post(prefix + "/render", json={"mode": "replay"})
    assert result.status_code == 200, result.text
    run = result.json()["latest_run"]
    assert client.get(prefix + f"/download/new?run={run}").content.startswith(b"%PDF")
    assert client.get(prefix + f"/download/report?run={run}").json()["run_id"] == run
    assert client.get(prefix + "/download/new?run=invalid").status_code == 404
    baseline = client.post(prefix + "/baseline", files={"file": ("old.pdf", source.read_bytes(), "application/pdf")})
    assert baseline.json()["baseline"]["page_count"] == 1
    assert client.get(prefix + "/pages/baseline/0").status_code == 200
    assert len(client.get("/api/documents").json()) == 1
