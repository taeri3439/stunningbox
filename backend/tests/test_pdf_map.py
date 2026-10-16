"""pdf_map: page PNG rendering + manifest freshness."""
import json
import os

import fitz  # PyMuPDF

from pipeline import pdf_map
from pipeline.pdf_map import _norm_tokens, align_words, find_terms, map_segments


def _make_pdf(path, texts_per_page):
    doc = fitz.open()
    for texts in texts_per_page:
        page = doc.new_page(width=595, height=842)  # A4 pt
        y = 72
        for t in texts:
            page.insert_text((72, y), t, fontsize=12)
            y += 24
    doc.save(str(path))
    doc.close()


def test_renders_pages_and_manifest(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["hello world"], ["second page"]])
    out_dir = tmp_path / "tpages"
    m = pdf_map.render_pages(str(pdf), str(out_dir))
    assert [p["n"] for p in m["pages"]] == [0, 1]
    assert (out_dir / "tpage0.png").exists() and (out_dir / "tpage1.png").exists()
    assert abs(m["page_w"] - 595) < 1 and abs(m["page_h"] - 842) < 1
    saved = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved["version"] == m["version"]


def test_fresh_manifest_skips_rerender(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["hi"]])
    out_dir = tmp_path / "tpages"
    m1 = pdf_map.render_pages(str(pdf), str(out_dir))
    mtime = os.path.getmtime(out_dir / "tpage0.png")
    m2 = pdf_map.render_pages(str(pdf), str(out_dir))
    assert m2["version"] == m1["version"]
    assert os.path.getmtime(out_dir / "tpage0.png") == mtime  # untouched


def test_pdf_change_rerenders_and_clears_stale_pages(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["v1 page one"], ["v1 page two"]])
    out_dir = tmp_path / "tpages"
    pdf_map.render_pages(str(pdf), str(out_dir))
    _make_pdf(pdf, [["v2 single page"]])  # rewrite: 1 page now
    m = pdf_map.render_pages(str(pdf), str(out_dir))
    assert len(m["pages"]) == 1
    assert not (out_dir / "tpage1.png").exists()  # stale page removed


def test_render_pages_uses_unique_per_attempt_tmp_name(tmp_path, monkeypatch):
    """Manifest writes must go through a pid+random-suffixed tmp name (not a fixed
    '<manifest>.tmp' path) so two concurrent regenerations can't race the same tmp
    file's os.replace on Windows (PermissionError) — see ds_convert.ensure_pdf's
    identical hardening."""
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["hi"]])
    out_dir = tmp_path / "tpages"
    captured = {}
    orig_replace = os.replace

    def spy_replace(src, dst):
        captured["src"] = src
        return orig_replace(src, dst)

    monkeypatch.setattr(pdf_map.os, "replace", spy_replace)
    pdf_map.render_pages(str(pdf), str(out_dir))
    manifest_path = str(out_dir / "manifest.json")
    assert captured["src"] != manifest_path + ".tmp"
    assert captured["src"].startswith(f"{manifest_path}.{os.getpid()}.")
    assert captured["src"].endswith(".tmp")


def _w(page, x0, y0, x1, y1, tok):
    return (page, fitz.Rect(x0, y0, x1, y1), tok)


def test_norm_tokens_collapses_leaders_case_space():
    assert _norm_tokens("Scope of  Application ......... 8") == ["scope", "of", "application", "8"]
    assert _norm_tokens("통과 (공 용)") == ["통과", "(공", "용)"]
    assert _norm_tokens("···") == []


def test_align_exact_in_order():
    words = [_w(0, 0, 0, 10, 10, "alpha"), _w(0, 0, 20, 10, 30, "beta"),
             _w(0, 0, 40, 10, 50, "gamma")]
    got = align_words(words, [{"id": "s1", "text": "Alpha beta"}, {"id": "s2", "text": "gamma"}])
    page, rect = got["s1"]
    assert page == 0 and rect.y1 == 30  # union of alpha+beta
    assert got["s2"][1].y0 == 40


def test_align_skips_noise_between_segments():
    words = [_w(0, 0, 0, 10, 10, "title"),
             _w(0, 0, 90, 10, 95, "kfs"), _w(0, 0, 90, 10, 95, "0011"),  # running band noise
             _w(1, 0, 0, 10, 10, "body")]
    got = align_words(words, [{"id": "a", "text": "Title"}, {"id": "b", "text": "Body"}])
    assert got["a"][0] == 0 and got["b"][0] == 1


def test_align_miss_returns_none_and_does_not_advance():
    words = [_w(0, 0, 0, 10, 10, "one"), _w(0, 0, 20, 10, 30, "two")]
    got = align_words(words, [{"id": "x", "text": "absent text"}, {"id": "y", "text": "one two"}])
    assert got["x"] is None
    assert got["y"] is not None  # cursor was not consumed by the miss


def test_align_duplicate_segments_take_successive_occurrences():
    words = [_w(0, 0, 0, 10, 10, "go"), _w(0, 0, 20, 10, 30, "stop"),
             _w(0, 0, 40, 10, 50, "go")]
    got = align_words(words, [{"id": "a", "text": "go"}, {"id": "b", "text": "stop"},
                              {"id": "c", "text": "go"}])
    assert got["a"][1].y0 == 0 and got["c"][1].y0 == 40


def test_align_long_segment_matches_head_and_extends():
    toks = [f"w{i}" for i in range(20)]
    words = [_w(0, 0, i * 10, 10, i * 10 + 5, t) for i, t in enumerate(toks)]
    got = align_words(words, [{"id": "long", "text": " ".join(toks)}])
    page, rect = got["long"]
    assert rect.y1 == 195  # extended through ALL 20 words, not just the head


def test_map_segments_fractions_on_real_pdf(tmp_path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf, [["Rated load test", "Second sentence here"], ["Third page-two line"]])
    got = map_segments(str(pdf), [
        {"id": "s1", "text": "Rated load test"},
        {"id": "s2", "text": "Second sentence here"},
        {"id": "s3", "text": "Third page-two line"},
        {"id": "s4", "text": "never rendered"},
    ])
    b1, b3 = got["s1"], got["s3"]
    assert b1["page"] == 0 and b3["page"] == 1
    for b in (b1, b3):
        for k in ("x", "y", "w", "h"):
            assert 0.0 <= b[k] <= 1.0
    assert got["s2"]["y"] > b1["y"]  # below the first line
    assert got["s4"] is None


def test_find_terms_fractions(tmp_path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf, [["Stabilizer goes here", "another Stabilizer"], ["no hits"]])
    got = find_terms(str(pdf), ["Stabilizer", "missing"])
    assert len(got["Stabilizer"]) == 2
    assert all(h["page"] == 0 for h in got["Stabilizer"])
    assert got["missing"] == []


def test_align_survives_long_noise_run_via_window_escalation():
    # alpha, then 500 sequential unmatchable noise words, then zulu.
    words = [_w(0, 0, 0, 10, 10, "alpha")]
    words += [_w(0, 0, (i + 1) * 10, 10, (i + 1) * 10 + 5, f"noise{i}") for i in range(500)]
    words.append(_w(0, 0, 5010, 10, 5020, "zulu"))
    got = align_words(words, [
        {"id": "a", "text": "alpha"},
        {"id": "mid", "text": "unmatchable middle"},
        {"id": "z", "text": "zulu"},
    ])
    assert got["a"] is not None
    assert got["mid"] is None
    assert got["z"] is not None  # only reachable once the search window escalates past 500


def test_align_miss_counter_resets_on_hit():
    # alpha, then a 450-word noise run, then bravo/charlie/delta back-to-back.
    words = [_w(0, 0, 0, 10, 10, "alpha")]
    words += [_w(0, 0, (i + 1) * 10, 10, (i + 1) * 10 + 5, f"noise{i}") for i in range(450)]
    words.append(_w(0, 0, 4510, 10, 4520, "bravo"))
    words.append(_w(0, 0, 4520, 10, 4530, "charlie"))
    words.append(_w(0, 0, 4530, 10, 4540, "delta"))
    got = align_words(words, [
        {"id": "a", "text": "alpha"},
        {"id": "gap", "text": "unmatchable text"},  # forces one miss -> escalates window
        {"id": "b", "text": "bravo"},  # hit via the escalated window -> resets misses
        {"id": "c", "text": "charlie"},  # must still match immediately with the small window
        {"id": "d", "text": "delta"},
    ])
    assert got["gap"] is None
    for seg_id in ("a", "b", "c", "d"):
        assert got[seg_id] is not None, seg_id


def test_map_segments_finds_glued_toc_leader_number(tmp_path):
    pdf = tmp_path / "toc.pdf"
    _make_pdf(pdf, [["Scope of Application.........8"]])
    got = map_segments(str(pdf), [{"id": "toc", "text": "Scope of Application 8"}])
    assert got["toc"] is not None


def test_fraction_clamps_at_page_bottom_right_corner():
    page_rect = fitz.Rect(0, 0, 595, 842)
    rect = fitz.Rect(594.5, 841.5, 594.6, 841.6)  # tiny degenerate rect in the corner
    b = pdf_map._fraction(page_rect, 0, rect)
    assert b["x"] + b["w"] <= 1.0
    assert b["y"] + b["h"] <= 1.0
