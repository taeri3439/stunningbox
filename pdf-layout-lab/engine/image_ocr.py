"""Page-at-a-time rasterization and bounded, ID-checked OCR batches."""
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path

import fitz
from PIL import Image

from .ocr_review import IMAGE_SYSTEM, crop_for_vision, ocr_configuration

BATCH_SIZE = 6
VERSION = 1
BATCH_SYSTEM = IMAGE_SYSTEM + '\nFor multiple images, return one result per supplied ID. Never mix text between IDs.'
ITEM_SCHEMA = {'type':'object','properties':{'text':{'type':'string'},
               'kind':{'type':'string','enum':['text','nontext','unreadable']}},'required':['text','kind']}


def valid(answer):
    return (isinstance(answer,dict) and answer.get('kind') in {'text','nontext','unreadable'}
            and isinstance(answer.get('text'),str)
            and (answer['kind']!='text' or bool(answer['text'].strip())))


def request_batch(items, config):
    single = len(items)==1
    row_schema = {'type':'object','properties':{**ITEM_SCHEMA['properties'],'id':{'type':'string'}},
                  'required':['id','text','kind']}
    schema = ITEM_SCHEMA if single else {'type':'object','properties':{'regions':{'type':'array','items':row_schema}},'required':['regions']}
    content=[]
    for region,b64 in items:
        if not single:content.append({'type':'text','text':json.dumps({'id':region['id']})})
        content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64,'detail':'high'}})
    if config['provider']=='openai':
        from pipeline.llm_provider import openai_tool_call
        answer,finish=openai_tool_call(model=config['model'],system=BATCH_SYSTEM,user_content=content,
            tool_name='transcribe',tool_description='Read original text for each image ID',parameters=schema,
            max_tokens=2500 if single else 10000)
        truncated=finish=='length'
    else:
        import anthropic
        converted=[]
        for item in content:
            if item['type']=='text':converted.append(item)
            else:converted.append({'type':'image','source':{'type':'base64','media_type':'image/png',
                'data':item['image_url']['url'].split(',',1)[1]}})
        response=anthropic.Anthropic().messages.create(model=config['model'],max_tokens=2500 if single else 10000,
            system=BATCH_SYSTEM,tools=[{'name':'transcribe','description':'Read original text','input_schema':schema}],
            tool_choice={'type':'tool','name':'transcribe'},messages=[{'role':'user','content':converted}])
        answer=next((b.input for b in response.content if b.type=='tool_use'),None)
        truncated=response.stop_reason=='max_tokens'
    if single:
        if truncated or not valid(answer):raise ValueError('이미지 문자 인식 응답이 불완전합니다. 저장된 결과로 재시도할 수 있습니다.')
        return {items[0][0]['id']:answer}
    rows=answer.get('regions',[]) if isinstance(answer,dict) else []
    if not isinstance(rows,list):rows=[]
    result={}
    for region,b64 in items:
        matches=[row for row in rows if isinstance(row,dict) and row.get('id')==region['id']]
        if not truncated and len(matches)==1 and valid(matches[0]):result[region['id']]=matches[0]
        else:result.update(request_batch([(region,b64)],config))
    return result


def raster_page(source, number, lock, dpi=450):
    with (lock or nullcontext()),fitz.open(source) as doc:
        pix=doc[number].get_pixmap(dpi=dpi,alpha=False,colorspace=fitz.csRGB)
        return Image.frombytes('RGB',(pix.width,pix.height),pix.samples),(pix.x,pix.y)


def correct_images(layout,source,cache_dir,progress=None,lock=None,prepare_progress=None):
    config=ocr_configuration()
    cache_dir.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    total=sum(r.get('patch',False) and all(s['font']=='OCR' for s in r['spans'])
              for p in layout['pages'] for r in p['regions'])
    done=0
    def accept(region, answer):
        nonlocal done
        region['ocr_draft']=region['text'];region['ocr_verified_by']='vision-single' if total==1 else 'vision-batch'
        if answer['kind']=='text':region['text']=answer['text'];region.pop('keep_original',None)
        elif answer['kind']=='nontext':region['keep_original']=True
        else:region['warnings'].append('이미지 원문이 작거나 흐려 정확히 읽지 못했습니다.')
        done+=1
        if progress:progress(done,total)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for page in layout['pages']:
            pending=[]
            for region in page['regions']:
                if not region.get('patch') or not all(s['font']=='OCR' for s in region['spans']):continue
                identity=[VERSION,digest,page['number'],region['id'],region['bbox'],config,BATCH_SYSTEM]
                key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
                cache=cache_dir/f'batch-{key}.json'
                answer=None
                if cache.exists():
                    try:answer=json.loads(cache.read_text('utf-8'))
                    except (ValueError,OSError):pass
                if valid(answer):accept(region,answer)
                else:pending.append((region,cache))
            if not pending:continue
            if prepare_progress:prepare_progress(page['number']+1,len(layout['pages']),0,len(pending))
            image,origin=raster_page(source,page['number'],lock)
            entries=[]
            for i,(region,cache) in enumerate(pending,1):
                b=region['bbox'];z=450/72
                box=(max(0,math.floor(b[0]*z-origin[0])),max(0,math.floor(b[1]*z-origin[1])),
                     min(image.width,math.ceil(b[2]*z-origin[0])),min(image.height,math.ceil(b[3]*z-origin[1])))
                entries.append((region,crop_for_vision(image.crop(box)),cache))
                if prepare_progress:prepare_progress(page['number']+1,len(layout['pages']),i,len(pending))
            image.close()
            batches=[entries[i:i+BATCH_SIZE] for i in range(0,len(entries),BATCH_SIZE)]
            def process(batch):
                answers=request_batch([(r,b64) for r,b64,_ in batch],config)
                for region,_,cache in batch:
                    cache.write_text(json.dumps(answers[region['id']],ensure_ascii=False),encoding='utf-8')
                return batch,answers
            futures=[pool.submit(process,batch) for batch in batches]
            for future in as_completed(futures):
                batch,answers=future.result()
                for region,_,_ in batch:accept(region,answers[region['id']])
    return layout
