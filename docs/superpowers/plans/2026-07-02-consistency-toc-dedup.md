# Consistency + TOC Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD.

**Goal:** (a) identical source segments translate identically (and are sent to the LLM once); (b) wrapped TOC entries stop duplicating their number and regain their dot leader.

## Global Constraints

- Deterministic; no API calls in tests (Mock/spy translators). Never `git add -A`; protected files (`backend/auth.py`, `backend/documents_store.py`, `frontend/**`, `backend/_*.py`, `test-pdfs/`, root images) untouched.
- `iter_segments`/`build_segment_index_map` untouched.
- From `backend/`: `./.venv/Scripts/python.exe -m pytest …`; full suite at the end (15 pre-existing test_auth/test_documents_store failures excluded).
- Commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: unique-text dedup in `translate_document` (TDD)

**Files:** Modify `backend/pipeline/translator.py` (the `if payload:` body, ~lines 136-162); Test `backend/tests/test_translate_dedup.py` (create).

- [ ] **Step 1: Failing tests:**

```python
"""Identical source segments must translate identically (translated once)."""
import models
from pipeline.translator import translate_document


class SpyTranslator:
    """Returns 'T<call#>:<text>' so drifting duplicates would be visible; records calls."""

    def __init__(self):
        self.calls = []

    def translate(self, texts, source_lang, target_lang, glossary, on_done=None, style_context=""):
        self.calls.append(list(texts))
        return [f"T:{t}" for t in texts]


def _doc(texts):
    return models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text=t, source_span_ids=[f"s{i}"]) for i, t in enumerate(texts)
    ])


def test_duplicates_sent_once_and_fanned_out():
    spy = SpyTranslator()
    doc = _doc(["통과 (공 용)", "정지", "통과 (공 용)", "통과 (공 용)"])
    out = translate_document(doc, "en", translator=spy)
    sent = [t for call in spy.calls for t in call]
    assert sent == ["통과 (공 용)", "정지"]          # unique, first-seen order
    got = [b.text for b in out.blocks]
    assert got == ["T:통과 (공 용)", "T:정지", "T:통과 (공 용)", "T:통과 (공 용)"]


def test_tm_hits_apply_to_all_occurrences():
    spy = SpyTranslator()
    doc = _doc(["A", "B", "A"])
    out = translate_document(doc, "en", translator=spy, tm_map={"A": "TM-A"})
    sent = [t for call in spy.calls for t in call]
    assert sent == ["B"]                              # TM text never reaches the LLM
    assert [b.text for b in out.blocks] == ["TM-A", "T:B", "TM-A"]


def test_all_tm_reports_completion(monkeypatch):
    spy = SpyTranslator()
    doc = _doc(["A", "A"])
    seen = []
    out = translate_document(doc, "en", translator=spy, tm_map={"A": "TM-A"},
                             on_progress=lambda st, k, n: seen.append((st, k, n)))
    assert spy.calls == [] or spy.calls == [[]]
    assert [b.text for b in out.blocks] == ["TM-A", "TM-A"]
    assert ("translate", 1, 1) in seen
```

- [ ] **Step 2: Verify failure. Step 3: Implement** — replace the payload-processing body (keep the surrounding structure; today it computes `llm_pos`/`llm_payload` over `payload`, then `results`):

```python
    if payload:
        translator = translator or get_translator()
        glossary = {**load_glossary(source_lang, target_lang), **(extra_glossary or {})}
        tm_map = tm_map or {}
        # Identical source segments must translate identically: a table repeating
        # 통과/정지 across 14 rows must not drift between GO/NO-GO and PASS/STOP when
        # occurrences land in different chunks. Translate each UNIQUE text once
        # (first-seen order) and fan the result out to every occurrence — consistency
        # by construction, and repeated boilerplate stops costing LLM tokens.
        uniq_index: dict[str, int] = {}
        uniq_texts: list[str] = []
        for seg in payload:
            if seg not in uniq_index:
                uniq_index[seg] = len(uniq_texts)
                uniq_texts.append(seg)
        # Translation-Memory reuse: a segment with a high-confidence approved match skips
        # the LLM entirely (verbatim consistency for recurring boilerplate).
        llm_pos = [k for k, seg in enumerate(uniq_texts) if seg not in tm_map]
        llm_payload = [uniq_texts[k] for k in llm_pos]
        total_chunks = max(1, math.ceil(len(llm_payload) / CHUNK_SIZE)) if llm_payload else 1
        if on_progress:
            on_progress("translate", 0, total_chunks)
        on_done = _chunk_reporter(on_progress, total_chunks) if on_progress else None
        llm_out = translator.translate(
            llm_payload, source_lang, target_lang, glossary,
            on_done=on_done, style_context=style_context,
        ) if llm_payload else []
        uniq_results = list(uniq_texts)
        for k, seg in enumerate(uniq_texts):
            if seg in tm_map:
                uniq_results[k] = tm_map[seg]
        for pos, k in enumerate(llm_pos):
            uniq_results[k] = llm_out[pos]
        for i, seg in zip(idxs, payload):
            _, setter = accessors[i]
            setter(uniq_results[uniq_index[seg]])
        if on_progress and not llm_payload:    # all segments TM-reused → report completion
            on_progress("translate", 1, 1)
```

- [ ] **Step 4: New tests + translator suites pass** (`-k "translat or tm or style"`). Watch for tests asserting the translator receives every occurrence — adapt ONLY if the new unique-send behavior is why they fail (report it). **Step 5: Commit** — `feat(translate): identical source segments translate identically (unique-send + fan-out)`.

---

### Task 2: marker-prefix strip from preserved lines (TDD)

**Files:** Modify `backend/pipeline/reconciler.py` (~lines 441-443, the existing whole-line marker drop); Test `backend/tests/test_marker_lines.py` (create).

- [ ] **Step 1: Failing tests** — drive at the `reconcile()` level like neighboring tests, OR (simpler) extract-and-test the helper below. Cases:
  - first recovered line `"2.2.1.2.2. Profiling of Metabolites…"` with marker `"2.2.1.2.2."` → `lines[0] == "Profiling of Metabolites…"`;
  - first line exactly the marker → line dropped (existing behavior preserved);
  - first line `"2.2.1.2.20 something"` with marker `"2.2.1.2.2."` → untouched (no false prefix: after stripping the marker the next char must be whitespace — note the marker ends with "." so `"2.2.1.2.20…"` does NOT start with `"2.2.1.2.2. "`);
  - no marker → untouched.

- [ ] **Step 2: Verify failure. Step 3: Implement** — replace the whole-line drop with:

```python
                marker = b.get("marker")
                if marker and recovered:
                    mk = marker.strip()
                    first = recovered[0][0].strip()
                    if first == mk:
                        # marker recovered as its own line — drop the duplicate line
                        recovered = recovered[1:]
                    elif mk and first.startswith(mk) and first[len(mk):len(mk) + 1].isspace():
                        # marker embedded at the head of the first line ("2.2.1.2.2. Title…")
                        # — strip the prefix so "{marker} " + lines doesn't render it twice
                        t, *rest = recovered[0]
                        recovered = [(first[len(mk):].lstrip(), *rest)] + recovered[1:]
```

(Read the actual tuple shape of `recovered` items first — `preserved = [t for t, _, _ in recovered]` implies 3-tuples; rebuild accordingly.)

- [ ] **Step 4: Tests + reconcile suites pass. Step 5: Commit** — `fix(toc): strip the marker prefix from preserved first lines (no more "2.2. 2.2." duplication)`.

---

### Task 3: wrapped TOC entries reflow through `_toc_split` (TDD)

**Files:** Modify `backend/renderers/docx_renderer.py` (list-item branch, ~line 163-172); Test `backend/tests/test_toc_multiline.py` (create).

- [ ] **Step 1: Failing tests** — render a `SemanticDocument` with one `ListItemBlock(marker="2.2.1.2.2.", lines=["Profiling of Metabolites of X Generated by", "Hepatocytes from Mouse (MC11M-0047) ................. 8"], text="…")` via `render_docx`, read the docx back (python-docx):
  - exactly ONE paragraph for the entry, containing the marker once;
  - the paragraph XML contains a right-aligned tab stop with a dot leader (match how existing `_toc_split`/`_render_toc` docx tests assert — find and mirror them);
  - the page number "8" is the paragraph's trailing text;
  - control: a non-TOC two-line item still renders with a line break and unchanged text.

- [ ] **Step 2: Verify failure. Step 3: Implement** — in the list-item branch replace `toc = None if block.lines else _toc_split(block.text)` with:

```python
            # A wrapped TOC entry (leader + page number at the END of its LAST line)
            # reads better as one leader row than as broken preserved lines — join and
            # let _toc_split decide; non-TOC multi-line items keep their line breaks.
            toc = _toc_split(" ".join(
                ln.strip() for ln in block.lines if ln.strip()
            )) if block.lines else _toc_split(block.text)
```

and route the `toc` truthy case through the existing `_render_toc` exactly as the single-line path does (read the current branch and keep the non-TOC `lines` path unchanged).

- [ ] **Step 4: Tests + renderer suites pass** (`-k "toc or render"`). **Step 5: Commit** — `fix(toc): wrapped TOC entries reflow as a single leader row`.

---

### Task 4: full-suite + ledger note

- [ ] Full suite `-q` (only pre-existing protected-file failures). Report totals. No commit needed beyond Task 1-3.

## Self-Review
- Spec (a)→T1, (b1)→T2, (b2)→T3. TM interplay covered by tests. `retranslate_one_segment` untouched. Tuple-shape caution flagged for T2; existing-assert mirroring flagged for T3.
