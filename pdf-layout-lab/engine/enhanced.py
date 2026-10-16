"""Cell-aware native extraction with OCR supplements for images and outlined glyphs.

OCR supplies geometry; native characters remain authoritative where available.
All caches are local to a document and include the algorithm/language version.
"""
import hashlib
import json
import os
import unicodedata
from pathlib import Path
from statistics import median

import cv2
import fitz
import numpy as np

VERSION = 9
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


def image_tokens(page, images, ocr_words):
    global _DETECTOR
    if _DETECTOR is None:
        from rapidocr_onnxruntime import RapidOCR
        _DETECTOR=RapidOCR(det_limit_side_len=1800,intra_op_num_threads=2,inter_op_num_threads=2)
    result=[]
    for im in images:
        if im.width<20 or im.height<12:continue
        pix=page.get_pixmap(dpi=220,clip=im,colorspace=fitz.csRGB,alpha=False)
        pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
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
        for line in block['lines']:
            for span in line['spans']:
                for ch in span['chars']:
                    if ch['c'].strip():
                        native.append(dict(text=ch['c'], bbox=list(ch['bbox']), origin=list(ch['origin']),
                                           size=span['size'], font=span['font'], flags=span['flags'], color=span['color'],
                                           native=True, block=bi,
                                           unsupported=(abs(line['dir'][0]-1)>.001 or abs(line['dir'][1])>.001 or span.get('alpha',255)!=255)))
    images = [fitz.Rect(info['bbox']) for info in page.get_image_info()]
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
        detected=image_tokens(page,images,supplements)
        supplements=[t for t in supplements if not any(fitz.Rect(t['bbox']).intersects(im) for im in images)]+detected
    tokens = native + supplements
    groups = {}
    for token in tokens:
        box=fitz.Rect(token['bbox']); center=fitz.Point((box.x0+box.x1)/2,(box.y0+box.y1)/2)
        cell = next((i for i,c in enumerate(cells) if c.contains(center)), None)
        key = ('cell',cell) if cell is not None else ('block',token['block'])
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
            if inter.get_area()>0 or consecutive or (has_native and abs(box.y0-ob.y0)<3 and min(abs(box.x1-ob.x0),abs(ob.x1-box.x0))<18):
                groups[key].extend(groups.pop(other));box |= ob
    regions=[]
    for key, items in groups.items():
        lines=make_lines(items)
        text='\n'.join(ln['text'] for ln in lines if ln['text'])
        if not text: continue
        ink=union(t['bbox'] for t in items)
        if key[0]=='cell':
            box=cells[key[1]] + (2,2,-2,-2)
            box |= ink  # never clip existing glyphs due to border detection rounding
        else:
            box=fitz.Rect(ink)+(-.5,-1.5,.5,1.5)
        box &= page.rect
        has_ocr=any(not t['native'] for t in items)
        private_use=any(unicodedata.category(c)=='Co' for c in text)
        patch=has_ocr or private_use or any(box.intersects(im) for im in images) or any(box.intersects(g) for g in glyphs)
        spans=[dict(text=t['text'],bbox=t['bbox'],origin=t['origin'],size=t['size'],font=t['font'],flags=t['flags'],
                    color=t['color'],alpha=255,chars=[{'c':t['text'],'bbox':t['bbox'],'origin':t['origin']}]) for t in items]
        dominant=max((t for t in items if t['native']),key=lambda t:t['size'], default=items[0])
        size=median(t['size'] for t in items if t['native']) if any(t['native'] for t in items) else median(t['size'] for t in items)
        warnings=[]
        if existing_redaction:warnings.append('기존 삭제 주석이 있는 페이지는 수정하지 않습니다.')
        if any(t.get('unsupported') for t in items):warnings.append('회전·세로·투명 텍스트는 원문을 유지합니다.')
        regions.append(dict(id='',page=page.number,text=text,bbox=list(box),display_bbox=list(box),source_bbox=list(ink),
                            kind='table_cell' if key[0]=='cell' else 'paragraph',spans=spans,font_size=size,warnings=warnings,
                            patch=patch,ocr=has_ocr or private_use,mask_boxes=[t['bbox'] for t in items]+[list(g) for g in glyphs if box.contains(g)],
                            dominant_color=dominant['color'],dominant_flags=dominant['flags']))
    regions = merge_image_paragraphs(regions, page, image)
    regions.sort(key=lambda r:(round(r['bbox'][1],1),r['bbox'][0]))
    for i,r in enumerate(regions): r['id']=f'p{page.number+1}-r{i}'
    partition_regions(regions)
    # Unclassified paragraphs retain their measured width. Empty page space
    # does not imply permission to expand across a table or figure boundary.
    return dict(number=page.number,width=page.rect.width,height=page.rect.height,rotation=page.rotation,
                mediabox=list(page.mediabox),cropbox=list(page.cropbox),regions=regions,
                warnings=[] if regions else ['글자를 찾지 못한 페이지입니다. 수동 확인이 필요합니다.'],
                ocr_regions=sum(r['ocr'] for r in regions),table_cells=sum(r['kind']=='table_cell' for r in regions))


def partition_regions(regions):
    """Share whitespace between adjacent lines; padding must not overlap."""
    for i, upper in enumerate(regions):
        a = fitz.Rect(upper['source_bbox'])
        for lower in regions[i+1:]:
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
        return np.median(pixels.reshape(-1,3),axis=0)
    def eligible(r):
        return r['kind']=='paragraph' and all(s['font']=='OCR' for s in r['spans'])
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


def cleaned_patch(page, region):
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
    # PNG uses BGR in OpenCV.
    ok, encoded=cv2.imencode('.png',cv2.cvtColor(cleaned,cv2.COLOR_RGB2BGR))
    if not ok:raise ValueError('이미지 원문 제거 결과를 저장할 수 없습니다.')
    return encoded.tobytes()
