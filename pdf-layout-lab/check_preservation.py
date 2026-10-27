"""Offline structural + rendered regression corpus. Never calls a translation API.

Run from pdf-layout-lab:
  python check_preservation.py ../output/pdf/layout-preservation
  python check_preservation.py OUTPUT --saved-run data/DOC/runs/RUN --source data/DOC/source.pdf
Existing documents, runs and translation caches are read-only.
"""
import argparse
from collections import Counter
import copy
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from engine.models import RenderRequest
from engine.service import render


def compare_pages(source, output, directory):
    executable = shutil.which('pdftoppm')
    if not executable:
        raise RuntimeError('pdftoppm is required for independent visual regression rendering')
    for name, path in [('source', source), ('output', output)]:
        subprocess.run([executable, '-r', '100', '-png', str(path), str(directory/name)],
                       check=True, capture_output=True)
    thumbnails = []
    sources = sorted(directory.glob('source-*.png'))
    for src in sources:
        dst = directory/src.name.replace('source-', 'output-')
        with Image.open(src) as left, Image.open(dst) as right:
            left.thumbnail((400,560));right.thumbnail((400,560))
            tile = Image.new('RGB',(820,max(left.height,right.height)+30),'white')
            tile.paste(left,(0,30));tile.paste(right,(420,30))
            ImageDraw.Draw(tile).text((10,7),src.stem+' / translated',fill='black')
            tile.save(directory/src.name.replace('source-', 'comparison-'))
            thumbnails.append(tile)
    for offset in range(0,len(thumbnails),6):
        chunk=thumbnails[offset:offset+6]
        contact=Image.new('RGB',(820,sum(im.height for im in chunk)), '#ddd')
        y=0
        for im in chunk:contact.paste(im,(0,y));y+=im.height
        contact.save(directory/f'contact-{offset+1}.png')


def summarize(report):
    validation = report['validation']
    return dict(total=validation['total'], translated=validation['translated'],
                retained_for_review=validation['skipped'], geometry_ok=validation['geometry_ok'],
                structure_ok=validation.get('structure_ok'),
                text_check_failures=sum(r.get('text_check') is False for r in report['regions']),
                pages_with_issues=sum(bool(p['issues']) for p in validation['pages']),
                outside_changed_pixels=sum(p['outside_changed_pixels'] for p in validation['pages']),
                applied_review=validation.get('applied_review',0),
                retained_reasons=dict(Counter(r.get('failure_category','legacy') for r in report['regions'] if r['status']=='NEEDS_REVIEW')),
                status=validation['status'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--saved-run',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--no-images',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=args.output/'placement.log',level=logging.WARNING)
    summary={}
    if args.saved_run:
        if not args.source:parser.error('--source is required with --saved-run')
        # Prevent accidental overwriting of saved user runs.
        if args.output.resolve()==args.saved_run.resolve():parser.error('Choose a separate output directory')
        layout=json.loads((args.saved_run/'layout.json').read_text('utf-8'))
        previous=json.loads((args.saved_run/'report.json').read_text('utf-8'))
        policy=RenderRequest.model_validate(previous['policy'])
        layout=copy.deepcopy(layout)
        report=render(args.source,args.output/'output.pdf',layout,policy,
                      progress=lambda n,total:print(f'PAGE {n}/{total}',flush=True))
        (args.output/'layout.json').write_text(json.dumps(layout,ensure_ascii=False),encoding='utf-8')
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        summary={'before':summarize(previous),'after':summarize(report)}
        if not args.no_images:compare_pages(args.source,args.output/'output.pdf',args.output)
    else:
        sys.path.insert(0,str(Path(__file__).parent/'tests'))
        from test_layout_preservation import fixture_pdf, analyze
        for name,variant,rotation,crop in [('body','body',0,False),('columns','columns',0,False),
                ('rotated-table','table',90,True),('artwork','artwork',0,False),('styles','styles',0,False),
                ('overflow','body',0,False)]:
            folder=args.output/name;folder.mkdir(exist_ok=True)
            source=fixture_pdf(folder/'source.pdf',variant=variant,rotation=rotation,crop=crop,pages=3)
            layout=analyze(source)
            replacements={r['id']:('Expanded translation requiring additional readable lines.' if p['number']%2==0 else 'Short translation.')
                          for p in layout['pages'] for r in p['regions'] if r['text']=='Source paragraph'}
            if name=='overflow':replacements={key:'Unreasonably long translation. '*350 for key in replacements}
            report=render(source,folder/'output.pdf',layout,RenderRequest(mode='fit',replacements=replacements))
            (folder/'layout.json').write_text(json.dumps(layout,ensure_ascii=False),encoding='utf-8')
            (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            summary[name]=summarize(report)
            if not args.no_images:compare_pages(source,folder/'output.pdf',folder)
            print(name,json.dumps(summary[name]),flush=True)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
