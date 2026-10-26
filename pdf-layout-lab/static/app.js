const $ = id => document.getElementById(id);
let doc = null, page = 0, selected = null, drafts = {}, busy = false, overlay = false;
let geometryIds = [], geometryOwner = '', suppressRegionClick = false;
const checkedRegions = new Set();
let apiConfig = {configured:false};
const jobStages = [
  ['analyze', '문서·표 분석'], ['ocr', '이미지 글자 읽기'], ['translate', '번역'],
  ['new_pdf', '새 PDF 조판·검증'], ['new_docx', '새 DOCX 생성'],
  ['new_preview', '새 DOCX → PDF'], ['docx', '기존 프로젝트 처리'],
  ['legacy_pdf', '기존 DOCX → PDF'], ['finalize', '결과 저장'],
];
const stageGroup = stage => stage === 'ocr_prepare' ? 'ocr' : stage;
let displayedJob = null, jobReceivedAt = 0, jobTimer = null;
const duration = seconds => {
  const value = Math.max(0, Math.floor(seconds || 0));
  return value < 60 ? `${value}초` : `${Math.floor(value / 60)}분 ${value % 60}초`;
};
function showJob(job) {
  displayedJob = job;
  jobReceivedAt = performance.now();
  clearInterval(jobTimer);
  renderJob();
  if (['queued', 'running'].includes(job.status)) jobTimer = setInterval(renderJob, 1000);
}
function renderJob() {
  if (!displayedJob) return;
  const job = displayedJob;
  const running = ['queued', 'running'].includes(job.status);
  const timed = typeof job.elapsed_seconds === 'number';
  const extra = running ? (performance.now() - jobReceivedAt) / 1000 : 0;
  const active = stageGroup(job.stage);
  $('jobProgress').hidden = false;
  $('jobProgress').classList.toggle('failed', job.status === 'failed');
  const label = jobStages.find(([key]) => key === active)?.[1];
  const title = job.status === 'queued' ? '앞선 작업 완료 대기 중' : running ? `${label || '처리'} 중` :
    job.status === 'failed' ? '처리 중 오류 발생' : job.status === 'cancelled' ? '작업 중지됨' : job.status === 'partial' ? '처리 완료 · 검토 필요' : '결과 준비 완료';
  if ($('jobTitle').textContent !== title) $('jobTitle').textContent = title;
  $('jobElapsed').textContent = timed ? `전체 ${duration(job.elapsed_seconds + extra)}` : '시간 기록 없음';
  $('jobDetail').textContent = job.message + (running && timed ? ` · 현재 단계 ${duration((job.stage_seconds || 0) + extra)}` : '');
  $('jobMeter').hidden = !running || !timed || !(job.total > 0);
  if (job.total > 0) { $('jobMeter').max = job.total; $('jobMeter').value = Math.min(job.done || 0, job.total); }
  const stages = job.include_baseline === false ? jobStages.filter(([key]) => !['new_preview', 'docx', 'legacy_pdf'].includes(key)) : jobStages;
  $('jobSteps').replaceChildren(...stages.map(([key, name], index) => {
    const records = (job.steps || []).filter(step => stageGroup(step.stage) === key);
    const isActive = running && active === key;
    const failed = records.some(step => step.status === 'failed');
    const cancelled = records.some(step => step.status === 'cancelled');
    const passed = records.length > 0 && !isActive && !cancelled;
    const item = document.createElement('li');
    item.className = isActive ? 'active' : failed ? 'failed' : passed ? 'finished' : '';
    if (isActive) item.setAttribute('aria-current', 'step');
    const marker = document.createElement('span'); marker.className = 'step-marker';
    marker.textContent = failed ? '!' : cancelled ? '■' : passed ? '✓' : String(index + 1).padStart(2, '0');
    const text = document.createElement('span'); text.textContent = name;
    const time = document.createElement('small');
    const seconds = records.reduce((sum, step) => sum + step.seconds, 0) + (isActive ? extra : 0);
    time.textContent = cancelled ? `중지 · ${duration(seconds)}` : records.length ? duration(seconds) : isActive ? '진행 중' : !timed ? '—' : running ? '대기' : '미실행';
    item.append(marker, text, time); return item;
  }));
  const parts = [job.include_baseline === false ? '새 레이아웃만 생성합니다. 기존 프로젝트 처리와 DOCX 미리보기 변환은 생략합니다.' : '두 방식 비교는 기존 프로젝트 처리와 DOCX 미리보기 변환까지 수행합니다.'];
  if (job.cache_hit) parts.push('저장된 번역문을 재사용했습니다.');
  if (typeof job.cached_segments==='number') parts.push(`문장 캐시 ${job.cached_segments}/${job.segment_count}개 재사용.`);
  if (job.translation_stats?.api_calls) parts.push(`API ${job.translation_stats.api_calls}회 · 응답 대기 ${duration(job.translation_stats.api_seconds)} · 형식 오류 ${job.translation_stats.invalid_responses}회.`);
  if (!timed) parts.push('업데이트 전에 시작된 작업입니다. 단계별 시간은 서버 업데이트 후 새 작업부터 기록됩니다.');
  if (running && ['new_preview', 'legacy_pdf'].includes(job.stage)) parts.push('OnlyOffice 응답을 기다리는 중입니다. 변환기는 세부 진행률을 제공하지 않습니다.');
  if (running && extra > 10) parts.push('상태 업데이트가 지연되고 있습니다. 표시 시간은 브라우저 기준 경과 시간입니다.');
  $('jobHint').textContent = parts.join(' ');
  document.dispatchEvent(new CustomEvent('lab:job'));
}
const currentPage = () => doc?.layout.pages[page] || {regions:[],warnings:[],width:1,height:1};
const newPageTotal = () => doc?.report?.pdf_source === 'layout' ? doc.layout.pages.length : (doc?.report?.docx_output?.page_count || doc?.layout.pages.length || 0);
const pageTotal = () => Math.max(doc?.layout.pages.length || 0, doc?.baseline?.page_count || 0, newPageTotal());

function notice(message, error = false) {
  $('notice').hidden = !message;
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : `요청 실패 (${response.status})`);
  }
  return response.json();
}
async function task(message, action) {
  if (busy) return;
  busy = true;
  document.body.classList.add('busy');
  document.dispatchEvent(new CustomEvent('lab:busy'));
  for (const id of ['uploadButton', 'renderButton', 'applyRegionButton', 'baselineButton', 'history', 'mode', 'replacement', 'minScale', 'minFont', 'lineHeight', 'preserveSpacing', 'harmonizeRepeats', 'compareButton', 'sourceLang', 'targetLang']) $(id).disabled = true;
  notice(message);
  updateBatchControls();
  try { await action(); } catch (error) { notice(error.message, true); }
  finally {
    busy = false; document.body.classList.remove('busy');
    for (const id of ['uploadButton', 'history', 'mode', 'minScale', 'minFont', 'lineHeight', 'preserveSpacing', 'harmonizeRepeats', 'sourceLang', 'targetLang']) $(id).disabled = false;
    $('renderButton').disabled = !doc;
    $('baselineButton').disabled = !doc;
    $('compareButton').disabled = !doc || !apiConfig.configured;
    $('replacement').disabled = !selected || $('mode').value !== 'fit';
    showRegionApplication();
    showTranslationUnit();
    updateBatchControls();
    document.dispatchEvent(new CustomEvent('lab:idle'));
  }
}
async function refreshDocuments() {
  const list = await api('/api/documents');
  $('history').replaceChildren(new Option('PDF를 선택하세요', ''));
  for (const item of list) $('history').add(new Option(item.name, item.id));
  if (doc) $('history').value = doc.id;
  document.dispatchEvent(new CustomEvent('lab:history'));
}
function base() { return `/api/documents/${doc.id}`; }
function imageUrl(kind, number = page) {
  const version = kind === 'baseline' ? doc.baseline?.version : doc.latest_run;
  return `${base()}/pages/${kind}/${number}?run=${doc.latest_run || ''}&v=${version || doc.id}`;
}
function useDocument(value, fresh = false) {
  if (fresh || doc?.id !== value.id) { checkedRegions.clear(); $('batchApplyResult').textContent = ''; }
  if (value.native) { doc = value; location.href = `/native?document=${value.id}`; return; }
  doc = value;
  if (fresh && !doc.active_job) {
    clearInterval(jobTimer);
    displayedJob = null;
    $('jobProgress').hidden = true;
    if (doc.report?.processing) showJob(doc.report.processing);
  }
  if (fresh) {
    page = 0; selected = null; drafts = {}; overlay = false;
    $('preserveSpacing').checked = doc.report?.policy.preserve_spacing ?? true;
    $('harmonizeRepeats').checked = doc.report?.policy.harmonize_repeats ?? true;
    if (doc.report) {
      drafts = {...doc.report.policy.replacements};
      $('mode').value = doc.report.mode;
      $('minScale').value = Math.round(doc.report.policy.min_scale * 100);
      $('minFont').value = doc.report.policy.min_font_size;
      $('lineHeight').value = doc.report.policy.line_height;
    }
    if (doc.comparison) {
      $('sourceLang').value = doc.comparison.source_lang;
      $('targetLang').value = doc.comparison.target_lang;
    }
    if ((doc.layout.schema_version || 1) < 5) {
      $('minScale').value = 40; $('minFont').value = 3; $('lineHeight').value = 1.1;
    }
  }
  $('history').value = doc.id;
  $('fileInfo').textContent = `${doc.name} · ${doc.layout.pages.length}페이지 · ${doc.layout.region_count}개 영역`;
  $('baselineHelp').textContent = doc.baseline ? `${doc.baseline.name} · ${doc.baseline.page_count}페이지. 페이지 번호 기준으로 비교합니다.` : 'API 번역 비교 시 자동 생성됩니다. 별도 PDF를 올리면 동일 번역문 여부는 확인할 수 없습니다.';
  const comparison = doc.comparison;
  $('baselineTitle').textContent = comparison?.project_result ? '기존 프로젝트 · 전체 파이프라인' : comparison ? '이전 실험 · 문단 변환 결과' : '기존 프로젝트 · 비교 결과';
  $('comparisonStatus').hidden = !comparison && !doc.baseline;
  $('comparisonStatus').classList.toggle('warning', !comparison || comparison.legacy_status !== 'ready' || doc.report?.validation.status !== 'CHECKS_PASSED');
  if (comparison) {
    const v = doc.report.validation;
    const status = `번역 배치 ${v.translated ?? v.processed}/${v.total - (v.unchanged || 0)}개 (${Math.round((v.translated ?? v.processed) / Math.max(1,v.total - (v.unchanged || 0))*100)}%) · 배치 실패 ${v.skipped}개 · 변경 없는 기호/원문 ${v.unchanged || 0}개 · ${v.status === 'CHECKS_PASSED' ? '자동 검사 통과' : '검토 필요'} · ${comparison.legacy_status === 'ready' ? '기존 PDF 비교 가능' : comparison.legacy_status === 'skipped' ? '새 레이아웃만 생성' : '기존 PDF 변환 미완료'}`;
    $('comparisonStatus').textContent = `${status}\n${comparison.provider} · ${comparison.model} · ${comparison.source_lang} → ${comparison.target_lang} · ${comparison.cache_hit ? '저장된 번역 재사용' : 'API 번역 완료'}\n${comparison.scope}${comparison.legacy_error ? '\n' + comparison.legacy_error : ''}${comparison.unchanged_ids.length ? '\n원문과 동일한 번역 ' + comparison.unchanged_ids.length + '개: 고유명사 또는 번역 실패 여부를 확인하세요.' : ''}`;
  } else if (doc.baseline) $('comparisonStatus').textContent = '수동 비교: 현재 새 출력과 기존 PDF가 동일한 번역문을 사용했는지 확인되지 않았습니다.';
  for (const [id, kind, available] of [['downloadDocx','docx',comparison?.docx_ready], ['downloadTranslation','translation',!!comparison]]) {
    $(id).hidden = !available;
    if (available) $(id).href = `${base()}/download/${kind}?run=${comparison.run_id}`;
  }
  const newDocx = doc.report?.docx_output;
  $('prepareDocxButton').hidden = !newDocx?.deferred;
  $('downloadNewDocx').hidden = $('editNewDocx').hidden = !newDocx?.ready;
  if (newDocx?.ready) {
    $('downloadNewDocx').href = `${base()}/download/new-docx?run=${doc.latest_run}`;
    $('editNewDocx').href = `/editor?document=${doc.id}&run=${doc.latest_run}`;
    $('comparisonStatus').hidden = false;
    const preview = doc.report.pdf_source === 'layout' && !newDocx.edited ? '기본 PDF는 줄바꿈·배치를 직접 조판한 결과입니다. DOCX는 별도 편집·Export 결과입니다.' :
      newDocx.preview_status === 'ready' ? '기본 출력은 PDF입니다. 현재 PDF는 추가 DOCX 결과를 문서 서버에서 렌더링한 파일입니다.' :
      newDocx.preview_status === 'stale' ? '편집 후 미리보기가 아직 갱신되지 않았습니다. 편집기의 저장·갱신 버튼을 누르세요.' :
      '기본 출력인 레이아웃 PDF를 표시합니다. DOCX는 추가 Export로 다운로드·편집할 수 있습니다.';
    if (!comparison && !doc.baseline) $('comparisonStatus').textContent = '';
    $('comparisonStatus').textContent += `\n${preview}\n${newDocx.scope}`;
    if (newDocx.edited) $('comparisonStatus').textContent += '\n추가 DOCX를 수동 편집했습니다. 저장·갱신한 PDF에는 편집 내용이 반영되며 기존 출력과 번역문이 달라질 수 있습니다.';
  }
  updateExports();
  showPage(); modeChanged();
  document.dispatchEvent(new CustomEvent('lab:document', {detail:{fresh}}));
}
function showImage(kind, exists) {
  $(`${kind}Paper`).hidden = !exists;
  $(`${kind}Empty`).hidden = exists;
  if (exists) $(`${kind}Image`).src = imageUrl(kind);
}
function showPage() {
  if (!doc) return;
  page = Math.min(page, pageTotal() - 1);
  const current = currentPage();
  $('pageLabel').textContent = `${page + 1} / ${pageTotal()}`;
  $('previous').disabled = page === 0;
  $('next').disabled = page + 1 >= pageTotal();
  const hasSource = page < doc.layout.pages.length;
  showImage('source', hasSource); showImage('new', !!doc.latest_run && page < newPageTotal());
  if (!hasSource) {
    $('sourceEmpty').querySelector('p').textContent = '원본에는 이 페이지가 없습니다. 기존 출력에서 늘어난 페이지입니다.';
    $('newEmpty').querySelector('p').textContent = '새 출력에는 이 페이지가 없습니다.';
  }
  showImage('baseline', !!doc.baseline && page < doc.baseline.page_count);
  if (doc.baseline && page >= doc.baseline.page_count) $('baselineEmpty').querySelector('p').textContent = '기존 출력에는 이 페이지가 없습니다.';
  else $('baselineEmpty').querySelector('p').textContent = doc.comparison?.legacy_error || '번역 후 결과 생성·비교를 실행하거나 기존 출력 PDF를 추가하세요.';
  $('overlayButton').disabled = !doc.latest_run || !hasSource;
  $('newVersion').textContent = doc.report ? `${doc.report.mode.toUpperCase()} · ${doc.latest_run.slice(0, 6)}` : 'NOT GENERATED';
  $('regions').replaceChildren(new Option('영역을 선택하세요', ''));
  $('regionCount').textContent = `${current.regions.length}개 영역`;
  $('regionLayer').replaceChildren();
  for (const region of current.regions) {
    $('regions').add(new Option(`${region.id} · ${region.text.slice(0, 36).replaceAll('\n', ' ')}`, region.id));
    const rect = region.display_bbox;
    const button = document.createElement('button');
    button.className = 'region'; button.type = 'button';
    button.dataset.region = region.id;
    button.title = `${region.id}: ${region.text.slice(0, 100)}`;
    button.setAttribute('aria-label', `${region.id} 영역 선택`);
    button.style.cssText = `left:${rect[0] / current.width * 100}%;top:${rect[1] / current.height * 100}%;width:${(rect[2] - rect[0]) / current.width * 100}%;height:${(rect[3] - rect[1]) / current.height * 100}%;`;
    if (doc.report?.regions.find(r => r.id === region.id)?.status === 'NEEDS_REVIEW') button.classList.add('review');
    button.addEventListener('click', () => {
      if (suppressRegionClick) { suppressRegionClick=false; return; }
      if (busy) return;
      selectRegion(region.id);
      if ($('geometryMode').checked) return;
      if ($('pdfMultiSelect').checked && batchEnabled()) {
        if (batchEligible(region)) {
          checkedRegions.has(region.id) ? checkedRegions.delete(region.id) : checkedRegions.add(region.id);
        } else notice(manualApplyBlock(region) || '교체 텍스트를 입력한 뒤 선택하세요.', true);
      }
      updateBatchControls();
    });
    const handle=document.createElement('span'); handle.className='resize-handle';
    handle.title='드래그하여 박스 크기 조절'; button.append(handle);
    button.addEventListener('pointerdown', event => beginGeometryDrag(event,region,button));
    $('regionLayer').append(button);
  }
  $('regionLayer').hidden = !$('showRegions').checked;
  if ($('regionView').value === 'source' && !$('geometryMode').checked) showSourceAreas();
  selectRegion(current.regions.find(r => r.id === selected?.id)?.id || current.regions[0]?.id);
  drawBatchRegions();
  showResults(); showOverlay();
}
function selectRegion(id) {
  selected = currentPage().regions.find(r => r.id === id) || null;
  $('regions').value = selected?.id || '';
  for (const el of $('regionLayer').children) el.classList.toggle('selected', el.dataset.region === selected?.id);
  $('originalText').value = selected?.text || '';
  $('replacement').value = selected ? (drafts[selected.id] ?? selected.text) : '';
  $('replacement').disabled = busy || !selected || $('mode').value !== 'fit';
  const structure = {list_item:'목록 항목',check_column:'체크란',checkbox_label:'체크란 설명',checkbox_mark:'체크 표시 · 원본 유지',check_column_unresolved:'체크란 · 분리 검토 필요'}[selected?.structure];
  const paragraph = selected?.logical_paragraph ? `문단 단위 번역${selected.source_fragments?.length > 1 ? ' · 원문 ' + selected.source_fragments.length + '줄 연결' : ''} · ` : '';
  $('regionMeta').textContent = selected ? `${paragraph}${structure ? structure + ' · ' : ''}${selected.fonts.join(', ')} · ${selected.font_size.toFixed(1)}pt · ${selected.bbox.map(n => n.toFixed(1)).join(', ')} pt${selected.warnings.length ? ' · ' + selected.warnings.join(' ') : ''}` : '이 페이지에는 편집 가능한 텍스트 영역이 없습니다.';
  showRegionApplication();
  showTranslationUnit();
  if (geometryOwner !== `${doc?.latest_run}:${selected?.id}`) resetGeometry();
  updateBatchControls();
  document.dispatchEvent(new CustomEvent('lab:selection'));
}
function showSourceAreas() {
  const layer=$('regionLayer'), p=currentPage();
  const owners=new Map(p.regions.flatMap(r=>(r.source_region_ids||[]).map(id=>[id,r])));
  layer.replaceChildren();
  for(const source of doc.layout.source_regions||[]) {
    const owner=owners.get(source.id); if(!owner) continue;
    const b=source.bbox, button=document.createElement('button');
    button.type='button';button.className='region';button.dataset.region=owner.id;
    button.title=`원본 영역: ${source.text}`;
    button.setAttribute('aria-label',`원본 영역 · ${source.text.slice(0,30)}`);
    button.style.cssText=`left:${b[0]/p.width*100}%;top:${b[1]/p.height*100}%;width:${(b[2]-b[0])/p.width*100}%;height:${(b[3]-b[1])/p.height*100}%;`;
    button.onclick=()=>{
      if(busy)return;selectRegion(owner.id);
      if($('pdfMultiSelect').checked && batchEnabled() && batchEligible(owner)) {
        checkedRegions.has(owner.id)?checkedRegions.delete(owner.id):checkedRegions.add(owner.id);
      }
      updateBatchControls();
    };
    layer.append(button);
  }
}
function showTranslationUnit() {
  const unit=doc?.layout.translation_units?.find(u=>u.id===selected?.translation_unit_id);
  const labels={pending:'번역 대기',translated:'번역 완료',manual:'사용자 교체문',failed:'번역 실패',unchanged:'원문과 동일 · 확인 필요',protected:'원문 보호'};
  $('unitStatus').textContent=unit?`원본 영역 ${unit.source_region_ids.length}개 → 번역 묶음 1개 → 배치 공간 ${unit.placement_ids.length}개\n${labels[unit.status]||unit.status}${unit.reason?' · '+unit.reason:''}${unit.context_before||unit.context_after?'\n앞뒤 참고 문맥 포함 (번역 대상에서는 제외)':''}`:'기존 실행입니다. 다음 생성 시 번역 단위 정보를 저장합니다.';
  const sources=new Map((doc?.layout.source_regions||[]).map(s=>[s.id,s]));
  $('splitUnitAfter').replaceChildren(new Option('분리할 원본 줄을 선택하세요',''));
  for(const [i,id] of (unit?.source_region_ids||[]).slice(0,-1).entries()) {
    $('splitUnitAfter').add(new Option(`${i+1}번째 줄 뒤 · ${(sources.get(id)?.text||'').slice(-45)}`,id));
  }
  const editable=!!unit&&!!doc?.latest_run&&!busy&&!doc?.report?.docx_output?.edited;
  $('mergeTranslationUnits').disabled=!editable;
  $('splitTranslationUnit').disabled=!editable||unit.source_region_ids.length<2||selected?.patch||!!selected?.structure;
}
async function editTranslationUnit(action) {
  if(!doc||busy)return;
  const regionIds=action==='split'?[selected?.id]:[...checkedRegions];
  if(action==='merge'&&regionIds.length<2)return notice('여러 영역 선택으로 같은 열의 두 묶음 이상을 선택하세요.',true);
  if(action==='split'&&!$('splitUnitAfter').value)return notice('분리할 원본 줄을 선택하세요.',true);
  const splitAfter=$('splitUnitAfter').value;
  await task('번역 묶음을 저장하고 해당 페이지를 갱신합니다…',async()=>{
    const value=await api(`${base()}/units/edit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_run:doc.latest_run,action,region_ids:regionIds,split_after:splitAfter||null})});
    for(const id of regionIds){delete drafts[id];checkedRegions.delete(id);}
    useDocument(value);selectRegion(value.report.unit_edit.region_ids[0]);
    notice('묶음을 변경했습니다. 해당 단위만 다시 번역하거나 교체문을 입력하세요.');
  });
}
$('mergeTranslationUnits').onclick=()=>editTranslationUnit('merge');
$('splitTranslationUnit').onclick=()=>editTranslationUnit('split');
$('regionView').onchange=()=>{if($('regionView').value==='source'){$('geometryMode').checked=false;$('regionLayer').classList.remove('geometry-edit');}showPage();};
function showRegionApplication() {
  const element = $('regionApplication');
  const button = $('applyRegionButton');
  const enabled = !!selected && !!doc?.report && $('mode').value === 'fit' && selected.kind !== 'protected_text';
  button.hidden = $('applyRegionHelp').hidden = !enabled;
  button.disabled = busy || !enabled || !$('replacement').value.trim() || !!manualApplyBlock(selected);
  $('applyRegionHelp').textContent = manualApplyBlock(selected) || '현재 교체문을 이 영역 안에서 재배치합니다. 입력한 엔터는 유지하며, 공간·최소 글자 크기·보호 영역 제한은 계속 검사합니다. 아래 반영 결과를 확인하세요.';
  element.className = 'region-application';
  if (!selected) { element.textContent = '영역을 선택하면 PDF 반영 여부를 표시합니다.'; return; }
  const result = doc.report?.regions.find(r => r.id === selected.id);
  const saved = doc.report?.policy?.replacements?.[selected.id] ?? selected.text;
  if ((drafts[selected.id] ?? selected.text) !== saved) {
    element.textContent = '미반영 변경 · 현재 교체 텍스트는 아직 PDF에 적용되지 않았습니다. 재생성이 필요합니다.';
    element.classList.add('review'); return;
  }
  if (!result) { element.textContent = '미생성 · 이 영역의 PDF 반영 결과가 아직 없습니다.'; return; }
  if (doc.report.mode === 'replay') {
    element.textContent = result.status === 'NEEDS_REVIEW' ? `원문 재삽입 미반영 · ${result.reason}` : '원문 재삽입 결과 · 번역문을 적용한 실행이 아닙니다.';
    return;
  }
  if (doc.report?.docx_output?.edited) {
    element.textContent = '확인 필요 · DOCX를 수동 편집하여 현재 PDF와 최초 영역 보고서가 다를 수 있습니다. 미리보기를 확인하세요.';
    element.classList.add('review'); return;
  }
  if (result.status === 'NEEDS_REVIEW') {
    element.textContent = `PDF 미반영 · 원문 유지\n사유: ${result.reason}`;
    element.classList.add('review');
  } else if (result.status === 'UNCHANGED') {
    element.textContent = `원문 유지 · ${result.reason}`;
  } else if (result.pdf_text_found === false || !result.text_check) {
    element.textContent = `${result.manual_applied ? '사용자 확인으로 배치 · ' : '배치 시도 · '}텍스트 검증 미통과\n번역문이 완전히 반영됐는지 PDF를 직접 확인하세요.`;
    element.classList.add('review');
  } else {
    element.textContent = result.pdf_text_found === true ? 'PDF 반영됨 · 최종 PDF에서 번역문 확인 (위치·겹침은 미리보기 검토)' : '조판 반영됨 · 최종 PDF 미리보기에서 위치와 글자를 확인하세요.';
    element.classList.add('pass');
    if (result.manual_applied) element.textContent += '\n사용자 확인으로 적용 · 이웃 글자·표와의 겹침을 확인하세요.';
    if (result.layout_style?.anchored) element.textContent += '\n원문 행 위치·여백 기준 배치';
    if (result.layout_style?.rotated_process) element.textContent += '\n공정명 90° 회전 · 번호와 단계는 가로 유지';
    if (result.layout_style?.rotated_label) element.textContent += '\n세로 항목명 90° 회전 · 읽기 크기 확보';
    if (result.layout_style?.adaptive_leaders) element.textContent += '\n점선·수량 칸 조정 · 긴 항목명 줄바꿈';
    if (result.layout_style?.spacing_relaxed) element.textContent += '\n공간 부족으로 원문 여백 일부 축소 · 셀 경계와 엔터는 유지';
    if (result.layout_style?.repeated) element.textContent += ' · 반복 머리말 서식 통일';
  }
  if (result.adjustment) {
    const labels={padding:'내부 여백 조정',line_spacing:'행 간격 조정',word_breaks:'긴 단어 줄바꿈',available_space:'빈 공간까지 배치 확장',compact_cell:'표 칸 내부 간격 조정'};
    const steps=result.adjustment.attempts.map(a=>labels[a.step]||a.step).join(' → ');
    element.textContent += `\n자동 배치 조정: ${steps || '적용 대상 아님'}\n조정 결과: ${result.adjustment.status==='adjusted'?'글자·경계 검사 통과':(result.adjustment.stop_reason||'원문 유지')}`;
  }
  if (result.paragraph_alignment) {
    element.textContent += `\n문단 정렬: ${result.paragraph_alignment.reason}`;
    if (result.paragraph_alignment.status==='needs_review') element.classList.add('review');
  }
  if (result.typography_quality) {
    const q=result.typography_quality;
    element.textContent += `\n글자 크기: 원문 ${q.source_font_size.toFixed(1)}pt → 결과 ${q.rendered_font_size.toFixed(1)}pt (${Math.round(q.retained_ratio*100)}%)`;
    if (q.space?.expanded) element.textContent += `\n${q.space.boundary==='cell'?'같은 표 셀':'같은 열'}의 빈 공간을 먼저 활용했습니다.`;
    if (q.heading && q.line_count===1) element.textContent += '\n제목 한 줄 유지';
    if (q.review_required) { element.textContent += `\n가독성 검토: ${result.readability_warning}`; element.classList.add('review'); }
  }
  const originalEdit = doc.report?.docx_output?.original_regions?.find(r => r.id === selected.id);
  if (originalEdit?.editable) element.textContent += '\nDOCX 편집 가능 · 인식된 원문을 직접 수정할 수 있습니다.';
  else if (originalEdit) element.textContent += `\nDOCX 원문 복원 보류 · ${originalEdit.reason}`;
}
function manualApplyBlock(region) {
  if (doc?.report?.docx_output?.edited) return 'DOCX에서 편집한 결과입니다. DOCX 편집 화면에서 수정하세요.';
  if (region?.kind === 'protected_text') return '워터마크·보호 영역은 직접 교체할 수 없습니다.';
  if (region?.structure === 'checkbox_mark') return '체크 표시의 선택 상태는 원본 그대로 유지합니다. 옆의 설명 영역을 선택하세요.';
  if (region?.spatial_unresolved) return '이전 분석에서 여러 칸이 합쳐진 영역입니다. 번역 후 결과 생성·비교를 다시 실행해 영역을 분리하세요.';
  return '';
}
function geometryBox() {
  const [x,y,w,h]=['boxX','boxY','boxW','boxH'].map(id=>Number($(id).value));
  return [x,y,x+w,y+h];
}
function setGeometryBox(box) {
  [box[0],box[1],box[2]-box[0],box[3]-box[1]].forEach((n,i)=>$( ['boxX','boxY','boxW','boxH'][i]).value=Number(n.toFixed(2)));
  const el=$('regionLayer').querySelector(`[data-region="${selected?.id}"]`), p=currentPage();
  if(el && $('regionView').value!=='source') el.style.cssText=`left:${box[0]/p.width*100}%;top:${box[1]/p.height*100}%;width:${(box[2]-box[0])/p.width*100}%;height:${(box[3]-box[1])/p.height*100}%;`;
}
function resetGeometry() {
  geometryOwner=`${doc?.latest_run}:${selected?.id}`;
  geometryIds=selected?[selected.id]:[];
  $('editSourceText').value=selected?.text||'';
  if(selected) setGeometryBox(selected.bbox);
  $('geometryResult').textContent='';
}
function prepareMerge() {
  const regions=currentPage().regions.filter(r=>checkedRegions.has(r.id));
  if(regions.length!==checkedRegions.size || regions.length<2) return notice('같은 페이지의 두 영역 이상을 선택하세요.',true);
  if(regions.some(r=>manualApplyBlock(r))) return notice('보호되거나 위치가 미확정인 영역은 합칠 수 없습니다.',true);
  regions.sort((a,b)=>(a.source_bbox||a.bbox)[1]-(b.source_bbox||b.bbox)[1]||(a.source_bbox||a.bbox)[0]-(b.source_bbox||b.bbox)[0]);
  if($('regionView').value==='source'){$('regionView').value='placement';showPage();}
  selectRegion(regions[0].id); geometryIds=regions.map(r=>r.id);
  setGeometryBox([Math.min(...regions.map(r=>r.bbox[0])),Math.min(...regions.map(r=>r.bbox[1])),Math.max(...regions.map(r=>r.bbox[2])),Math.max(...regions.map(r=>r.bbox[3]))]);
  $('editSourceText').value=regions.map(r=>r.text).join('\n');
  $('replacement').value=regions.map(r=>drafts[r.id]??r.text).join('\n');
  $('geometryMode').checked=true; $('regionLayer').classList.add('geometry-edit');
  $('pdfMultiSelect').checked=false;updateBatchControls();
  $('geometryResult').textContent=`${regions.length}개 영역 병합 준비. 원문 순서와 박스를 확인하고 저장 또는 다시 번역·적용하세요.`;
  $('editSourceText').scrollIntoView({behavior:'smooth',block:'center'});
}
function beginGeometryDrag(event,region,button) {
  if(busy || !$('geometryMode').checked || manualApplyBlock(region)) return;
  if(selected?.id!==region.id) selectRegion(region.id);
  event.preventDefault(); button.setPointerCapture(event.pointerId);
  const start=geometryBox(), rect=$('regionLayer').getBoundingClientRect(), p=currentPage();
  const x=event.clientX,y=event.clientY,resize=event.target.classList.contains('resize-handle');
  let moved=false;
  const move=e=>{
    const dx=(e.clientX-x)*p.width/rect.width,dy=(e.clientY-y)*p.height/rect.height;
    moved=moved||Math.abs(dx)+Math.abs(dy)>1;
    if(resize) setGeometryBox([start[0],start[1],Math.max(start[0]+5,Math.min(p.width,start[2]+dx)),Math.max(start[1]+5,Math.min(p.height,start[3]+dy))]);
    else {
      const mx=Math.max(-start[0],Math.min(p.width-start[2],dx)),my=Math.max(-start[1],Math.min(p.height-start[3],dy));
      setGeometryBox([start[0]+mx,start[1]+my,start[2]+mx,start[3]+my]);
    }
  };
  const end=()=>{button.removeEventListener('pointermove',move);button.removeEventListener('pointerup',end);button.removeEventListener('pointercancel',end);suppressRegionClick=moved;};
  button.addEventListener('pointermove',move);button.addEventListener('pointerup',end);button.addEventListener('pointercancel',end);
}
function saveGeometry(translate) {
  if(!selected || busy) return;
  const payload={base_run:doc.latest_run,region_ids:geometryIds,bbox:geometryBox(),source_text:$('editSourceText').value,
    replacement:$('replacement').value,action:translate?'translate_apply':'apply',source_lang:$('sourceLang').value,target_lang:$('targetLang').value};
  task(translate?'선택 영역만 번역 API에 요청하고 해당 페이지에 적용합니다…':'영역을 저장하고 해당 페이지만 재배치합니다…',async()=>{
    const value=await api(`${base()}/regions/edit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const id=value.report.region_edit.region_id;
    for(const old of geometryIds){delete drafts[old];checkedRegions.delete(old);}
    drafts[id]=value.report.policy.replacements[id];
    useDocument(value);selectRegion(id);
    const result=value.report.regions.find(r=>r.id===id);
    const ok=result.status!=='NEEDS_REVIEW' && result.text_check && result.pdf_text_found!==false;
    const message=ok?'영역 저장·PDF 반영 확인. 위치와 줄바꿈을 확인하세요.':`영역 저장됨 · PDF 미반영 또는 검증 필요: ${result.reason}`;
    $('draftState').textContent=ok?'선택 영역 반영 확인':'미반영·검증 필요';
    $('geometryResult').textContent=message;notice(message,!ok);
  });
}
$('mergeRegionSelection').onclick=$('mergePdfSelection').onclick=prepareMerge;
$('geometryMode').onchange=()=>{
  if($('geometryMode').checked && $('regionView').value==='source'){$('regionView').value='placement';showPage();}
  $('regionLayer').classList.toggle('geometry-edit',$('geometryMode').checked);
  if($('geometryMode').checked){$('pdfMultiSelect').checked=false;$('showRegions').checked=true;$('regionLayer').hidden=false;}
  updateBatchControls();
};
for(const id of ['boxX','boxY','boxW','boxH']) $(id).oninput=()=>setGeometryBox(geometryBox());
$('saveRegionGeometry').onclick=()=>saveGeometry(false);
$('translateRegionGeometry').onclick=()=>saveGeometry(true);
function batchEnabled() {
  return !!doc?.report && $('mode').value === 'fit' && !doc.report.docx_output?.edited;
}
function batchEligible(region) {
  return !manualApplyBlock(region) &&
    !!(drafts[region.id] ?? region.text).trim();
}
function updateBatchControls() {
  const enabled = batchEnabled();
  const eligible = new Set(doc?.layout.pages.flatMap(p => p.regions).filter(batchEligible).map(r => r.id) || []);
  for (const id of checkedRegions) if (!eligible.has(id)) checkedRegions.delete(id);
  $('batchCount').textContent = `${checkedRegions.size}개 선택`;
  $('applyRegionsButton').textContent = `선택한 ${checkedRegions.size}개 영역 적용`;
  $('applyRegionsButton').disabled = busy || !enabled || !checkedRegions.size;
  $('pdfSelectionCount').textContent = `${checkedRegions.size}개 선택`;
  $('applyPdfSelectionButton').textContent = `선택한 ${checkedRegions.size}개 영역 적용`;
  $('applyPdfSelectionButton').disabled = $('applyRegionsButton').disabled;
  $('pdfMultiSelect').disabled = busy || !enabled;
  for (const id of ['saveRegionGeometry','translateRegionGeometry','geometryMode','editSourceText','boxX','boxY','boxW','boxH']) $(id).disabled=busy || !enabled || !selected || !!manualApplyBlock(selected);
  $('mergeRegionSelection').disabled=$('mergePdfSelection').disabled=busy || !enabled || checkedRegions.size<2;
  $('pdfSelectionHelp').textContent = !doc?.report ? '번역 결과를 생성하면 여러 영역을 선택할 수 있습니다.' :
    doc.report.docx_output?.edited ? 'DOCX 편집 결과는 DOCX 편집 화면에서 수정하세요.' :
    !enabled ? 'FIT 모드에서 교체 텍스트를 적용할 수 있습니다.' :
    $('pdfMultiSelect').checked ? '원본 영역을 클릭해 선택·해제합니다. 파란 ✓ 표시가 적용 대상이며, 페이지를 바꿔도 유지됩니다.' : '원본 클릭은 내용 살펴보기만 합니다. 아래 체크 목록에서도 여러 영역을 선택할 수 있습니다.';
  $('selectPageRegions').disabled = busy || !enabled;
  $('clearRegionSelection').disabled = busy || !checkedRegions.size;
  $('prepareDocxButton').disabled = busy;
  for (const input of $('batchRegions').querySelectorAll('input')) {
    input.checked = checkedRegions.has(input.value);
    input.disabled = busy || !enabled;
  }
  for (const el of $('regionLayer').children) {
    const checked = checkedRegions.has(el.dataset.region);
    el.classList.toggle('batch-selected', checked);
    el.setAttribute('aria-pressed', String(enabled && $('pdfMultiSelect').checked ? checked : el.dataset.region === selected?.id));
  }
}
function drawBatchRegions() {
  $('batchRegions').replaceChildren(...currentPage().regions.filter(batchEligible).map(region => {
    const row = document.createElement('div'); row.className = 'batch-region-row';
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = region.id;
    input.setAttribute('aria-label', `${region.id} 일괄 적용 선택`);
    input.onchange = () => { input.checked ? checkedRegions.add(region.id) : checkedRegions.delete(region.id); updateBatchControls(); };
    const text = document.createElement('span');
    text.textContent = `${region.id} · ${(drafts[region.id] ?? region.text).slice(0, 85).replaceAll('\n', ' ')}`;
    label.append(input, text);
    const inspect = document.createElement('button'); inspect.type = 'button'; inspect.className = 'secondary'; inspect.textContent = '살펴보기';
    inspect.onclick = () => selectRegion(region.id);
    row.append(label, inspect); return row;
  }));
  updateBatchControls();
}
function modeChanged() {
  const fit = $('mode').value === 'fit';
  $('fitPolicy').hidden = !fit;
  $('scaleValue').textContent = `${$('minScale').value}%`;
  $('modeHelp').textContent = fit ? '입력한 엔터·빈 줄·들여쓰기를 보존하고, 각 줄이 길면 영역 안에서 자동 줄바꿈합니다. 대체 폰트와 대표 서식을 사용하며 공간이 부족하면 원문을 유지합니다.' : '원본 폰트와 문자별 위치로 다시 그려 복원 정확도를 확인합니다. 교체 텍스트는 사용하지 않습니다.';
  $('renderButton').textContent = fit ? '교체 텍스트로 재생성' : '원문으로 재생성';
  $('replacement').disabled = busy || !selected || !fit;
  showRegionApplication();
  updateBatchControls();
}
function showOverlay() {
  overlay = overlay && !!doc?.latest_run && page < doc.layout.pages.length;
  $('overlayControls').hidden = !overlay;
  $('overlayImage').hidden = !overlay;
  $('overlayButton').classList.toggle('active', overlay);
  if (overlay) {
    $('overlayImage').src = imageUrl('new');
    $('overlayImage').style.opacity = $('opacity').value / 100;
  }
}
function showResults() {
  const report = doc.report, validation = report?.validation;
  const pass = validation?.status === 'CHECKS_PASSED';
  $('resultBadge').textContent = validation ? (pass ? '자동 검사 통과' : '검토 필요') : '실행 전';
  $('resultBadge').className = 'badge ' + (validation ? (pass ? 'pass' : 'review') : '');
  $('processed').textContent = validation ? `${validation.processed}/${validation.total}` : '—';
  $('review').textContent = validation ? report.regions.filter(r => r.status === 'NEEDS_REVIEW' || !r.text_check).length : '—';
  $('outside').textContent = validation?.pages[page] ? validation.pages[page].outside_changed_pixels.toLocaleString() : '—';
  $('validationScope').textContent = doc.report?.docx_output && doc.report.pdf_source !== 'layout' ? '아래 배치 검사는 DOCX 생성 전 중간 PDF 기준입니다. Word 폰트 대체·텍스트 상자 배치는 DOCX 미리보기에서 확인하세요.' : (validation?.scope || '생성한 PDF를 다시 열어 페이지 기하·텍스트·영역 밖 시각 변화를 검사합니다.');
  const issues = [...currentPage().warnings, ...(validation?.warnings || []), ...(doc.report?.docx_output?.warnings || [])];
  if (report) {
    issues.push(...(validation.pages[page]?.issues || []));
    for (const r of report.regions.filter(r => r.page === page)) {
      if (r.status === 'NEEDS_REVIEW') issues.push(`${r.id} · 원문 유지: ${r.reason}`);
      else if (r.status === 'ADJUSTED') issues.push(`${r.id} · ${Math.round(r.scale * 100)}% 크기로 조정`);
    }
    if (report.mode === 'fit') issues.push('FIT 출력은 대체 폰트로 조판했습니다. 전체 픽셀 차이는 번역 품질 점수가 아닙니다.');
    if (!issues.length) issues.push('현재 페이지에서 자동 검사 이상을 찾지 못했습니다. 시각 비교도 확인하세요.');
  } else if (!issues.length) issues.push('아직 생성 결과가 없습니다.');
  $('issues').replaceChildren(...issues.map(text => { const li = document.createElement('li'); li.textContent = text; return li; }));
  for (const [id, kind] of [['downloadPdf', 'new'], ['downloadReport', 'report']]) {
    const link = $(id); link.classList.toggle('disabled', !report); link.setAttribute('aria-disabled', String(!report));
    if (report) link.href = `${base()}/download/${kind}?run=${doc.latest_run}`;
    else link.removeAttribute('href');
  }
  $('downloadPdf').textContent = validation && !pass ? '검토용 PDF' : '결과 PDF';
}
$('uploadButton').onclick = () => $('sourceFile').click();
async function uploadFile(file) {
  if (!file || busy || (window.labDashboard && !window.labDashboard.canSwitch())) return;
  if (file.size > 25 * 1024 * 1024) { notice('25MB 이하 파일을 선택하세요.', true); return; }
  window.labDashboard?.applyUploadDefaults();
  await task('원본 형식과 문서 구조를 분석하고 있습니다…', async () => {
    const form = new FormData(); form.append('file', file);
    useDocument(await api('/api/documents', {method: 'POST', body: form}), true);
    await refreshDocuments(); notice('원본을 분석했습니다. 번역을 시작하거나 문서 살펴보기에서 영역을 확인하세요.');
  });
}
$('sourceFile').onchange = event => {
  uploadFile(event.target.files[0]); event.target.value = '';
};
$('baselineButton').onclick = () => $('baselineFile').click();
$('baselineFile').onchange = event => {
  const file = event.target.files[0]; if (!file || !doc) return;
  task('기존 출력 PDF를 추가하고 있습니다…', async () => {
    const form = new FormData(); form.append('file', file);
    useDocument(await api(`${base()}/baseline`, {method: 'POST', body: form}));
    notice('기존 출력이 연결되었습니다. 페이지 번호 기준으로 비교합니다.');
  }); event.target.value = '';
};
$('history').onchange = event => {
  if (!event.target.value) return;
  if (window.labDashboard && !window.labDashboard.canSwitch()) { $('history').value = doc?.id || ''; return; }
  task('문서를 불러오고 있습니다…', async () => {
    useDocument(await api(`/api/documents/${event.target.value}`), true);
    window.labDashboard?.showView('compare');
    if (doc.active_job) await waitForComparison(doc.active_job);
    else notice('문서를 불러왔습니다.');
  });
};
$('renderButton').onclick = () => task('새 엔진이 조판·재생성·출력 검증을 수행하고 있습니다…', async () => {
  const payload = {mode: $('mode').value, replacements: drafts, min_scale: Number($('minScale').value) / 100,
    preserve_spacing: $('preserveSpacing').checked, harmonize_repeats: $('harmonizeRepeats').checked,
    manual_regions: doc.report?.policy.manual_regions || [],
    min_font_size: Number($('minFont').value), line_height: Number($('lineHeight').value)};
  const result = await api(`${base()}/render`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  drafts = {...result.report.policy.replacements};
  useDocument(result);
  $('draftState').textContent = doc.report.mode === 'fit' ? '현재 실행 결과에 반영됨' : 'REPLAY에서는 교체문을 사용하지 않음';
  notice(doc.report.validation.status === 'CHECKS_PASSED' ? '재생성과 자동 검사가 완료되었습니다. 원본과 시각적으로도 비교해 주세요.' : '재생성이 완료되었습니다. 원문 유지 영역 또는 검증 이슈를 확인해 주세요.');
});
function applyRegions(ids) {
  if (!ids.length || busy) return;
  const all = doc.layout.pages.flatMap(p => p.regions);
  const replacements = Object.fromEntries(ids.map(id => [id, drafts[id] ?? all.find(r => r.id === id)?.text ?? '']));
  const payload = {mode: 'fit', apply_region_ids: ids, base_run: doc.latest_run, replacements};
  task(`${ids.length}개 영역이 있는 페이지를 갱신·검사하고 있습니다. 번역 API는 호출하지 않습니다…`, async () => {
    const value = await api(`${base()}/render`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const results = value.report.regions.filter(r => ids.includes(r.id));
    const passed = results.filter(r => r.manual_applied && r.status !== 'NEEDS_REVIEW' && r.text_check && r.pdf_text_found !== false);
    const unverified = results.filter(r => r.manual_applied && r.status !== 'NEEDS_REVIEW' && (!r.text_check || r.pdf_text_found === false));
    const unchanged = results.filter(r => r.status === 'UNCHANGED');
    [...passed, ...unchanged].forEach(r => checkedRegions.delete(r.id));
    useDocument(value);
    const failed = results.filter(r => r.status === 'NEEDS_REVIEW' || (!r.manual_applied && r.status !== 'UNCHANGED'));
    const text = `${passed.length}개 적용 확인 · ${unverified.length}개 배치 후 검증 필요 · ${unchanged.length}개 변경 없음 · ${failed.length}개 원문 유지 · ${value.report.render_seconds}초.`;
    const remaining = Object.entries(drafts).filter(([id, text]) => text !== (value.report.policy.replacements[id] ?? all.find(r => r.id === id)?.text)).length;
    $('draftState').textContent = failed.length || unverified.length ? '미반영·검증 필요 영역이 있습니다' : remaining ? `다른 영역 ${remaining}개 미반영 변경` : '선택 영역 처리 결과를 확인하세요';
    const reasons = failed.map(r => `${r.id}: ${r.reason}`).concat(unverified.map(r => `${r.id}: 배치됐지만 텍스트 검증을 통과하지 못했습니다. PDF에서 누락·겹침을 확인하세요.`));
    $('batchApplyResult').textContent = text + (reasons.length ? '\n' + reasons.join('\n') : ' PDF에서 위치와 글자를 확인하세요.');
    notice(text + (ids.length === 1 && reasons.length ? '\n' + reasons[0] : ' 아래 영역별 반영 결과를 확인하세요.'), !!(failed.length || unverified.length));
    drawBatchRegions();
  });
}
$('applyRegionButton').onclick = () => { if (selected) applyRegions([selected.id]); };
$('applyRegionsButton').onclick = () => applyRegions([...checkedRegions]);
$('applyPdfSelectionButton').onclick = () => applyRegions([...checkedRegions]);
$('pdfMultiSelect').onchange = () => { if ($('pdfMultiSelect').checked) { $('geometryMode').checked=false;$('regionLayer').classList.remove('geometry-edit');$('showRegions').checked = true; $('regionLayer').hidden = false; } updateBatchControls(); };
$('selectPageRegions').onclick = () => { currentPage().regions.filter(batchEligible).forEach(r => checkedRegions.add(r.id)); updateBatchControls(); };
$('clearRegionSelection').onclick = () => { checkedRegions.clear(); updateBatchControls(); };
$('prepareDocxButton').onclick = () => task('현재 PDF의 DOCX 편집본을 생성하고 있습니다…', async () => {
  useDocument(await api(`${base()}/editable-docx?run=${doc.latest_run}`, {method: 'POST'}));
  notice('DOCX 편집본을 생성했습니다. DOCX 편집 또는 내보내기를 이용하세요.');
});
$('previous').onclick = () => { if (page > 0) { page--; showPage(); } };
$('next').onclick = () => { if (doc && page + 1 < pageTotal()) { page++; showPage(); } };
$('regions').onchange = e => selectRegion(e.target.value);
$('mode').onchange = modeChanged;
$('minScale').oninput = modeChanged;
$('replacement').oninput = e => {
  if (!selected) return;
  drafts[selected.id] = e.target.value;
  $('draftState').textContent = '미반영 변경 · 재생성 필요';
  showRegionApplication();
  drawBatchRegions();
};
$('showRegions').onchange = () => { $('regionLayer').hidden = !$('showRegions').checked; };
$('overlayButton').onclick = () => { overlay = !overlay; showOverlay(); };
$('opacity').oninput = showOverlay;
$('zoom').onchange = () => {
  for (const paper of document.querySelectorAll('.paper')) paper.style.width = `${$('zoom').value}%`;
};
let scrollLock = false;
for (const viewport of document.querySelectorAll('.viewport')) viewport.addEventListener('scroll', () => {
  if (scrollLock || !$('syncScroll').checked) return;
  scrollLock = true;
  const ratio = viewport.scrollTop / Math.max(1, viewport.scrollHeight - viewport.clientHeight);
  const horizontal = viewport.scrollLeft / Math.max(1, viewport.scrollWidth - viewport.clientWidth);
  for (const other of document.querySelectorAll('.viewport')) if (other !== viewport) {
    other.scrollTop = ratio * (other.scrollHeight - other.clientHeight);
    other.scrollLeft = horizontal * (other.scrollWidth - other.clientWidth);
  }
  requestAnimationFrame(() => { scrollLock = false; });
});
for (const kind of ['source', 'new', 'baseline']) $(`${kind}Image`).onerror = () => {
  $(`${kind}Paper`).hidden = true; $(`${kind}Empty`).hidden = false;
  $(`${kind}Empty`).querySelector('p').textContent = '페이지 이미지를 불러오지 못했습니다. 문서를 다시 선택해 주세요.';
};
refreshDocuments().then(async () => {
  const requested = new URLSearchParams(location.search).get('document');
  if (requested && /^[a-f0-9]{32}$/.test(requested)) {
    await task('문서를 불러오고 있습니다…', async () => {
      useDocument(await api(`/api/documents/${requested}`), true);
      if (doc.active_job) await waitForComparison(doc.active_job);
      else notice('문서를 불러왔습니다. 번역 적용률과 검토 항목을 확인하세요.');
    });
  }
}).catch(error => notice(error.message, true));
api('/api/config').then(value => {
  apiConfig = value;
  $('apiStatus').textContent = value.configured ? `${value.provider} · ${value.model} (기존 설정)` : value.error || '번역 API 키가 없습니다. backend/.env 설정을 확인하세요.';
  $('compareButton').disabled = busy || !doc || !value.configured;
}).catch(error => { $('apiStatus').textContent = error.message; });

async function waitForComparison(jobId) {
  try { while (true) {
    const job = await api(`/api/jobs/${jobId}`);
    showJob(job);
    notice(job.message);
    if (job.status === 'failed') throw new Error(job.message);
    if (job.status === 'cancelled') { notice(job.message); return; }
    if (job.status === 'completed' || job.status === 'partial') {
      useDocument(await api(base()), true);
      $('mode').value = 'fit'; modeChanged();
      notice(job.message);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  } } finally { clearInterval(jobTimer); }
}
$('compareButton').onclick = () => task('번역·비교 작업을 시작합니다…', async () => {
  const job = await api(`${base()}/compare`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    source_lang:$('sourceLang').value, target_lang:$('targetLang').value,
    include_baseline: window.labDashboard?.includeBaseline() ?? true,
    preserve_spacing:$('preserveSpacing').checked, harmonize_repeats:$('harmonizeRepeats').checked,
    min_scale:Number($('minScale').value)/100, min_font_size:Number($('minFont').value), line_height:Number($('lineHeight').value)
  })});
  showJob(job);
  await waitForComparison(job.id);
  if (['completed', 'partial'].includes(displayedJob?.status)) window.labDashboard?.showView('compare');
});

function updateExports() {
  $('analyzeExports').disabled = !doc?.latest_run;
  const policy = doc?.exports;
  const select = $('exportFormat');
  const previous = select.dataset.document === doc?.id ? select.value : null;
  select.replaceChildren();
  for (const option of policy?.options || []) {
    select.add(new Option(option.label, option.format));
  }
  if (!select.options.length) select.add(new Option('결과 생성 후 선택', ''));
  select.value = [...select.options].some(o => o.value === previous) ? previous : (policy?.default_format || '');
  select.dataset.document = doc?.id || '';
  select.disabled = !policy?.options?.length;
  exportSelectionChanged();
}
function exportSelectionChanged() {
  const option = doc?.exports?.options.find(item => item.format === $('exportFormat').value);
  $('exportButton').disabled = !option?.available;
  $('exportHelp').textContent = option?.description || '현재 PDF 입력을 지원합니다. 결과 구조와 지원 여부에 맞는 형식만 표시합니다.';
}
$('exportFormat').addEventListener('change', exportSelectionChanged);
$('exportButton').addEventListener('click', async () => {
  if (!doc || busy) return;
  const selectedFormat = $('exportFormat').value;
  await task('최신 출력 상태를 확인하고 내보내는 중…', async () => {
    useDocument(await api(base()));
    const choice = doc.exports.options.find(item => item.format === selectedFormat);
    if (!choice?.available) throw new Error(choice?.description || '이 출력 형식은 현재 사용할 수 없습니다.');
    const response = await fetch(`${base()}/export/${selectedFormat}?run=${doc.latest_run}`);
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || '내보내기에 실패했습니다.');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = doc.name.replace(/\.[^.]+$/, '') + '-layout.' + selectedFormat;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    notice(`${selectedFormat.toUpperCase()} 파일을 내보냈습니다.`);
  });
});

$('analyzeExports').onclick = () => task('페이지 구조와 PPTX 변환 결과를 확인하고 있습니다…', async () => { useDocument(await api(`${base()}/smart-exports`, {method:'POST'})); notice('검증된 PPTX Export를 추가했습니다.'); });
