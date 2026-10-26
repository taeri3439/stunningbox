"""Cell-aware native extraction with OCR supplements for images and outlined glyphs.

OCR supplies geometry; native characters remain authoritative where available.
All caches are local to a document and include the algorithm/language version.
"""
import hashlib
import json
import os
import re
import copy
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median

import cv2
import fitz
import numpy as np

VERSION = 16
_DETECTOR = None
ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {"ko": "kor", "en": "eng", "ja": "jpn", "zh": "chi_sim",
             "es": "spa", "fr": "fra", "de": "deu", "vi": "vie"}


def tessdata(language):
    folder = Path(os.environ.get("TESSDATA_PREFIX", str(ROOT / "models" / "tessdata")))
    codes = list(dict.fromkeys([LANGUAGES[language], "eng"]))
    missing = [code for code in codes if not (folder / f"{code}.traineddata").is_file()]
    if missing:
        raise ValueError("OCR 언어 모델이 없습니다: " + ", ".join(missing) + ". setup-ocr.ps1로 설치하세요.")
    return str(folder), "+".join(codes)


def union(rects):
    result = fitz.Rect()
    for rect in rects:
        result |= fitz.Rect(rect)
    return result


def raster(page, dpi=240):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def image_tokens(page, images, ocr_words, overlays=()):
    global _DETECTOR
    if _DETECTOR is None:
        from rapidocr_onnxruntime import RapidOCR
        _DETECTOR=RapidOCR(det_limit_side_len=1800,intra_op_num_threads=2,inter_op_num_threads=2)
    result=[]
    for im in images:
        if im.width<20 or im.height<12:continue
        pix=page.get_pixmap(dpi=220,clip=im,colorspace=fitz.csRGB,alpha=False)
        pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).copy()
        # Suppress colored native overlays only in the detector image. The source
        # and transcription crops keep the watermark; this recovers obscured rows.
        for token in overlays:
            b=fitz.Rect(token['bbox']) & im
            x0=max(0,int((b.x0-im.x0)/im.width*pix.width)); x1=min(pix.width,int((b.x1-im.x0)/im.width*pix.width)+1)
            y0=max(0,int((b.y0-im.y0)/im.height*pix.height)); y1=min(pix.height,int((b.y1-im.y0)/im.height*pix.height)+1)
            patch=pixels[y0:y1,x0:x1]
            if patch.size:
                patch[np.ptp(patch,axis=2)>60]=255
        boxes,_=_DETECTOR(pixels,use_cls=False,use_rec=False)
        for points in boxes or []:
            points=np.asarray(points)
            x0,y0=points.min(axis=0);x1,y1=points.max(axis=0)
            box=fitz.Rect(im.x0+x0/pix.width*im.width,im.y0+y0/pix.height*im.height,
                          im.x0+x1/pix.width*im.width,im.y0+y1/pix.height*im.height)
            words=[t for t in ocr_words if (box & fitz.Rect(t['bbox'])).get_area()>fitz.Rect(t['bbox']).get_area()*.4]
            draft=' '.join(t['text'] for t in sorted(words,key=lambda t:t['bbox'][0])) or '[OCR]'
            sample=pixels[max(0,int(y0)):min(pix.height,int(y1)+1),max(0,int(x0)):min(pix.width,int(x1)+1)].reshape(-1,3)
            bg=np.median(sample,axis=0);dist=np.linalg.norm(sample-bg,axis=1)
            ink=sample[dist>=np.percentile(dist,95)]
            color=np.median(ink,axis=0).astype(int) if len(ink) else np.zeros(3,dtype=int)
            result.append(dict(text=draft,bbox=list(box),origin=[box.x0,box.y1],size=max(3,box.height*.85),font='OCR',
                        flags=0,color=int(color[0])*65536+int(color[1])*256+int(color[2]),native=False,block=200000+len(result),detected=True))
    return result


def grid_mask(image):
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 12)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((1, max(35, image.shape[1] // 40)), np.uint8))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((max(35, image.shape[0] // 50), 1), np.uint8))
    return cv2.bitwise_or(horizontal, vertical)


def cells_of(page, image):
    cells = []
    try:
        found = page.find_tables()
        cells = [fitz.Rect(c) for c in found.cells if c]
        # Open outer borders can leave the last column undetected. Recover only
        # vacant slots aligned with a complete header; never split merged cells.
        for table in found.tables:
            rows = table.rows
            if not rows or any(c is None for c in rows[0].cells):
                continue
            for row in rows[1:]:
                for index, cell in enumerate(row.cells):
                    if cell is not None or index >= len(rows[0].cells):
                        continue
                    header = fitz.Rect(rows[0].cells[index])
                    candidate = fitz.Rect(header.x0, row.bbox[1], header.x1, row.bbox[3])
                    if not any((candidate & old).get_area() > 1 for old in cells):
                        cells.append(candidate)
    except Exception:
        pass
    # Raster grids add cells absent from the PDF drawing stream (scanned tables).
    lines = grid_mask(image)
    contours, hierarchy = cv2.findContours(lines, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    sx, sy = page.rect.width / image.shape[1], page.rect.height / image.shape[0]
    if hierarchy is not None:
        for i, contour in enumerate(contours):
            if hierarchy[0][i][3] < 0:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            rect = fitz.Rect(x*sx, y*sy, (x+w)*sx, (y+h)*sy)
            if w > 18 and h > 14 and cv2.contourArea(contour) > w*h*.8 and rect.get_area() < page.rect.get_area()*.7:
                if not any((rect & old).get_area() / max(1, min(rect.get_area(), old.get_area())) > .85 for old in cells):
                    cells.append(rect)
    # Table finders may call the outer page/figure frame a merged cell.
    cells=[c for c in cells if not any(c.contains(other) and c.get_area()>other.get_area()*1.5 for other in cells)]
    return sorted(cells, key=lambda r: r.get_area())


def make_lines(tokens):
    lines = []
    for token in sorted(tokens, key=lambda t: ((t['bbox'][1]+t['bbox'][3])/2, t['bbox'][0])):
        box = fitz.Rect(token['bbox'])
        match = next((ln for ln in reversed(lines[-12:])
                      if abs((ln['box'].y0+ln['box'].y1)/2-(box.y0+box.y1)/2) < min(ln['box'].height, box.height)*.48), None)
        if match is None:
            lines.append({'box': box, 'tokens': [token]})
        else:
            match['tokens'].append(token)
            match['box'] |= box
    result = []
    for ln in sorted(lines, key=lambda ln: ln['box'].y0):
        ordered = sorted(ln['tokens'], key=lambda t: t['bbox'][0])
        text = ''; previous = None
        for t in ordered:
            if previous is not None and t['bbox'][0]-previous['bbox'][2] > min(t['size'], previous['size'])*.16:
                text += ' '
            text += t['text']
            previous = t
        ln['text'] = text.strip()
        result.append(ln)
    return result


def structured_cell_parts(items, cell):
    """Partition a list/checklist into physical rows and aligned text columns.

    Coordinates come from extraction, never from a language model. A repeated
    wide gutter establishes the checklist column; ordinary wrapped prose stays
    a single cell.
    """
    lines = make_lines(items)
    numbered = sum(bool(re.match(r'^\s*(?:\(?\d{1,2}[.)]|[①-⑳•●▪])', ln['text'])) for ln in lines)
    checks = sum(bool(re.search(r'[☐☑☒□✓✔]|적합|부착', ln['text'])) for ln in lines)
    if numbered < 2 and checks < 3:
        return None
    gaps = []
    for ln in lines:
        tokens = sorted(ln['tokens'], key=lambda t:t['bbox'][0])
        for a,b in zip(tokens,tokens[1:]):
            gap = b['bbox'][0] - a['bbox'][2]
            if gap > max(20, min(a['size'],b['size'])*2.5):
                gaps.append((a['bbox'][2], b['bbox'][0]))
    # A stable vertical gutter, not incidental spaces in a single item.
    candidates = [(a+b)/2 for a,b in gaps]
    gutter = max(candidates, key=lambda x:sum(a<x<b for a,b in gaps), default=None)
    if gutter is not None and sum(a<gutter<b for a,b in gaps)<2:
        gutter = None
    columns = [[t for t in items if (t['bbox'][0]+t['bbox'][2])/2 < gutter],
               [t for t in items if (t['bbox'][0]+t['bbox'][2])/2 >= gutter]] if gutter else [items]
    if gutter and columns[1]:
        gutter = min(t['bbox'][0] for t in columns[1]) - 3
    parts = []
    for col, tokens in enumerate(columns):
        rows = make_lines(tokens)
        if not checks and col == 0:
            # A numbered item is a semantic unit, not one unit per physical
            # line. Only join continuations inside this already-detected cell.
            grouped = []
            for row in rows:
                marker = re.match(r'^\s*(?:\(?\d{1,2}[.)]|[①-⑳•●▪◦\uf09f\uf0a7\uf06e])',row['text'])
                if (grouped and not marker
                        and row['box'].y0-grouped[-1]['box'].y1 < median(t['size'] for t in row['tokens'])*.8
                        and row['box'].x0 >= grouped[-1]['box'].x0-1):
                    prev=grouped[-1]
                    prev['tokens'].extend(row['tokens']); prev['box'] |= row['box']
                    prev['text'] += '\n'+row['text']
                else: grouped.append(dict(row,tokens=list(row['tokens'])))
            rows=grouped
        for index, ln in enumerate(rows):
            ink = ln['box']
            top = cell.y0 if index==0 else (rows[index-1]['box'].y1 + ink.y0)/2
            bottom = cell.y1 if index==len(rows)-1 else (ink.y1 + rows[index+1]['box'].y0)/2
            # Keep each row's original indentation. Grow into its own whitespace.
            right = gutter-2 if gutter and col==0 else cell.x1
            left = max(cell.x0, ink.x0-.5)
            box = fitz.Rect(left, max(top, ink.y0-1), right, bottom) | ink
            parts.append((ln['tokens'], box, 'check_column' if col else 'list_item'))
    return parts


def separate_check_marks(regions, page, image):
    """Retain the ink of a two-choice check column; translate its two labels only.

    Require four clearly separated runs (mark, label, mark, label). Ambiguous
    crops retain the complete column rather than guessing the checked state.
    """
    result=[]
    sx,sy=image.shape[1]/page.rect.width,image.shape[0]/page.rect.height
    for region in regions:
        if region.get('structure') != 'check_column' or not all(s['font']=='OCR' for s in region['spans']):
            result.append(region); continue
        box=fitz.Rect(region['source_bbox'])
        x0,y0=max(0,int(box.x0*sx)),max(0,int(box.y0*sy))
        pixels=image[y0:int(box.y1*sy)+1,x0:int(box.x1*sx)+1].copy()
        pixels[np.ptp(pixels,axis=2)>60]=255
        ink=(pixels.mean(axis=2)<160).astype(np.uint8)
        active=(ink.sum(axis=0)>=2).astype(np.uint8).reshape(1,-1)
        active=cv2.morphologyEx(active,cv2.MORPH_CLOSE,np.ones((1,max(2,round(3.2*sx))),np.uint8))[0]
        edges=np.diff(np.pad(active.astype(int),(1,1)))
        runs=list(zip(np.where(edges==1)[0],np.where(edges==-1)[0]))
        widths=[(b-a)/sx for a,b in runs]
        certain=(len(runs)==4 and 3<widths[0]<box.height*1.1 and 3<widths[2]<box.height*1.1
                 and widths[1]>widths[0]*1.3 and widths[3]>widths[2]*1.3)
        if not certain:
            region['keep_original']=True
            region['structure']='check_column_unresolved'
            region['warnings'].append('체크 표시와 설명을 확실히 분리하지 못해 선택 상태를 원본 그대로 보존했습니다.')
            result.append(region); continue
        for index,(a,b) in enumerate(runs):
            yy,xx=np.where(ink[:,a:b])
            bounds=fitz.Rect((x0+a+int(xx.min()))/sx,(y0+int(yy.min()))/sy,
                             (x0+a+int(xx.max())+1)/sx,(y0+int(yy.max())+1)/sy)
            child=copy.deepcopy(region)
            child.update(source_bbox=list(bounds),mask_boxes=[list(bounds)],
                         bbox=list(bounds+(-.4,-.6,.4,1.2)),display_bbox=list(bounds),
                         font_size=max(3,bounds.height*.9),dominant_color=0,dominant_flags=0,
                         text='[OCR]',spans=[dict(font='OCR',text='[OCR]',bbox=list(bounds),
                         origin=[bounds.x0,bounds.y1],size=max(3,bounds.height*.9),flags=0,color=0,chars=[])])
            if index%2==0:
                child.update(kind='protected_text',keep_original=True,structure='checkbox_mark',text='[CHECK]')
            else:
                # Labels get the free space up to the next mark or cell edge.
                child['bbox'][2]=(x0+runs[index+1][0])/sx-2 if index+1<len(runs) else region['bbox'][2]
                child['bbox'][3]=region['bbox'][3]
                child['structure']='checkbox_label'
            result.append(child)
    return result


def analyze_page(page, language, use_ocr=True):
    if page.rotation:
        raise ValueError("OCR 비교에서는 회전된 페이지를 먼저 정방향으로 저장해 주세요.")
    image = raster(page)
    cells = cells_of(page, image)
    native = []
    existing_redaction=any(a.type[0]==fitz.PDF_ANNOT_REDACT for a in page.annots() or [])
    for bi, block in enumerate(page.get_text('rawdict')['blocks']):
        if block['type'] != 0:
            continue
        for li, line in enumerate(block['lines']):
            for span in line['spans']:
                for ch in span['chars']:
                    if ch['c'].strip():
                        native.append(dict(text=ch['c'], bbox=list(ch['bbox']), origin=list(ch['origin']),
                                           size=span['size'], font=span['font'], flags=span['flags'], color=span['color'],
                                           native=True, block=bi, source_line=(bi,li),
                                           unsupported=(abs(line['dir'][0]-1)>.001 or abs(line['dir'][1])>.001 or span.get('alpha',255)!=255)))
    images = [fitz.Rect(info['bbox']) for info in page.get_image_info()]
    # A frame around a diagram is not one editable text cell. Keep its detected
    # labels independent, and do not merge a native caption with raster labels.
    cells = [cell for cell in cells if not any(
        im.width > 120 and im.height > 60 and im.get_area() < page.rect.get_area() * .75
        and (cell & im).get_area() > im.get_area() * .8
        and cell.get_area() > im.get_area() * .8 for im in images)]
    drawings = page.get_drawings()
    glyphs = [d['rect'] for d in drawings if d['type'] == 'f' and len(d['items']) >= 1
              and 1 < d['rect'].width < 28 and 2 < d['rect'].height < 28]
    supplements = []
    if use_ocr and (images or len(glyphs) > 5 or not native):
        data, codes = tessdata(language)
        tp = page.get_textpage_ocr(language=codes, dpi=240, full=True, tessdata=data)
        for word in page.get_text('words', textpage=tp):
            box = fitz.Rect(word[:4]); text = word[4]
            if native and not any(box.intersects(im) for im in images) and not any(box.intersects(g) for g in glyphs):
                continue
            # A native character is not recognized a second time. OCR only fills holes.
            overlaps = [t for t in native if (fitz.Rect(t['bbox']) & box).get_area() > fitz.Rect(t['bbox']).get_area()*.3]
            if overlaps:
                continue
            if box.width < 1 or box.height < 2 or not text.strip():
                continue
            supplements.append(dict(text=text, bbox=list(box), origin=[box.x0,box.y1], size=box.height*.9,
                                    font='OCR', flags=0, color=0, native=False, block=100000+word[5]*1000+word[6]))
    if use_ocr and images:
        overlays=[t for t in native if t.get('unsupported') and max((t['color']>>s)&255 for s in (16,8,0))-min((t['color']>>s)&255 for s in (16,8,0))>80]
        detected=image_tokens(page,images,supplements,overlays)
        supplements=[t for t in supplements if not any(fitz.Rect(t['bbox']).intersects(im) for im in images)]+detected
    tokens = native + supplements
    groups = {}
    for token in tokens:
        box=fitz.Rect(token['bbox']); center=fitz.Point((box.x0+box.x1)/2,(box.y0+box.y1)/2)
        cell = next((i for i,c in enumerate(cells) if c.contains(center)), None)
        # Rotated/transparent overlays must never contaminate a table cell's body.
        key = (('protected', token['block']) if token.get('unsupported') else
               ('cell',cell,token['native']) if cell is not None else ('block',token['block']))
        groups.setdefault(key, []).append(token)
    # PDF producers split lines by font. Merge non-cell groups which share physical lines.
    keys = [k for k in groups if k[0]=='block']
    for i,key in enumerate(keys):
        if key not in groups: continue
        box=union(t['bbox'] for t in groups[key])
        for other in keys[i+1:]:
            if other not in groups: continue
            ob=union(t['bbox'] for t in groups[other])
            inter=box & ob
            size=median(t['size'] for t in groups[key])
            both_native=all(t['native'] for t in groups[key]+groups[other])
            consecutive=(0<=ob.y0-box.y1<size*.85 and abs(box.x0-ob.x0)<12 and box.width>180 and both_native)
            has_native=any(t['native'] for t in groups[key]+groups[other])
            if inter.get_area()>0 or consecutive or (has_native and abs(box.y0-ob.y0)<3 and min(abs(box.x1-ob.x0),abs(ob.x1-box.x0))<max(3,size*.65)):
                groups[key].extend(groups.pop(other));box |= ob
    structured = {}
    for key, items in list(groups.items()):
        if key[0] != 'cell': continue
        parts = structured_cell_parts(items, cells[key[1]] + (2,2,-2,-2))
        if not parts: continue
        del groups[key]
        for index,(tokens,box,kind) in enumerate(parts):
            child = key + (index,)
            groups[child] = tokens
            structured[child] = (box,kind)
    regions=[]
    for key, items in groups.items():
        lines=make_lines(items)
        text='\n'.join(ln['text'] for ln in lines if ln['text'])
        if not text: continue
        ink=union(t['bbox'] for t in items)
        if key in structured:
            box = structured[key][0]
        elif key[0]=='cell':
            box=cells[key[1]] + (2,2,-2,-2)
            box |= ink  # never clip existing glyphs due to border detection rounding
        else:
            box=fitz.Rect(ink)+(-.5,-1.5,.5,1.5)
        box &= page.rect
        has_ocr=any(not t['native'] for t in items)
        private_use=any(unicodedata.category(c)=='Co' for c in text)
        patch=has_ocr or private_use or any(box.intersects(im) for im in images) or any(box.intersects(g) for g in glyphs)
        spans=[dict(text=t['text'],bbox=t['bbox'],origin=t['origin'],size=t['size'],font=t['font'],flags=t['flags'],
                    color=t['color'],alpha=255,source_block=t.get('block'),source_line=t.get('source_line'),
                    chars=[{'c':t['text'],'bbox':t['bbox'],'origin':t['origin']}]) for t in items]
        dominant=max((t for t in items if t['native']),key=lambda t:t['size'], default=items[0])
        size=median(t['size'] for t in items if t['native']) if any(t['native'] for t in items) else median(t['size'] for t in items)
        protected = key[0] == 'protected'
        colored_overlay = protected and all(
            max((t['color'] >> s) & 255 for s in (16, 8, 0)) -
            min((t['color'] >> s) & 255 for s in (16, 8, 0)) > 80 for t in items)
        warnings=[]
        if existing_redaction:warnings.append('기존 삭제 주석이 있는 페이지는 수정하지 않습니다.')
        if protected and not colored_overlay:warnings.append('회전·세로·투명 텍스트는 원문을 유지합니다.')
        regions.append(dict(id='',page=page.number,text=text,bbox=list(box),display_bbox=list(box),source_bbox=list(ink),
                            kind='protected_text' if protected else 'table_cell' if key[0]=='cell' else 'paragraph',spans=spans,font_size=size,warnings=warnings,
                            patch=patch,ocr=has_ocr or private_use,mask_boxes=[t['bbox'] for t in items]+[list(g) for g in glyphs if box.contains(g)],
                            dominant_color=dominant['color'],dominant_flags=dominant['flags'],
                            keep_original=protected, overlay_safe=colored_overlay))
        if key in structured:
            regions[-1]['structure'] = structured[key][1]
            if structured[key][1]=='list_item' and len(lines)>1:
                regions[-1]['logical_paragraph']=True
    regions = separate_check_marks(regions, page, image)
    regions = merge_image_paragraphs(regions, page, image)
    separators = [d['rect']+(-.3,-.3,.3,.3) for d in drawings if
                  (d['rect'].width > 30 and d['rect'].height < 2) or
                  (d['rect'].height > 30 and d['rect'].width < 2)]
    regions = regroup_prose(regions, barriers=cells + images + separators)
    expand_image_labels(regions, page, image, images)
    regions.sort(key=lambda r:(round(r['bbox'][1],1),r['bbox'][0]))
    for i,r in enumerate(regions): r['id']=f'p{page.number+1}-r{i}'
    partition_regions(regions)
    # Unclassified paragraphs retain their measured width. Empty page space
    # does not imply permission to expand across a table or figure boundary.
    return dict(number=page.number,width=page.rect.width,height=page.rect.height,rotation=page.rotation,
                mediabox=list(page.mediabox),cropbox=list(page.cropbox),regions=regions,
                warnings=[] if regions else ['글자를 찾지 못한 페이지입니다. 수동 확인이 필요합니다.'],
                ocr_regions=sum(r['ocr'] for r in regions),table_cells=sum(r['kind']=='table_cell' for r in regions))


def regroup_prose(regions, barriers=()):
    """PDF producer blocks can end halfway through a sentence or start a heading
    and its following paragraph together. Reconstruct logical native paragraphs
    from measured rows, leaving tables, raster labels and manual layouts alone.
    """
    import copy
    candidates = [r for r in regions if r['kind']=='paragraph' and not r.get('patch')
                  and not r.get('keep_original') and not r.get('warnings') and not r.get('user_geometry')
                  and r['font_size'] >= 9 and any(c.isalpha() for c in r['text'])]
    candidate_ids = {id(r) for r in candidates}
    rows = []
    for region in candidates:
        for row in make_lines(region['spans']):
            rows.append((row,region))
    # A PDF block can contain two columns on the same baseline. A native line
    # boundary alone is insufficient: producers also split a sentence there.
    # Require a repeated gutter for narrow gaps, and reject gaps bridged by
    # nearby full-width prose, before cutting a physical row into columns.
    def column_gutter(row, left, right, size, threshold):
        midpoint = (left+right)/2
        supported = False
        for other,_ in rows:
            distance = abs(other['box'].y0-row['box'].y0)
            if other is row or not size*.5 < distance < size*4:
                continue
            if abs(median(t['size'] for t in other['tokens'])-size) >= .8:
                continue
            ordered = sorted(other['tokens'],key=lambda t:t['bbox'][0])
            if not (other['box'].x0 < left and other['box'].x1 > right):
                continue
            opening = next((b['bbox'][0]-a['bbox'][2] for a,b in zip(ordered,ordered[1:])
                            if a['bbox'][2] < midpoint < b['bbox'][0]),0)
            if opening > threshold:
                supported = True
            else:
                return False
        return supported or right-left > size*3

    split_rows = []
    for row,region in rows:
        ordered = sorted(row['tokens'],key=lambda t:t['bbox'][0])
        size = median(t['size'] for t in ordered)
        gaps = [b['bbox'][0]-a['bbox'][2] for a,b in zip(ordered,ordered[1:])]
        word_gaps = [gap for gap in gaps if size*.16 < gap < size*.6]
        threshold = max(8,size*.8,(median(word_gaps) if word_gaps else size*.25)*2.5)
        chunks = [[]]
        for i,token in enumerate(ordered):
            if i and gaps[i-1] > threshold:
                previous = ordered[i-1]
                different_line = (previous.get('source_line') is not None and
                                  token.get('source_line') is not None and
                                  previous['source_line'] != token['source_line'])
                if different_line and column_gutter(row,previous['bbox'][2],token['bbox'][0],size,threshold):
                    chunks.append([])
            chunks[-1].append(token)
        for chunk in chunks:
            split_rows.extend((part,region) for part in make_lines(chunk))
    rows = split_rows
    rows.sort(key=lambda item:(item[0]['box'].y0,item[0]['box'].x0))
    # Synthetic-font punctuation sometimes has a shifted baseline. Attach it
    # to the overlapping physical row instead of creating a blocking region.
    absorbed = set()
    for index,(row,owner) in enumerate(rows):
        if any(c.isalnum() for c in row['text']):
            continue
        box = row['box']
        targets = [(i,r) for i,(r,_) in enumerate(rows) if i!=index
                   and any(c.isalnum() for c in r['text'])
                   and r['box'].x0 <= box.x0 < r['box'].x1
                   and min(r['box'].y1,box.y1)-max(r['box'].y0,box.y0)>box.height*.25]
        if targets:
            _,target = min(targets,key=lambda item:abs(item[1]['box'].y0-box.y0))
            target['tokens'].extend(row['tokens'])
            target['box'] |= box
            ordered = sorted(target['tokens'],key=lambda t:t['bbox'][0])
            text = ''
            for j,token in enumerate(ordered):
                if j and token['bbox'][0]-ordered[j-1]['bbox'][2] > min(token['size'],ordered[j-1]['size'])*.16:
                    text += ' '
                text += token['text']
            target['text'] = text.strip()
            absorbed.add(index)
    rows = [row for i,row in enumerate(rows) if i not in absorbed]
    # Color changes within black/grey prose are not paragraph boundaries. Keep
    # the actual per-span styles below, rather than discarding that information.
    def row_style(row):
        tokens = row['tokens']
        size = median(t['size'] for t in tokens)
        color = Counter(c for t in tokens for c in [t.get('color',0)]*max(1,len(t['text']))).most_common(1)[0][0]
        bold = sum(len(t['text']) for t in tokens if t.get('flags',0)&16) > sum(len(t['text']) for t in tokens)*.65
        return size, color, bold

    def compatible_color(a, b):
        ca, cb = [[(color >> shift)&255 for shift in (16,8,0)] for color in (a,b)]
        return a == b or (max(ca)-min(ca)<12 and max(cb)-min(cb)<12 and max(ca+cb)<=48)

    marker_pattern = r'^\s*(?:[◦•●▪-]\s|[①-⑳㈎-㈞]|\(?\d+[.)]\s|[가-힣][.)]\s)'
    groups = []
    for row,region in rows:
        marker = re.match(marker_pattern,row['text'])
        matches = []
        # Reading order interleaves left/right columns. Search compatible prior
        # paragraphs instead of joining only the last row in page-wide y order.
        for group_index, group in enumerate(groups) if not marker else []:
            prev,owner = group[-1]
            a,b = prev['box'],row['box']
            gap = b.y0-a.y1
            size,color,bold = row_style(row)
            prev_size,prev_color,prev_bold = row_style(prev)
            same_size = abs(size-prev_size) < .8
            same_style = compatible_color(color,prev_color) and (bold==prev_bold or
                          (a.width>size*25 and b.x0>=a.x0 and not prev['text'].rstrip().endswith(('.',':'))))
            centered = (abs((a.x0+a.x1-b.x0-b.x1)/2)<region['font_size']*.5
                        and a.width>region['font_size']*6 and b.width<a.width*.8)
            overlap = min(a.x1,b.x1)-max(a.x0,b.x0)
            heading = (re.match(r'^\s*(?:\(?\d+[.)]|[가-힣][.)]|[㈎-㈞])\s*',prev['text'])
                       and a.width < 300)
            corridor = a|b
            blocked = any(id(r) not in candidate_ids and (corridor & fitz.Rect(r['bbox'])).get_area()>.5 for r in regions)
            # Include intervening prose and vector/table/image boundaries. A
            # diagonal union must never swallow a third, unrelated text block.
            blocked |= any(other is not row and all(other is not member[0] for member in group)
                           and (corridor & other['box']).get_area() > other['box'].get_area()*.15
                           for other,_ in rows)
            blocked |= any(corridor.intersects(fitz.Rect(boundary)) for boundary in barriers)
            paragraph_break = (prev['text'].rstrip().endswith(('.', '!', '?', '。'))
                               and gap > size*.45)
            attach = (same_size and same_style and not heading and not blocked and not paragraph_break
                      and b.y0>a.y0+size*.5 and -size*.4 <= gap < size*1.1
                      and overlap > min(a.width,b.width)*.6 and (abs(a.x0-b.x0)<30 or centered))
            if attach: matches.append((abs(gap),group_index))
        if matches:
            groups[min(matches)[1]].append((row,region))
        else:
            groups.append([(row,region)])
    result = [r for r in regions if id(r) not in candidate_ids]
    for group in groups:
        r = copy.deepcopy(group[0][1])
        r['spans'] = [s for row,_ in group for s in row['tokens']]
        r['text'] = '\n'.join(row['text'] for row,_ in group)
        r['dominant_color'] = Counter(s.get('color',0) for s in r['spans'] for _ in s['text']).most_common(1)[0][0]
        r['logical_paragraph'] = True
        r['source_fragments'] = [dict(text=row['text'],bbox=list(row['box']),
                                     spans=copy.deepcopy(row['tokens']),
                                     source_region_id=owner.get('id')) for row,owner in group]
        if len(group)>1 and abs(group[0][0]['box'].x0-group[-1][0]['box'].x0)>30:
            r['text_align']='center'
        ink = union(row['box'] for row,_ in group)
        r.update(source_bbox=list(ink),bbox=list(ink+(-.5,-1.5,.5,1.5)),
                 display_bbox=list(ink+(-.5,-1.5,.5,1.5)),mask_boxes=[s['bbox'] for s in r['spans']])
        for key in ('typography','spacing_source_bbox','spacing_source_display_bbox','source_mask_boxes','prose_source_bbox','space_base_bbox','space_plan'):
            r.pop(key,None)
        result.append(r)
    # A cell remains its own boundary even when its physical lines form prose.
    # Lists/check columns retain the structural translation path.
    for r in result:
        if (r['kind']=='table_cell' and not r.get('structure') and not r.get('patch')
                and '\n' in r['text'] and not re.search(r'[☐☑☒□■✓✔]',r['text'])
                and not any(re.match(marker_pattern,line) for line in r['text'].splitlines())):
            r['logical_paragraph'] = True
    return result


def expand_image_labels(regions, page, pixels, images):
    """Let a local diagram label use adjacent blank panel space, never another
    label, icon, connector or a full-page scan's table column."""
    sx,sy = pixels.shape[1]/page.rect.width,pixels.shape[0]/page.rect.height
    for region in regions:
        if region['kind']!='paragraph' or not all(s['font']=='OCR' for s in region['spans']):
            continue
        ink=fitz.Rect(region['source_bbox'])
        image=next((im for im in images if im.contains(ink) and im.get_area()<page.rect.get_area()*.75),None)
        if image is None:
            continue
        region['image_label']=True
        box=fitz.Rect(region['bbox'])
        peers=[fitz.Rect(r['bbox']) for r in regions if r is not region]
        right=min([b.x0-2 for b in peers if b.x0>=box.x1 and min(b.y1,box.y1)>max(b.y0,box.y0)]
                  +[image.x1-2,box.x1+min(100,box.width*1.5)])
        if right<=box.x1:
            continue
        y0,y1=max(0,int(ink.y0*sy)),min(pixels.shape[0],int(ink.y1*sy)+1)
        x0,x1=int(ink.x0*sx),int(ink.x1*sx)
        crop=pixels[y0:y1,x0:x1]
        if not crop.size:
            continue
        # Tight detector boxes contain many antialiased glyph pixels; their
        # median is darker than the blank panel and would stop every expansion.
        bg=np.percentile(crop.reshape(-1,3),85,axis=0)
        start,end=int(box.x1*sx),min(pixels.shape[1],int(right*sx))
        strip=pixels[y0:y1,start:end]
        if not strip.size:
            continue
        occupied=(np.linalg.norm(strip.astype(float)-bg,axis=2)>18).mean(axis=0)>.12
        hits=np.flatnonzero(occupied[:-1]&occupied[1:])
        if len(hits):
            right=min(right,(start+hits[0])/sx-.5)
        region['bbox'][2]=max(box.x1,right)
        region['display_bbox']=list(region['bbox'])
    for region in regions:
        if not region.get('image_label'):
            continue
        box=fitz.Rect(region['bbox']);ink=fitz.Rect(region['source_bbox'])
        im=next((im for im in images if im.contains(ink)),None)
        if im is None:
            continue
        peers=[fitz.Rect(r['bbox']) for r in regions if r is not region]
        bottom=min([b.y0-1 for b in peers if b.y0>=box.y1 and min(b.x1,box.x1)>max(b.x0,box.x0)]
                   +[im.y1-1,box.y1+region['font_size']*1.5])
        x0,x1=max(0,int(box.x0*sx)),min(pixels.shape[1],int(box.x1*sx))
        start,end=int(box.y1*sy),min(pixels.shape[0],int(bottom*sy))
        crop=pixels[max(0,int(ink.y0*sy)):int(ink.y1*sy)+1,int(ink.x0*sx):int(ink.x1*sx)]
        strip=pixels[start:end,x0:x1]
        if not crop.size or not strip.size:
            continue
        bg=np.percentile(crop.reshape(-1,3),85,axis=0)
        occupied=(np.linalg.norm(strip.astype(float)-bg,axis=2)>18).mean(axis=1)>.03
        hits=np.flatnonzero(occupied[:-1]&occupied[1:])
        if len(hits):bottom=min(bottom,(start+hits[0])/sy-.5)
        region['bbox'][3]=max(box.y1,bottom)
        region['display_bbox']=list(region['bbox'])


def partition_regions(regions):
    """Share whitespace between adjacent lines; padding must not overlap."""
    for i, upper in enumerate(regions):
        if upper.get('kind') == 'protected_text': continue
        a = fitz.Rect(upper['source_bbox'])
        for lower in regions[i+1:]:
            if lower.get('kind') == 'protected_text': continue
            b = fitz.Rect(lower['source_bbox'])
            if a.y1 <= b.y0 and min(a.x1,b.x1) > max(a.x0,b.x0):
                boundary = (a.y1+b.y0)/2
                upper['bbox'][3] = min(upper['bbox'][3],boundary)
                lower['bbox'][1] = max(lower['bbox'][1],boundary)
    for region in regions:
        region['display_bbox'] = list(region['bbox'])


def merge_image_paragraphs(regions, page, image):
    """Keep adjacent lines on the same flat diagram panel in one layout unit."""
    sx,sy=image.shape[1]/page.rect.width,image.shape[0]/page.rect.height
    def background(region):
        x0,y0,x1,y1=region['source_bbox']
        pixels=image[max(0,int(y0*sy)):int(y1*sy)+1,max(0,int(x0*sx)):int(x1*sx)+1]
        # Median darkens with text density and falsely splits two rows on the
        # same panel. Estimate the paper from its light background pixels.
        return np.percentile(pixels.reshape(-1,3),85,axis=0)
    def eligible(r):
        return (r['kind']=='paragraph' and not r.get('warnings') and not r.get('keep_original')
                and all(s['font']=='OCR' for s in r['spans']))
    ordered=sorted(regions,key=lambda r:r['source_bbox'][1])
    removed=set()
    for i,region in enumerate(ordered):
        if i in removed or not eligible(region):continue
        bg=background(region)
        for j in range(i+1,len(ordered)):
            other=ordered[j]
            if j in removed or not eligible(other):continue
            a,b=fitz.Rect(region['source_bbox']),fitz.Rect(other['source_bbox'])
            gap=b.y0-a.y1
            if gap<0 or gap>min(region['font_size'],other['font_size'])*.65:continue
            if min(a.x1,b.x1)-max(a.x0,b.x0)<min(a.width,b.width)*.65:continue
            if max(region['font_size'],other['font_size'])/min(region['font_size'],other['font_size'])>1.35:continue
            if np.linalg.norm(bg-background(other))>18:continue
            c1=np.array([(region['dominant_color']>>s)&255 for s in [16,8,0]])
            c2=np.array([(other['dominant_color']>>s)&255 for s in [16,8,0]])
            if np.linalg.norm(c1-c2)>45:continue
            combined=a|b
            # No other column or caption may occupy the merged rectangle.
            if any(k not in removed and k not in [i,j] and
                   (combined & fitz.Rect(r['source_bbox'])).get_area()>.5
                   for k,r in enumerate(ordered)):continue
            region['source_bbox']=list(combined)
            region['bbox']=list(fitz.Rect(region['bbox'])|fitz.Rect(other['bbox']))
            region['text']+='\n'+other['text']
            region['logical_paragraph']=True
            region['spans'].extend(other['spans'])
            region['mask_boxes'].extend(other['mask_boxes'])
            region['warnings'].extend(other['warnings'])
            removed.add(j)
    return [r for i,r in enumerate(ordered) if i not in removed]


def prepare(source, cache_dir, language, progress=None, lock=None):
    from contextlib import nullcontext
    cache_dir.mkdir(exist_ok=True)
    digest=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    key=f'layout-v{VERSION}-{language}-{digest[:16]}.json'
    cache=cache_dir/key
    if cache.exists():return json.loads(cache.read_text('utf-8'))
    pages=[]
    with (lock or nullcontext()), fitz.open(source) as doc: count=len(doc)
    for i in range(count):
        if progress:progress(i,count)
        page_cache=cache_dir/f'{key}-page{i}.json'
        if page_cache.exists():
            page=json.loads(page_cache.read_text('utf-8'))
        else:
            with (lock or nullcontext()), fitz.open(source) as doc:
                page=analyze_page(doc[i],language)
            page_cache.write_text(json.dumps(page,ensure_ascii=False),encoding='utf-8')
        pages.append(page)
    result=dict(source_hash=digest,schema_version=VERSION,pages=pages,region_count=sum(len(p['regions']) for p in pages))
    cache.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result


def cleaned_patch(page, region, overlays=()):
    """Erase native + raster/outlined ink inside known word boxes, preserving grid lines.

    Only the affected region is rasterized. Photos/gradients remain a review case.
    """
    zoom=240/72
    rect=fitz.Rect(region['bbox'])
    pix=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=rect,colorspace=fitz.csRGB,alpha=False)
    image=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).copy()
    mask=np.zeros(image.shape[:2],np.uint8)
    for b in region.get('mask_boxes',[region.get('source_bbox',region['bbox'])]):
        x0=max(0,int(b[0]*zoom-pix.x)-2);y0=max(0,int(b[1]*zoom-pix.y)-2)
        x1=min(pix.width,int(b[2]*zoom-pix.x)+3);y1=min(pix.height,int(b[3]*zoom-pix.y)+3)
        if x1>x0 and y1>y0:mask[y0:y1,x0:x1]=255
    lines=grid_mask(image)
    # Retain only long grid strokes; glyph stems are not table borders.
    lines=cv2.bitwise_or(cv2.morphologyEx(lines,cv2.MORPH_OPEN,np.ones((1,80),np.uint8)),
                        cv2.morphologyEx(lines,cv2.MORPH_OPEN,np.ones((80,1),np.uint8)))
    mask[cv2.dilate(lines,np.ones((2,2),np.uint8))>0]=0
    # Flat document backgrounds are reconstructed from their dominant colour.
    # Inpainting whole text rectangles can drag table borders into grey smears.
    cleaned=image.copy()
    background=np.median(image.reshape(-1,3),axis=0).astype(np.uint8)
    distance=np.linalg.norm(image.astype(float)-background.astype(float),axis=2)
    if np.mean(distance<35)<.5:
        raise ValueError('사진/복잡한 배경의 글자 영역입니다. 배경 손상을 막기 위해 원문을 유지했습니다.')
    cleaned[mask>0]=background
    # Preserve colored vector watermark pixels where they cross a raster text patch.
    # Use each glyph's bounds AND its color ray (including antialias/opacity), not
    # the large rotated block rectangle, which would preserve the untranslated body.
    for overlay in overlays:
        for span in overlay['spans']:
            color = np.array([(span['color'] >> s) & 255 for s in (16, 8, 0)], dtype=float)
            direction = color - background.astype(float)
            norm = float(direction @ direction)
            if norm < 1: continue
            b = span['bbox']
            x0=max(0,int(b[0]*zoom-pix.x)-2); y0=max(0,int(b[1]*zoom-pix.y)-2)
            x1=min(pix.width,int(b[2]*zoom-pix.x)+3); y1=min(pix.height,int(b[3]*zoom-pix.y)+3)
            if x1<=x0 or y1<=y0: continue
            pixels=image[y0:y1,x0:x1].astype(float)
            delta=pixels-background.astype(float)
            alpha=np.clip((delta @ direction)/norm,0,1)
            distance=np.linalg.norm(delta-alpha[...,None]*direction,axis=2)
            preserve=(distance<35) & (alpha>.08) & (np.ptp(pixels,axis=2)>30)
            cleaned[y0:y1,x0:x1][preserve]=image[y0:y1,x0:x1][preserve]
    # PNG uses BGR in OpenCV.
    ok, encoded=cv2.imencode('.png',cv2.cvtColor(cleaned,cv2.COLOR_RGB2BGR))
    if not ok:raise ValueError('이미지 원문 제거 결과를 저장할 수 없습니다.')
    return encoded.tobytes()
