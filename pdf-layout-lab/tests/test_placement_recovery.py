import copy

import fitz

from engine.models import RenderRequest
from engine.placement_recovery import recover, available_box
from engine.typesetter import fit
from engine.native_refinement import repair_symbols
from engine.enhanced import structured_cell_parts
from engine.unit_translation import bind_sources


def region():
    b=[40,40,240,54]
    return dict(id='body',bbox=b,source_bbox=[40,40,80,53],display_bbox=list(b),
                text='Original',kind='paragraph',font_size=12,patch=False,
                typography={'parts':[dict(text='A longer translation with an explicit line.\n    Indented second line.',
                    source_text='Original',bbox=[40,40.4,239.5,53.7],ink_bbox=[40,40,80,53],
                    font_size=12,align='left',flags=0,color=0,anchored=False)]})


def test_space_recovery_preserves_source_and_enter():
    r=region(); original=copy.deepcopy(r)
    policy=RenderRequest(mode='fit',min_scale=.8,min_font_size=9)
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=250)
        scale,audit=recover(p,{'regions':[r]},r,r['typography']['parts'][0]['text'],policy,'배치 공간 부족')
        assert scale and audit['status']=='adjusted'
        assert r['bbox'][3]>original['bbox'][3]
        assert r['source_bbox']==original['source_bbox']
        assert r['typography']['parts'][0]['text']==original['typography']['parts'][0]['text']
        fit(p,r,r['typography']['parts'][0]['text'],policy)
        words={w[4]:w for w in p.get_text('words')}
        assert words['Indented'][0]>words['A'][0]+8
        assert words['Indented'][1]>words['A'][1]
        assert audit['attempts'][-1]['step']=='available_space'


def test_failed_recovery_does_not_mutate_and_cannot_cross_neighbor_or_rule():
    r=region(); before=copy.deepcopy(r)
    other=dict(id='neighbor',bbox=[40,55,240,75],source_bbox=[40,55,240,75],kind='paragraph',font_size=12)
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=250)
        scale,audit=recover(p,{'regions':[r,other]},r,r['typography']['parts'][0]['text'],
                            RenderRequest(mode='fit',min_scale=1,min_font_size=12),'공간 부족')
        assert scale is None and audit['status']=='unresolved' and r==before
        p.draw_line((30,56),(270,56))
        assert available_box(p,{'regions':[r]},r).y1<56


def test_table_and_explicit_user_box_never_expand():
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=250)
        for changes in ({'kind':'table_cell'},{'user_geometry':True},{'patch':True}):
            r=region();r.update(changes)
            assert list(available_box(p,{'regions':[r]},r))==r['bbox']
        r=region()
        scale,audit=recover(p,{'regions':[r]},r,'text',RenderRequest(),'번역 ID 누락')
        assert scale is None and not audit['attempts']


def test_wrapped_numbered_item_is_one_unit_but_new_marker_stays_separate():
    tokens=[dict(text=text,bbox=[40,y,right,y+10],size=10)
            for text,y,right in [('1) Domain terminology identifi',40,195),('cation and style',52,130),
                                 ('2) Another independent item',72,195)]]
    parts=structured_cell_parts(tokens,fitz.Rect(30,30,210,95))
    assert len(parts)==2
    assert len(parts[0][0])==2 and len(parts[1][0])==1


def test_only_known_leading_symbols_are_repaired():
    r=dict(text='\uf0a7 A bullet\ncontinued',spans=[dict(text='\uf0a7',chars=[{'c':'\uf0a7'}])],
           warnings=['특수 글꼴 문자를 정확히 읽지 못했습니다. 원문을 유지합니다.'])
    assert repair_symbols(r) and r['text']=='▪ A bullet\ncontinued' and not r['warnings']
    for text in ['unknown \ue001','word\uf0a7end']:
        r=dict(text=text)
        assert not repair_symbols(r) and r['text']==text


def test_matching_id_with_wrong_source_echo_is_rejected():
    segments=[dict(id='a',text='First source\nline'),dict(id='b',text='Next unrelated source')]
    response=[dict(id='a',source_text='Next unrelated source',text='Wrongly shifted'),
              dict(id='b',source_text='Next unrelated source',text='Correct')]
    assert [r['id'] for r in bind_sources(segments,response)]==['b']
    assert bind_sources(segments,[dict(id='a',source_text='First source line',text='Correct')])
    assert bind_sources(segments,[dict(id='unexpected',source_text='First source',text='Wrong')]) is None


def test_long_word_wrap_does_not_change_extracted_letters():
    from engine.typesetter import place_part
    from engine.models import normalized
    part=dict(text='Registration',font_size=9,bbox=[10,10,42,60],align='left',flags=0,color=0,wrap_long_words=True)
    with fitz.open() as pdf:
        page=pdf.new_page(width=100,height=100)
        assert place_part(page,part,RenderRequest(),fixed=True)==1
        assert normalized(page.get_text())=='Registration'
        assert len(page.get_text().splitlines())>1


def test_parallel_translation_keeps_response_ids_and_limits_requests(tmp_path,monkeypatch):
    import threading,time
    from engine import legacy,unit_translation
    legacy.setup()
    monkeypatch.setattr(legacy,'configuration',lambda:dict(configured=True,provider='test',model='test'))
    units=[dict(id=f'u{i}',source_text=f'Translate packet {i}',flow=False,kind='paragraph',protected=False,
                context_before='',context_after='') for i in range(65)]
    layout=dict(translation_units=units,placement_spaces=[dict(unit_id=u['id'],region_id=u['id']) for u in units])
    active=peak=0;lock=threading.Lock()
    def request(batch,*args):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.03)
        with lock:active-=1
        return [dict(id=p['id'],text='Result '+p['text']) for p in reversed(batch)]
    monkeypatch.setattr(unit_translation,'request_segments',request)
    result=unit_translation.translate(layout,'en','ko',tmp_path)
    assert 1<peak<=3 and not result['incomplete_ids']
    assert all(result['replacements'][u['id']]=='Result '+u['source_text'] for u in units)
