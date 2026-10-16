"""Preflight placement on scratch pages; never mutate the original here."""
import html
import re

import fitz

from .models import RenderRequest


def rgb(value: int) -> tuple:
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


BASE_FONTS = {
    "Helvetica": "helv", "Helvetica-Bold": "hebo", "Helvetica-Oblique": "heit",
    "Helvetica-BoldOblique": "hebi", "Times-Roman": "tiro", "Times-Bold": "tibo",
    "Times-Italic": "tiit", "Times-BoldItalic": "tibi", "Courier": "cour",
    "Courier-Bold": "cobo", "Courier-Oblique": "coit", "Courier-BoldOblique": "cobi",
}


def font_key(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name).replace(" ", "")


class FontResolver:
    def __init__(self, source):
        self.source = source
        self.cache = {}

    def resolve(self, page, span):
        key = (page.number, span["font"])
        if key not in self.cache:
            found = None
            for entry in page.get_fonts(full=True):
                if font_key(entry[3]) != font_key(span["font"]):
                    continue
                try:
                    _, _, _, buf = self.source.extract_font(entry[0])
                    if buf:
                        found = (f"LabFont{entry[0]}", buf, fitz.Font(fontbuffer=buf))
                        break
                except Exception:
                    continue
            if found is None and span["font"] in BASE_FONTS:
                name = BASE_FONTS[span["font"]]
                found = (name, None, fitz.Font(name))
            if found is None:
                raise ValueError(f"원본 폰트를 재사용할 수 없습니다: {span['font']}")
            self.cache[key] = found
        result = self.cache[key]
        if any(not result[2].has_glyph(ord(c)) for c in span["text"] if not c.isspace()):
            raise ValueError(f"원본 폰트의 문자 매핑을 재사용할 수 없습니다: {span['font']}")
        return result


def replay(page, region, source_page, fonts):
    # Character origins retain kerning / letter spacing rather than approximating a run.
    for span in region["spans"]:
        name, buf, _ = fonts.resolve(source_page, span)
        if buf:
            page.insert_font(fontname=name, fontbuffer=buf)
        for char in span["chars"]:
            if not char["c"].isspace():
                page.insert_text(char["origin"], char["c"], fontsize=span["size"],
                                 fontname=name, color=rgb(span["color"]))


def fit(page, region, text, policy: RenderRequest):
    if 'source_bbox' in region:
        return fit_region(page, region, text, policy)
    size = region["font_size"]
    if size < policy.min_font_size:
        raise ValueError("원본 글자 크기가 설정한 최소 크기보다 작습니다.")
    lower = max(policy.min_scale, policy.min_font_size / size)
    styles = {(s["font"], round(s["size"], 2), s["color"], s["flags"]) for s in region["spans"]}
    if len(styles) > 1:
        raise ValueError("혼합 스타일 영역의 교체는 아직 지원하지 않습니다. REPLAY로 확인하세요.")
    span = region["spans"][0]
    weight = "bold" if span["flags"] & 16 else "normal"
    italic = "italic" if span["flags"] & 2 else "normal"
    # MuPDF Story's bundled fonts handle CJK fallback. No external HTML/resources allowed.
    css = (f"* {{font-family: sans-serif; font-size: {size}pt; color: #{span['color']:06x};"
           f"font-weight:{weight};font-style:{italic};line-height:{policy.line_height};}}"
           "body {margin:0;padding:0;} p {margin:0;padding:0;}")
    content = "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
    spare, scale = page.insert_htmlbox(fitz.Rect(region["bbox"]), content, css=css,
                                     scale_low=lower)
    if spare < 0:
        raise ValueError("허용한 글자 크기·줄 간격으로 영역 안에 들어가지 않습니다.")
    return scale


def fit_region(page, region, text, policy):
    size = region['font_size']
    floor = min(size, policy.min_font_size)
    lower = max(policy.min_scale, floor / size)
    color = region.get('dominant_color', 0)
    flags = region.get('dominant_flags', 0)
    weight = 'bold' if flags & 16 else 'normal'
    css = (f'* {{font-family:sans-serif;font-size:{size}pt;color:#{color:06x};'
           f'font-weight:{weight};line-height:{policy.line_height};}}'
           'body,p {margin:0;padding:0;}')
    content='<p>'+html.escape(' '.join(text.split()))+'</p>'
    box=fitz.Rect(region['bbox']) + (.3,.6,-.3,-.3)
    if box.is_empty:
        raise ValueError('텍스트를 배치할 내부 여백이 없습니다.')
    spare,scale=page.insert_htmlbox(box,content,css=css,scale_low=lower)
    if spare<0:
        raise ValueError('셀/문단 경계 안에 번역문을 넣을 수 없습니다. 최소 글자 크기를 확인하세요.')
    return scale
