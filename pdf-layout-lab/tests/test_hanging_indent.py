import fitz
import pytest

from engine.models import RenderRequest, normalized
from engine.typesetter import place_part, text_lines


def render_part(text, **options):
    part = dict(text=text, font_size=12, flags=0, color=0xff0000,
                align='left', bbox=[40, 40, 240, 400], **options)
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=500)
    assert place_part(page, part, RenderRequest(line_height=1.2), fixed=True) == 1
    return pdf


def characters(line):
    return [c for span in line['spans'] for c in span['chars']]


def raw_lines(page):
    return [line for b in page.get_text('rawdict')['blocks'] if b['type'] == 0 for line in b['lines']]


@pytest.mark.parametrize('marker', ['-', '•', '◦', '▪', '※', '-  '])
def test_wraps_align_with_first_body_without_changing_styles_or_first_line(marker):
    text = marker + ' A long paragraph with enough words to wrap across several lines inside the original column.'
    with render_part(text) as current, render_part(text, hanging_indent=False) as previous:
        lines, old = raw_lines(current[0]), raw_lines(previous[0])
        first = characters(lines[0])
        body = next(c for c in first if c['c'] == 'A')
        assert len(lines) >= 3
        assert first[0]['origin'] == pytest.approx(characters(old[0])[0]['origin'], abs=.001)
        assert body['origin'] == pytest.approx(next(c for c in characters(old[0]) if c['c'] == 'A')['origin'], abs=.001)
        for line in lines[1:]:
            assert characters(line)[0]['origin'][0] == pytest.approx(body['origin'][0], abs=.001)
        for line in lines:
            for span in line['spans']:
                assert span['size'] == pytest.approx(12, abs=.001)
                assert span['color'] == 0xff0000
        assert normalized(current[0].get_text()) == normalized(text)
        assert lines[1]['spans'][0]['origin'][1] - lines[0]['spans'][0]['origin'][1] == pytest.approx(14.4, abs=.001)


def test_explicit_enter_continues_indent_blank_line_and_new_bullet_reset_it():
    text = '- First item\nManual continuation\n\nOrdinary paragraph\n  ▪ Nested item\nContinued nested body'
    with render_part(text) as pdf:
        lines = raw_lines(pdf[0])
        starts = {''.join(c['c'] for c in characters(l)): characters(l)[0]['origin'] for l in lines}
        first_body = next(c['origin'][0] for c in characters(lines[0]) if c['c'] == 'F')
        nested_body = next(c['origin'][0] for c in characters(lines[-2]) if c['c'] == 'N')
        assert starts['Manual continuation'][0] == pytest.approx(first_body, abs=.001)
        assert starts['Ordinary paragraph'][0] == 40
        assert starts['Continued nested body'][0] == pytest.approx(nested_body, abs=.001)
        assert normalized(pdf[0].get_text()) == normalized(text)


def test_mixed_bold_prefix_and_regular_body_survive_reflow():
    text = '- Important lead sentence. A longer regular explanation continues onto several lines with a consistent body start.'
    with render_part(text, bold_prefix=len('- Important lead sentence.')) as pdf:
        lines = raw_lines(pdf[0])
        body_x = next(c['origin'][0] for c in characters(lines[0]) if c['c'] == 'I')
        assert all(characters(l)[0]['origin'][0] == pytest.approx(body_x, abs=.001) for l in lines[1:])
        spans = [s for l in text_lines(pdf[0]) for s in l['spans']]
        assert any(s['flags'] & 16 and 'Important' in s['text'] for s in spans)
        assert any(not s['flags'] & 16 and 'regular' in s['text'] for s in spans)
        assert normalized(pdf[0].get_text()) == normalized(text)


@pytest.mark.parametrize('text', ['- 18 -', '-42 is a number, not a bullet', 'An ordinary paragraph with long words that should keep its existing wrapping and alignment.'])
def test_non_lists_are_unchanged(text):
    with render_part(text) as new, render_part(text, hanging_indent=False) as old:
        assert new[0].get_pixmap().samples == old[0].get_pixmap().samples


def test_hanging_indent_survives_the_full_native_pdf_pipeline(tmp_path):
    from engine.enhanced import analyze_page
    from engine.service import render
    source = tmp_path / 'source.pdf'
    with fitz.open() as pdf:
        p = pdf.new_page(width=320, height=300)
        p.insert_text((40, 40), '- Original item', fontsize=12)
        p.draw_rect((40, 130, 280, 220))
        model = analyze_page(p, 'en', use_ocr=False)
        pdf.save(source)
    region = next(r for r in model['regions'] if 'Original' in r['text'])
    text = '- Translated item with several words that wrap to a second line.'
    report = render(source, tmp_path/'out.pdf', {'pages':[model]},
                    RenderRequest(mode='fit', replacements={region['id']:text}))
    result = next(r for r in report['regions'] if r['id']==region['id'])
    assert result['text_check'] and result['status'] in ('FIT', 'ADJUSTED')
    with fitz.open(tmp_path/'out.pdf') as pdf:
        lines = raw_lines(pdf[0])
        body_x = next(c['origin'][0] for c in characters(lines[0]) if c['c']=='T')
        assert len(lines)>1
        assert all(characters(l)[0]['origin'][0] == pytest.approx(body_x,abs=.01) for l in lines[1:])
        assert normalized(pdf[0].get_text()) == normalized(text)
