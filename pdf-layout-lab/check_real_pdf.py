"""Opt-in real API regression run. Never called by the unit tests."""
import argparse
import hashlib
import json
from pathlib import Path

import fitz

from engine.enhanced import analyze_page, VERSION
from engine.ocr_review import correct_sources, correct_image_sources
from engine.legacy import translate_shared
from engine.models import RenderRequest
from engine.service import render


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--pages',type=int,default=4)
    parser.add_argument('--analysis-cache',type=Path)
    args=parser.parse_args()
    if args.pages < 1:
        parser.error('--pages must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    source=args.output/'source.pdf'
    if not source.exists():
        with fitz.open(args.source) as doc,fitz.open() as sub:
            sub.insert_pdf(doc,from_page=0,to_page=min(len(doc),args.pages)-1)
            sub.save(source)
    pages=[]
    with fitz.open(source) as doc:
        if len(doc) > args.pages:
            raise ValueError('기존 검증 폴더의 PDF가 허용 페이지 수보다 큽니다. 새 출력 폴더를 사용하세요.')
        for i,page in enumerate(doc):
            cached=args.analysis_cache/f'page{i}.json' if args.analysis_cache else args.output/f'analysis-v{VERSION}-page{i}.json'
            if cached.exists(): data=json.loads(cached.read_text('utf-8'))
            else:
                print('ANALYZE',i+1,flush=True);data=analyze_page(page,'ko')
                cached.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            pages.append(data)
    layout=dict(source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),schema_version=VERSION,pages=pages,
                region_count=sum(len(p['regions']) for p in pages))
    layout=correct_image_sources(layout,source,args.output/'image-cache',lambda d,n:print('IMAGE',d,n,flush=True))
    layout=correct_sources(layout,source,args.output/'ocr-cache',lambda p,n,i,k:print('OCR',p+1,i,k,flush=True))
    (args.output/'layout.json').write_text(json.dumps(layout,ensure_ascii=False),encoding='utf-8')
    result=translate_shared(layout,'ko','en',args.output/'translations',lambda *a:print('TRANSLATE',a,flush=True))
    (args.output/'translation.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    report=render(source,args.output/'output.pdf',layout,RenderRequest(mode='fit',replacements=result['replacements'],min_scale=.4,min_font_size=3,line_height=1.1))
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',report['validation']['processed'],report['validation']['total'],report['validation']['status'],flush=True)


if __name__=='__main__':main()
