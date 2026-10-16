const $ = id => document.getElementById(id);
let doc = null, page = 0, selected = null, drafts = {}, busy = false, overlay = false;
let apiConfig = {configured:false};
const currentPage = () => doc?.layout.pages[page] || {regions:[],warnings:[],width:1,height:1};
const pageTotal = () => Math.max(doc?.layout.pages.length || 0, doc?.baseline?.page_count || 0, doc?.report?.docx_output?.page_count || 0);

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
  for (const id of ['uploadButton', 'renderButton', 'baselineButton', 'history', 'mode', 'replacement', 'minScale', 'minFont', 'lineHeight', 'compareButton', 'sourceLang', 'targetLang']) $(id).disabled = true;
  notice(message);
  try { await action(); } catch (error) { notice(error.message, true); }
  finally {
    busy = false; document.body.classList.remove('busy');
    for (const id of ['uploadButton', 'history', 'mode', 'minScale', 'minFont', 'lineHeight', 'sourceLang', 'targetLang']) $(id).disabled = false;
    $('renderButton').disabled = !doc;
    $('baselineButton').disabled = !doc;
    $('compareButton').disabled = !doc || !apiConfig.configured;
    $('replacement').disabled = !selected || $('mode').value !== 'fit';
  }
}
async function history() {
  const list = await api('/api/documents');
  $('history').replaceChildren(new Option('PDF를 선택하세요', ''));
  for (const item of list) $('history').add(new Option(item.name, item.id));
  if (doc) $('history').value = doc.id;
}
function base() { return `/api/documents/${doc.id}`; }
function imageUrl(kind, number = page) {
  const version = kind === 'baseline' ? doc.baseline?.version : doc.latest_run;
  return `${base()}/pages/${kind}/${number}?run=${doc.latest_run || ''}&v=${version || doc.id}`;
}
function useDocument(value, fresh = false) {
  if (value.native) { doc = value; location.href = `/native?document=${value.id}`; return; }
  doc = value;
  if (fresh) {
    page = 0; selected = null; drafts = {}; overlay = false;
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
  $('comparisonStatus').hidden = !comparison && !doc.baseline;
  $('comparisonStatus').classList.toggle('warning', !comparison || comparison.legacy_status !== 'ready' || doc.report?.validation.status !== 'CHECKS_PASSED');
  if (comparison) {
    const v = doc.report.validation;
    const status = `번역 배치 ${v.translated ?? v.processed}/${v.total - (v.unchanged || 0)}개 (${Math.round((v.translated ?? v.processed) / Math.max(1,v.total - (v.unchanged || 0))*100)}%) · 배치 실패 ${v.skipped}개 · 변경 없는 기호/원문 ${v.unchanged || 0}개 · ${v.status === 'CHECKS_PASSED' ? '자동 검사 통과' : '검토 필요'} · ${comparison.legacy_status === 'ready' ? '기존 PDF 비교 가능' : '기존 PDF 변환 미완료'}`;
    $('comparisonStatus').textContent = `${status}\n${comparison.provider} · ${comparison.model} · ${comparison.source_lang} → ${comparison.target_lang} · ${comparison.cache_hit ? '저장된 번역 재사용' : 'API 번역 완료'}\n${comparison.scope}${comparison.legacy_error ? '\n' + comparison.legacy_error : ''}${comparison.unchanged_ids.length ? '\n원문과 동일한 번역 ' + comparison.unchanged_ids.length + '개: 고유명사 또는 번역 실패 여부를 확인하세요.' : ''}`;
  } else if (doc.baseline) $('comparisonStatus').textContent = '수동 비교: 현재 새 출력과 기존 PDF가 동일한 번역문을 사용했는지 확인되지 않았습니다.';
  for (const [id, kind, available] of [['downloadDocx','docx',comparison?.docx_ready], ['downloadTranslation','translation',!!comparison]]) {
    $(id).hidden = !available;
    if (available) $(id).href = `${base()}/download/${kind}?run=${comparison.run_id}`;
  }
  const newDocx = doc.report?.docx_output;
  $('downloadNewDocx').hidden = $('editNewDocx').hidden = !newDocx?.ready;
  if (newDocx?.ready) {
    $('downloadNewDocx').href = `${base()}/download/new-docx?run=${doc.latest_run}`;
    $('editNewDocx').href = `/editor?document=${doc.id}&run=${doc.latest_run}`;
    $('comparisonStatus').hidden = false;
    const preview = newDocx.preview_status === 'ready' ? '기본 출력은 PDF입니다. 현재 PDF는 추가 DOCX 결과를 문서 서버에서 렌더링한 파일입니다.' :
      newDocx.preview_status === 'stale' ? '편집 후 미리보기가 아직 갱신되지 않았습니다. 편집기의 저장·갱신 버튼을 누르세요.' :
      '기본 출력인 레이아웃 PDF를 표시합니다. DOCX는 추가 Export로 다운로드·편집할 수 있습니다.';
    if (!comparison && !doc.baseline) $('comparisonStatus').textContent = '';
    $('comparisonStatus').textContent += `\n${preview}\n${newDocx.scope}`;
    if (newDocx.edited) $('comparisonStatus').textContent += '\n추가 DOCX를 수동 편집했습니다. 저장·갱신한 PDF에는 편집 내용이 반영되며 기존 출력과 번역문이 달라질 수 있습니다.';
  }
  updateExports();
  showPage(); modeChanged();
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
  showImage('source', hasSource); showImage('new', !!doc.latest_run && page < (doc.report?.docx_output?.page_count || doc.layout.pages.length));
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
    button.addEventListener('click', () => selectRegion(region.id));
    $('regionLayer').append(button);
  }
  $('regionLayer').hidden = !$('showRegions').checked;
  selectRegion(current.regions.find(r => r.id === selected?.id)?.id || current.regions[0]?.id);
  showResults(); showOverlay();
}
function selectRegion(id) {
  selected = currentPage().regions.find(r => r.id === id) || null;
  $('regions').value = selected?.id || '';
  for (const el of $('regionLayer').children) el.classList.toggle('selected', el.dataset.region === selected?.id);
  $('originalText').value = selected?.text || '';
  $('replacement').value = selected ? (drafts[selected.id] ?? selected.text) : '';
  $('replacement').disabled = busy || !selected || $('mode').value !== 'fit';
  $('regionMeta').textContent = selected ? `${selected.fonts.join(', ')} · ${selected.font_size.toFixed(1)}pt · ${selected.bbox.map(n => n.toFixed(1)).join(', ')} pt${selected.warnings.length ? ' · ' + selected.warnings.join(' ') : ''}` : '이 페이지에는 편집 가능한 텍스트 영역이 없습니다.';
}
function modeChanged() {
  const fit = $('mode').value === 'fit';
  $('fitPolicy').hidden = !fit;
  $('scaleValue').textContent = `${$('minScale').value}%`;
  $('modeHelp').textContent = fit ? '영역을 고정하고 줄바꿈·크기를 조정합니다. 대체 폰트와 대표 서식을 사용합니다. 혼합 스타일의 세부 강조는 동일하게 유지되지 않을 수 있습니다.' : '원본 폰트와 문자별 위치로 다시 그려 복원 정확도를 확인합니다. 교체 텍스트는 사용하지 않습니다.';
  $('renderButton').textContent = fit ? '교체 텍스트로 재생성' : '원문으로 재생성';
  $('replacement').disabled = busy || !selected || !fit;
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
  $('validationScope').textContent = doc.report?.docx_output ? '아래 배치 검사는 DOCX 생성 전 중간 PDF 기준입니다. Word 폰트 대체·텍스트 상자 배치는 DOCX 미리보기에서 확인하세요.' : (validation?.scope || '생성한 PDF를 다시 열어 페이지 기하·텍스트·영역 밖 시각 변화를 검사합니다.');
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
$('sourceFile').onchange = event => {
  const file = event.target.files[0]; if (!file) return;
  task('원본 형식과 문서 구조를 분석하고 있습니다…', async () => {
    const form = new FormData(); form.append('file', file);
    useDocument(await api('/api/documents', {method: 'POST', body: form}), true);
    await history(); notice('원본을 분석했습니다. 원문으로 재생성해 복원 정확도를 확인하세요.');
  }); event.target.value = '';
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
  task('문서를 불러오고 있습니다…', async () => {
    useDocument(await api(`/api/documents/${event.target.value}`), true);
    if (doc.active_job) await waitForComparison(doc.active_job);
    else notice('문서를 불러왔습니다.');
  });
};
$('renderButton').onclick = () => task('새 엔진이 조판·재생성·출력 검증을 수행하고 있습니다…', async () => {
  const payload = {mode: $('mode').value, replacements: drafts, min_scale: Number($('minScale').value) / 100,
    min_font_size: Number($('minFont').value), line_height: Number($('lineHeight').value)};
  useDocument(await api(`${base()}/render`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));
  $('draftState').textContent = doc.report.mode === 'fit' ? '현재 실행 결과에 반영됨' : 'REPLAY에서는 교체문을 사용하지 않음';
  notice(doc.report.validation.status === 'CHECKS_PASSED' ? '재생성과 자동 검사가 완료되었습니다. 원본과 시각적으로도 비교해 주세요.' : '재생성이 완료되었습니다. 원문 유지 영역 또는 검증 이슈를 확인해 주세요.');
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
history().then(async () => {
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
  while (true) {
    const job = await api(`/api/jobs/${jobId}`);
    notice(job.message);
    if (job.status === 'failed') throw new Error(job.message);
    if (job.status === 'completed' || job.status === 'partial') {
      useDocument(await api(base()), true);
      $('mode').value = 'fit'; modeChanged();
      notice(job.message);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}
$('compareButton').onclick = () => task('번역·비교 작업을 시작합니다…', async () => {
  const job = await api(`${base()}/compare`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    source_lang:$('sourceLang').value, target_lang:$('targetLang').value,
    min_scale:Number($('minScale').value)/100, min_font_size:Number($('minFont').value), line_height:Number($('lineHeight').value)
  })});
  await waitForComparison(job.id);
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
