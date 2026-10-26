"""Preflight placement on scratch pages; never mutate the original here."""
import html
import re
from collections import defaultdict
from functools import lru_cache
from statistics import median

import fitz

from .models import RenderRequest


def rgb(value: int) -> tuple:
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


LIST_START = re.compile(r'^([-•◦▪●○■※‣–—][ \t\u00a0]+)(?=\S)')


def list_prefix(text):
    """Only paragraph-leading list markers; exclude numeric page footers."""
    if re.fullmatch(r'\s*[-–—]\s*\d+\s*[-–—]\s*', text):
        return None
    return LIST_START.match(text)


@lru_cache(maxsize=512)
def marker_advance(prefix, size, flags):
    """Measure the body start with the actual Story font, including symbol fallback.

    Using the existing first-line advance keeps its marker/body positions intact.
    The paragraph's negative text-indent then aligns every wrapped line there.
    """
    weight = 'bold' if flags & 16 else 'normal'
    italic = 'italic' if flags & 2 else 'normal'
    with fitz.open() as scratch:
        page = scratch.new_page(width=500, height=100)
        escaped_prefix = re.sub(r' {2,}', lambda m: '&#160;' * (len(m[0]) - 1) + ' ', html.escape(prefix))
        page.insert_htmlbox((0, 0, 500, 100), '<p>' + escaped_prefix + 'M</p>',
            css=f'* {{font-family:sans-serif;font-size:{size}pt;font-weight:{weight};'
                f'font-style:{italic};}} body,p {{margin:0;padding:0;}}', scale_low=1)
        chars = [c for b in page.get_text('rawdict')['blocks'] if b['type'] == 0
                 for line in b['lines'] for span in line['spans'] for c in span['chars']]
        return next(c['origin'][0] for c in reversed(chars) if c['c'] == 'M')


def text_html(text, size, flags=0, bold_prefix=0, hanging=True):
    """Real paragraph indents, with explicit newlines and inline emphasis intact."""
    paragraphs = []
    active_hang = 0
    active_indent = 0
    offset = 0
    for line in text.replace('\r\n', '\n').replace('\r', '\n').expandtabs(4).split('\n'):
        indent = len(line) - len(line.lstrip(' '))
        content = line[indent:]
        marker = list_prefix(content) if hanging else None
        if not content.strip():
            active_hang = 0
            active_indent = 0
        if marker:
            marker_flags = flags | (16 if bold_prefix > offset + indent else 0)
            active_hang = marker_advance(marker[1], size, marker_flags)
            active_indent = indent * size * .33
        def escaped(value):
            return re.sub(r' {2,}', lambda m: '&#160;' * (len(m[0]) - 1) + ' ', html.escape(value))
        end = max(0, min(len(content), bold_prefix - offset - indent))
        body = (('<b style="font-weight:bold">' + escaped(content[:end]) + '</b>') if end else '') + escaped(content[end:])
        # No alignment characters are inserted into the stored or rendered text.
        # Only the first line outdents; a user's continuation Enter keeps the body indent.
        padding = max(indent * size * .33, active_indent) + active_hang
        outdent = -active_hang if marker else 0
        paragraphs.append(f'<p style="padding-left:{padding:.4f}pt;text-indent:{outdent:.4f}pt">{body or "<br>"}</p>')
        offset += len(line) + 1
    return ''.join(paragraphs)


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
    content = text_html(text, size, span['flags'])
    spare, scale = page.insert_htmlbox(fitz.Rect(region["bbox"]), content, css=css,
                                     scale_low=lower)
    if spare < 0:
        raise ValueError("허용한 글자 크기·줄 간격으로 영역 안에 들어가지 않습니다.")
    return scale


def fit_region(page, region, text, policy):
    if (policy.preserve_spacing and region.get('typography', {}).get('parts')
            and not region['typography'].get('spacing_relaxed')):
        return fit_parts(page, region, policy)
    size = region['font_size']
    floor = min(size, policy.min_font_size)
    lower = max(policy.min_scale, floor / size)
    color = region.get('dominant_color', 0)
    flags = region.get('dominant_flags', 0)
    weight = 'bold' if flags & 16 else 'normal'
    css = (f'* {{font-family:sans-serif;font-size:{size}pt;color:#{color:06x};'
           f'font-weight:{weight};line-height:{policy.line_height};}}'
           'body,p {margin:0;padding:0;}')
    content = text_html(text, size, flags)
    box=fitz.Rect(region['bbox']) + (.3,.6,-.3,-.3)
    if box.is_empty:
        raise ValueError('텍스트를 배치할 내부 여백이 없습니다.')
    spare,scale=page.insert_htmlbox(box,content,css=css,scale_low=lower)
    if spare<0:
        raise ValueError('셀/문단 경계 안에 번역문을 넣을 수 없습니다. 최소 글자 크기를 확인하세요.')
    return scale


def part_css(part, size, policy):
    weight = 'bold' if part['flags'] & 16 else 'normal'
    italic = 'italic' if part['flags'] & 2 else 'normal'
    return (f'* {{font-family:sans-serif;font-size:{size}pt;color:#{part["color"]:06x};'
            f'font-weight:{weight};font-style:{italic};line-height:{policy.line_height};}}'
            f'body,p {{margin:0;padding:0;text-align:{part["align"]};}}')


def place_part(page, part, policy, fixed=False):
    if part.get('line_height'):
        policy = policy.model_copy(update={'line_height':part['line_height']})
    size = part['font_size']
    if part.get('layout_error'):
        raise ValueError(part['layout_error'])
    floor = max(policy.min_font_size, part.get('readable_floor', 0))
    lower = 1 if fixed else max(policy.min_scale, min(size, floor)/size)
    box = fitz.Rect(part['bbox'])
    if part.get('rotate') == 90 and part.get('rotation_lines'):
        # The virtual line's height becomes its physical width after rotation.
        center = (box.x0+box.x1)/2
        width = min(box.width, size*policy.line_height*part['rotation_lines']+1)
        box.x0, box.x1 = center-width/2, center+width/2
    elif part.get('center_y') and fixed:
        ink = fitz.Rect(part['ink_bbox'])
        desired = (ink.y0 + ink.y1)/2 - size*policy.line_height/2
        box.y0 = max(box.y0, min(desired, box.y1-size*policy.line_height-.2))
    if box.is_empty:
        raise ValueError('원문의 여백을 유지할 배치 공간이 부족합니다.')
    render_text=part['text']
    if part.get('wrap_long_words'):
        # Story has no overflow-wrap support. Zero-width break opportunities
        # wrap long Latin words without inserting visible hyphens or changing
        # the stored translation. Extraction whitespace is checked afterward.
        render_text=re.sub(r'[A-Za-z]{8,}',lambda m:'\u200b'.join(m[0])
                           if measured_width(m[0],size,part['flags'],policy.line_height)>box.width else m[0],render_text)
    content = text_html(part['text'] if part.get('bold_prefix') else render_text, size,
                        part['flags'], part.get('bold_prefix', 0),
                        part.get('hanging_indent', True) and part['align'] in ('left', 'justify'))
    if part.get('single_line'):
        content = '<div style="white-space:nowrap">' + content + '</div>'
    spare, scale = page.insert_htmlbox(box,content,css=part_css(part,size,policy),scale_low=lower,
                                     rotate=part.get('rotate',0))
    if spare < 0:
        raise ValueError('원문의 행 위치·여백 안에 번역문을 넣을 수 없습니다. 교체문 길이를 확인하세요.')
    return scale


def leader_parts(part):
    if part.get('leader_layout'):
        return part['leader_layout']
    label, value = part['leader']
    left = {**part, 'text':label, 'bbox':list(part['bbox']), 'align':'left'}
    right = {**part, 'text':value, 'bbox':list(part['bbox']), 'align':'right'}
    left['bbox'][2] = part['value_left'] - 3
    right['bbox'][0] = part['value_left'] - 1
    return [left,right]


def render_units(text):
    # Compatibility unit glyphs can have broken character maps in CJK fallback
    # fonts. Equivalent superscript Latin units keep text searchable/editable.
    return text.replace('㎥','m³').replace('㎤','cm³').replace('℃','°C')


def text_lines(page):
    return [line for block in page.get_text('dict')['blocks'] if block['type']==0
            for line in block['lines']]


@lru_cache(maxsize=2048)
def measured_width(text, size, flags, line_height):
    """Use the same Story font metrics as final placement, including fallback."""
    policy = RenderRequest(line_height=line_height)
    part = dict(text=render_units(text), font_size=size, flags=flags, color=0,
                align='left', bbox=[10,10,2000,500], single_line=True)
    with fitz.open() as scratch:
        page = scratch.new_page(width=2100,height=600)
        place_part(page,part,policy,fixed=True)
        return max((line['bbox'][2]-10 for line in text_lines(page)),default=0)


def plan_leader_row(part, value_left, size, policy):
    """Shorten the leader, wrap only the label, and align the value to its last line."""
    label, value = part['leader']
    box = list(part['bbox'])
    common = dict(font_size=size, flags=part['flags'], color=part['color'], center_y=False)
    left = dict(common, text=label, align='left', bbox=[box[0],box[1],value_left-5,box[3]], single_line=False)
    right = dict(common, text=render_units(value), align='right',
                 bbox=[value_left,box[1],box[2],box[3]], single_line=True)
    with fitz.open() as scratch:
        width, height = max(1000,box[2]+10), max(1400,box[3]+10)
        page = scratch.new_page(width=width,height=height)
        place_part(page,left,policy,fixed=True)
        left_lines = text_lines(page)
        if not left_lines:
            raise ValueError('항목명을 읽을 수 있는 크기로 배치하지 못했습니다.')
        last = left_lines[-1]
        baseline = last['spans'][-1]['origin'][1]
        # Measure the value independently so CJK/superscript metrics do not
        # move the numeric baseline away from its label.
        value_page = scratch.new_page(width=width,height=height)
        place_part(value_page,right,policy,fixed=True)
        first = text_lines(value_page)[0]
        right['bbox'][1] += baseline-first['spans'][0]['origin'][1]
        value_page = scratch.new_page(width=width,height=height)
        place_part(value_page,right,policy,fixed=True)
        value_line = text_lines(value_page)[0]
        start, end = last['bbox'][2]+3, value_line['bbox'][0]-3
        leader = [start,baseline-size*.12,end] if end-start>8 else None
    part.update(font_size=size, fixed_size=True, adaptive_leader=True,
                value_left=value_left, leader_layout=[left,right], leader_line=leader)


def prepare_leader_rows(region, policy):
    rows = [p for p in region['typography']['parts'] if p.get('leader')]
    if not rows:
        return
    desired = median(p['font_size'] for p in rows)
    floor = min(desired,max(8,policy.min_font_size,desired*policy.min_scale))
    # One right edge per group. Only the amount column expands into dotted space;
    # the original label start and the next source row remain the outer limits.
    right_edge = min(region['bbox'][2]-2,max(p['ink_bbox'][2] for p in rows))
    try:
        amount_width = max(measured_width(p['leader'][1],desired,p['flags'],policy.line_height)+2 for p in rows)
    except ValueError:
        for part in rows:
            part.update(adaptive_leader=True, layout_error='수량 텍스트가 너무 길어 읽기 가능한 크기로 배치할 수 없습니다.')
        return
    value_left = min(min(p['value_left']-1 for p in rows),right_edge-amount_width)
    for part in rows:
        part['bbox'][2] = right_edge
        part['adaptive_leader'] = True
        # A long label must not shrink every other row. Try the common source
        # size first, allowing a modest local reduction with a readability floor.
        sizes = sorted({desired, max(floor,desired*.95), max(floor,desired*.9), floor},reverse=True)
        for size in sizes:
            try:
                if value_left-part['bbox'][0] < size*3:
                    raise ValueError('항목명과 수량에 필요한 폭이 셀 너비를 초과합니다.')
                plan_leader_row(part,value_left,size,policy)
                break
            except ValueError:
                continue
        else:
            part['layout_error'] = '점선·수량 칸 조정과 줄바꿈 후에도 읽기 가능한 글자 크기로 배치할 수 없습니다.'


def prepare_repeated_sizes(layout, policy):
    """One size per repeated header, determined by the narrowest peer's real fit."""
    from .placement_quality import eligible
    groups=defaultdict(list)
    for page in layout['pages']:
        prose_groups = defaultdict(list)
        for region in page['regions']:
            if region.get('typography'):
                prepare_leader_rows(region,policy)
            for part in region.get('typography',{}).get('parts',[]):
                if part.get('repeat_group'):groups[part['repeat_group']].append(part)
                elif part.get('prose') and not eligible(region):
                    prose_groups[round(part['font_size']*2)/2].append(part)
                if part.get('rotate') == 90:
                    try:
                        with fitz.open() as scratch:
                            probe = scratch.new_page(width=page['width'],height=page['height'])
                            scale = place_part(probe,part,policy)
                            part.update(font_size=part['font_size']*scale, fixed_size=True,
                                        rotation_lines=len(text_lines(probe)))
                    except ValueError:
                        part['layout_error'] = '세로 항목을 90° 회전·줄바꿈해도 허용한 글자 크기로 배치할 공간이 부족합니다.'
        for members in prose_groups.values():
            fitted = []
            for part in members:
                part['readable_floor'] = max(policy.min_font_size, part['font_size']*.7)
                try:
                    with fitz.open() as scratch:
                        probe = scratch.new_page(width=page['width'],height=page['height'])
                        fitted.append((part,part['font_size']*place_part(probe,part,policy)))
                except ValueError:
                    part['layout_error'] = '본문의 일관된 글자 크기를 유지할 공간이 부족해 원문을 보존했습니다. 교체문을 줄이거나 영역을 조정하세요.'
            if fitted:
                common = min(size for _,size in fitted)*.998
                for part,_ in fitted:
                    part.update(font_size=common, fixed_size=True)
    for members in groups.values():
        sizes=[]
        try:
            for part in members:
                with fitz.open() as scratch:
                    page=scratch.new_page(width=max(1000,part['bbox'][2]+10),height=max(1400,part['bbox'][3]+10))
                    for item in leader_parts(part) if part.get('leader') else [part]:
                        sizes.append(item['font_size']*place_part(page,item,policy))
            common=min(sizes)*.998  # Avoid rounding-induced reflow at an exact boundary.
            for part in members:
                part['font_size']=common
                part['fixed_size']=True
        except ValueError:
            # A problematic peer cannot force every other header below the floor.
            # Its regular per-region preflight will report the unresolved fit.
            for part in members:part['fixed_size']=False


def fit_parts(page, region, policy):
    sizes=[]
    for part in region['typography']['parts']:
        if part.get('layout_error'):
            raise ValueError(part['layout_error'])
        if part.get('leader'):
            left,right=leader_parts(part)
            sizes.extend(item['font_size']*place_part(page,item,policy,part.get('fixed_size',False)) for item in [left,right])
            # Leaders are decoration. Semantic checks use the label and value.
            if part.get('leader_line'):
                start,y,end=part['leader_line']
                page.draw_line((start,y),(end,y),color=rgb(part['color']),width=.5,dashes='[.6 2] 0')
        else:
            sizes.append(part['font_size']*place_part(page,part,policy,part.get('fixed_size',False)))
    return min(sizes)/region['font_size']
