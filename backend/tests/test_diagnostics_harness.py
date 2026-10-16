"""Offline guards for the diagnosis harness (no network, no LibreOffice)."""
import json
import os

from diagnostics import aggregate, pair, schema


def test_severity_mapping():
    assert schema.severity_for(4) == "none"
    assert schema.severity_for(3) == "low"
    assert schema.severity_for(2) == "medium"
    assert schema.severity_for(1) == "high"
    assert schema.severity_for(0) == "high"
    assert schema.severity_for(None) == "none"


def test_page_finding_recompute_fills_severity_and_mean():
    pf = schema.PageFinding(dimensions=[
        schema.DimensionFinding(dimension="font_family", score=0),
        schema.DimensionFinding(dimension="alignment", score=4),
        schema.DimensionFinding(dimension="table_shading", score=None),  # n/a
    ]).recompute()
    sev = {d.dimension: d.severity for d in pf.dimensions}
    assert sev["font_family"] == "high"
    assert sev["alignment"] == "none"
    assert sev["table_shading"] == "none"
    assert pf.page_score == 2.0  # mean of {0,4}, null skipped


def test_tool_schema_lists_all_dimensions():
    s = schema.tool_schema()
    enum = s["properties"]["dimensions"]["items"]["properties"]["dimension"]["enum"]
    assert set(enum) == set(schema.DIMENSIONS)


def test_build_pairs_handles_page_count_mismatch():
    src = ["s0", "s1", "s2", "s3"]
    ren = ["r0", "r1"]
    pairs = pair.build_pairs(src, ren)
    assert [p.index for p in pairs] == [0, 1]  # overlap only
    mm = pair.page_count_mismatch(src, ren)
    assert mm["source_pages"] == 4 and mm["rendered_pages"] == 2 and mm["gap"] == 2
    assert pair.page_count_mismatch(["a"], ["b"]) is None  # equal counts -> no mismatch


def test_aggregate_ranks_lowest_mean_first(tmp_path):
    run_dir = tmp_path / "run"
    # doc A: font_family terrible (0), alignment great (4)
    _write_page(run_dir, "docA", 0, {"font_family": 0, "alignment": 4})
    _write_page(run_dir, "docA", 1, {"font_family": 1, "alignment": 4})
    # doc B: font_family bad again, table_shading mediocre
    _write_page(run_dir, "docB", 0, {"font_family": 0, "table_shading": 2})

    report = aggregate.aggregate(str(run_dir))
    ranked = [d["dimension"] for d in report["dimensions"] if d["mean_score"] is not None]
    assert ranked[0] == "font_family"  # mean ~0.33, worst
    ff = next(d for d in report["dimensions"] if d["dimension"] == "font_family")
    assert ff["high_severity_count"] == 3
    assert ff["worst_examples"][0]["score"] == 0
    assert report["total_pages"] == 3


def test_outputs_confined_to_runs_dir():
    # The harness writes only under diagnostics/runs/. Guard the constant.
    from diagnostics import run as run_mod
    assert run_mod.RUNS_ROOT.replace("\\", "/").endswith("diagnostics/runs")


def _write_page(run_dir, doc_id, idx, dim_scores):
    ddir = run_dir / doc_id
    ddir.mkdir(parents=True, exist_ok=True)
    pf = schema.PageFinding(
        doc_id=doc_id, page_index=idx, pair_png=f"{doc_id}/pairs/pair{idx}.png",
        dimensions=[schema.DimensionFinding(dimension=k, score=v)
                    for k, v in dim_scores.items()],
    ).recompute()
    (ddir / f"page{idx}.json").write_text(
        json.dumps(pf.model_dump(), ensure_ascii=False), encoding="utf-8")
    # a meta.json so aggregate exercises that path too
    (ddir / "meta.json").write_text(json.dumps({"doc_id": doc_id}), encoding="utf-8")
