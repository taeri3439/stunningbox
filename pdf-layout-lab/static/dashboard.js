/* Dashboard shell adapted from ui-ux-share. PDF operations stay in app.js. */
(() => {
  const views = {new:'새 번역', compare:'결과 비교', quality:'품질 관리', history:'작업 기록', settings:'설정'};
  const defaults = {source:'ko', target:'en', mode:'new', zoom:'100', regions:true, sync:true};
  const prefKey = 'pdf-layout-lab.dashboard.v1';
  let preferences = {...defaults}, runMode = 'new', view = 'new';
  let historyOffset = 0, qualityOffset = 0, historySequence = 0, documentSequence = 0;
  let reviewItems = {}, reviewDrafts = {}, reviewRun = '', reviewReady = false, runItems = [];
  let recentSequence = 0, openedId = '', loadedRun = '', focusedPane = '';
  try { preferences = {...defaults, ...JSON.parse(localStorage.getItem(prefKey) || '{}')}; } catch { /* Defaults work without storage. */ }
  for (const [key, id] of [['source','sourceLang'],['target','targetLang'],['zoom','zoom']]) {
    if (![...$(id).options].some(option => option.value === preferences[key])) preferences[key] = defaults[key];
  }
  if (!['new','compare'].includes(preferences.mode)) preferences.mode = 'new';
  const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; };
  const date = value => new Date(value).toLocaleString('ko-KR', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  const labels = {uploaded:'업로드됨', running:'처리 중', ready:'자동 검사 통과', needs_review:'검토 필요'};
  const decisions = {pending:'미검토', approved:'확인 완료', needs_fix:'수정 요청'};
  const issue = result => result?.review_required || result?.status === 'NEEDS_REVIEW' || (['FIT','ADJUSTED'].includes(result?.status) && (!result.text_check || result.pdf_text_found === false));
  const appliedLabel = result => !result ? '미생성' : result.status === 'NEEDS_REVIEW' ? 'PDF 미반영' : result.status === 'UNCHANGED' ? '원문 유지' : issue(result) ? '번역 적용 · 검토 필요' : 'PDF 반영됨';

  function showView(next, updateHash = true) {
    view = Object.hasOwn(views, next) ? next : 'new';
    for (const section of document.querySelectorAll('[data-page]')) section.hidden = section.dataset.page !== view;
    for (const button of document.querySelectorAll('[data-view]')) {
      button.classList.toggle('active', button.dataset.view === view);
      if (button.dataset.view === view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
    }
    $('currentViewName').textContent = views[view];
    if (updateHash) window.history.replaceState(null, '', location.pathname + location.search + '#' + view);
    if (view === 'history') loadHistory();
    if (view === 'quality') renderQuality();
  }
  document.querySelectorAll('[data-view],[data-go]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view || button.dataset.go)));
  addEventListener('hashchange', () => showView(location.hash.slice(1), false));
  $('collapseSidebar').onclick = () => {
    const collapsed = document.querySelector('.dashboard-shell').classList.toggle('collapsed');
    $('collapseSidebar').setAttribute('aria-expanded', String(!collapsed));
    $('collapseSidebar').setAttribute('aria-label', collapsed ? '사이드 메뉴 펼치기' : '사이드 메뉴 접기');
    try { localStorage.setItem('pdf-layout-lab.sidebar', String(collapsed)); } catch { /* Optional preference. */ }
  };
  try { if (localStorage.getItem('pdf-layout-lab.sidebar') === 'true') $('collapseSidebar').click(); } catch { /* Optional preference. */ }

  function setRunMode(mode) {
    runMode = mode === 'compare' ? 'compare' : 'new';
    document.querySelectorAll('[data-run-mode]').forEach(button => {
      button.classList.toggle('selected', button.dataset.runMode === runMode);
      button.setAttribute('aria-pressed', String(button.dataset.runMode === runMode));
    });
    $('summaryMode').textContent = runMode === 'new' ? '새 레이아웃만' : '두 방식 비교';
    $('runModeHint').textContent = runMode === 'new' ? '새 PDF와 편집용 DOCX를 생성합니다. 기존 프로젝트 처리와 DOCX 미리보기 변환을 생략합니다.' : '새 레이아웃과 실제 기존 프로젝트 파이프라인을 모두 실행합니다. 추가 번역·변환 시간이 필요합니다.';
    $('compareButton').textContent = runMode === 'new' ? '번역 시작' : '번역·비교 시작';
  }
  document.querySelectorAll('[data-run-mode]').forEach(button => button.onclick = () => { if (!busy) setRunMode(button.dataset.runMode); });
  function summary() {
    $('summaryLanguages').textContent = `${$('sourceLang').selectedOptions[0]?.text || ''} → ${$('targetLang').selectedOptions[0]?.text || ''}`;
    if (doc && !doc.native) {
      $('summaryName').textContent = $('uploadName').textContent = $('compareName').textContent = doc.name;
      $('summaryPages').textContent = `${doc.layout.pages.length}페이지 / ${doc.layout.region_count.toLocaleString()}개`;
      $('qualityName').textContent = `${doc.name} · ${doc.latest_run ? '실행 ' + doc.latest_run.slice(0,8) : '결과 생성 전'}`;
    }
    $('openCurrent').disabled = !doc || busy;
    $('swapLanguages').disabled = busy;
    $('reloadDocument').disabled = !doc || busy;
    document.querySelectorAll('[data-run-mode]').forEach(button => button.disabled = busy);
    $('settingsApi').textContent = apiConfig.configured ? `${apiConfig.provider} · ${apiConfig.model} · 연결 설정됨` : $('apiStatus').textContent;
  }
  $('sourceLang').addEventListener('change', summary); $('targetLang').addEventListener('change', summary);
  $('swapLanguages').onclick = () => { if (busy) return; const original = $('sourceLang').value; $('sourceLang').value = $('targetLang').value; $('targetLang').value = original; summary(); };
  $('openCurrent').onclick = () => showView('compare');
  const unsavedText = () => !!doc && Object.entries(drafts).some(([id,value]) => value !== (doc.report?.policy?.replacements?.[id] ?? doc.layout.pages.flatMap(p => p.regions).find(r => r.id === id)?.text));
  const unsavedReviews = () => Object.keys(reviewDrafts).length > 0;
  const canSwitch = () => !busy && (!(unsavedText() || unsavedReviews()) || confirm('아직 저장·반영하지 않은 교체문 또는 검토 메모가 있습니다. 다른 문서를 불러오면 이 변경은 사라집니다. 계속할까요?'));
  for (const id of ['renderButton','applyRegionButton','compareButton']) $(id).addEventListener('click', event => {
    if (busy) return;
    if ((unsavedReviews() || (id === 'compareButton' && unsavedText())) &&
        !confirm('새 실행 결과로 전환하면 미저장 검토 메모가 사라집니다. API 번역은 수동 교체문 대신 저장된 번역을 사용합니다. 계속할까요?')) {
      event.stopImmediatePropagation(); event.preventDefault();
    }
  }, true);
  addEventListener('beforeunload', event => { if (unsavedText() || unsavedReviews()) { event.preventDefault(); event.returnValue = ''; } });
  $('reloadDocument').onclick = () => {
    if (!doc || !canSwitch()) return;
    task('현재 문서를 다시 불러옵니다…', async () => {
      useDocument(await api(base()), true);
      if (doc.active_job) await waitForComparison(doc.active_job); else notice('최신 결과를 불러왔습니다.');
    });
  };
  const zone = $('dropzone');
  for (const event of ['dragenter','dragover']) zone.addEventListener(event, e => { e.preventDefault(); if (!busy) zone.classList.add('dragging'); });
  for (const event of ['dragleave','drop']) zone.addEventListener(event, e => { e.preventDefault(); zone.classList.remove('dragging'); });
  zone.addEventListener('drop', e => {
    if (e.dataTransfer.files.length !== 1) { notice('파일을 한 개씩 올려주세요.', true); return; }
    uploadFile(e.dataTransfer.files[0]);
  });
  document.querySelectorAll('[data-focus]').forEach(button => button.onclick = () => {
    focusedPane = focusedPane === button.dataset.focus ? '' : button.dataset.focus;
    $('panes').classList.toggle('focused', !!focusedPane);
    document.querySelectorAll('[data-pane]').forEach(pane => pane.classList.toggle('is-focused', pane.dataset.pane === focusedPane));
    document.querySelectorAll('[data-focus]').forEach(el => { el.setAttribute('aria-pressed', String(el.dataset.focus === focusedPane)); el.textContent = el.dataset.focus === focusedPane ? '↙' : '⤢'; });
  });

  async function openDocument(id) {
    if (!canSwitch()) return;
    await task('저장된 문서를 불러옵니다…', async () => {
      useDocument(await api(`/api/documents/${id}`), true); showView('compare');
      if (doc.active_job) await waitForComparison(doc.active_job); else notice('저장된 문서와 결과를 불러왔습니다.');
    });
  }
  function historyRows(items) {
    if (!items.length) return [text('p', '조건에 맞는 저장된 작업이 없습니다.', 'empty-message')];
    return items.map(item => {
      const row = text('div', '', 'history-row');
      const icon = text('span', item.native ? 'DOC' : 'PDF', 'file-type');
      const detail = text('div', '', 'history-detail');
      const button = text('button', item.name, 'file-link'); button.onclick = () => openDocument(item.id); button.disabled = busy;
      detail.append(button, text('small', `${date(item.updated_at)}${item.source_lang ? ' · ' + item.source_lang + ' → ' + item.target_lang : ''}${typeof item.elapsed_seconds === 'number' ? ' · ' + duration(item.elapsed_seconds) : ''}`));
      const badge = text('span', item.native ? '문서 편집' : labels[item.status], 'status-pill ' + item.status);
      row.append(icon, detail, badge); return row;
    });
  }
  async function loadRecent() {
    const seq = ++recentSequence;
    try { const result = await api('/api/history?limit=5'); if (seq === recentSequence) $('recentJobs').replaceChildren(...historyRows(result.items)); }
    catch (error) { $('recentJobs').replaceChildren(text('p',error.message,'empty-message')); }
  }
  async function loadHistory() {
    const seq = ++historySequence;
    try {
      const params = new URLSearchParams({q:$('historySearch').value, status:$('historyStatus').value, offset:historyOffset, limit:10});
      const result = await api('/api/history?' + params);
      if (seq !== historySequence) return;
      $('historyList').replaceChildren(...historyRows(result.items));
      $('historyCount').textContent = `${result.total}개 · ${result.total ? historyOffset + 1 : 0}–${historyOffset + result.items.length}`;
      $('historyPrev').disabled = historyOffset === 0;
      $('historyNext').disabled = historyOffset + 10 >= result.total;
    } catch(error) { notice(error.message, true); }
  }
  let searchTimer;
  $('historySearch').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { historyOffset = 0; loadHistory(); }, 200); };
  $('historyStatus').onchange = () => { historyOffset = 0; loadHistory(); };
  $('historyPrev').onclick = () => { historyOffset = Math.max(0, historyOffset - 10); loadHistory(); };
  $('historyNext').onclick = () => { historyOffset += 10; loadHistory(); };
  $('refreshHistory').onclick = () => { loadHistory(); loadRecent(); };

  function renderRuns() {
    $('runHistory').replaceChildren(...(runItems.length ? runItems.map(run => {
      const row = text('div','','run-row');
      row.append(text('span', `${date(run.updated_at)} · ${run.mode?.toUpperCase()} · ${run.id.slice(0,8)}${run.id === doc.latest_run ? ' · 현재 결과' : ''}${run.manual_region ? ' · 영역 수정' : ''}`));
      for (const [kind,label] of [['new','PDF 열기'],['report','검증 JSON']]) {
        const link = text('a', label, 'text-button'); link.href = `${base()}/download/${kind}?run=${run.id}`; link.target='_blank'; link.rel='noopener'; row.append(link);
      }
      return row;
    }) : [text('p','아직 저장된 실행 결과가 없습니다.','helper')]));
  }
  async function loadDocumentDetails(fresh) {
    if (!doc || doc.native) return;
    const id = doc.id, run = doc.latest_run || '', changed = openedId !== id || loadedRun !== run;
    if (fresh || changed) {
      openedId = id; loadedRun = run; reviewItems = {}; reviewDrafts = {}; reviewRun = ''; reviewReady = false; runItems = []; qualityOffset = 0;
      $('qualityPage').replaceChildren(new Option('모든 페이지',''));
      doc.layout.pages.forEach((p,i) => $('qualityPage').add(new Option(`${i+1}페이지`,String(i))));
      $('reviewSaved').textContent = '';
    }
    const url = new URL(location.href); url.searchParams.set('document',id); window.history.replaceState(null,'',url);
    summary(); renderQuality(); renderReview();
    if (!fresh && !changed) return;
    const seq = ++documentSequence;
    const outcomes = await Promise.allSettled([api(`${base()}/runs`), run ? api(`${base()}/reviews?run=${run}`) : Promise.resolve({run:'',items:{}})]);
    if (seq !== documentSequence || doc.id !== id || (doc.latest_run || '') !== run) return;
    if (outcomes[0].status === 'fulfilled') { runItems = outcomes[0].value.items; renderRuns(); }
    else $('runHistory').textContent = outcomes[0].reason.message;
    if (outcomes[1].status === 'fulfilled') { reviewRun = run; reviewItems = outcomes[1].value.items; reviewReady = true; }
    else notice('검토 기록을 불러오지 못했습니다. 문서를 새로고침하세요.', true);
    renderQuality(); renderReview(); loadRecent();
  }
  function qualityRows() {
    if (!doc?.report) return [];
    const source = new Map(doc.layout.pages.flatMap(p => p.regions).map(region => [region.id,region]));
    return doc.report.regions.map(result => ({result, source:source.get(result.id), review:reviewItems[result.id] || {decision:'pending', note:''}}));
  }
  function renderQuality() {
    const rows = qualityRows();
    $('qualityTotal').textContent = doc?.report ? rows.length.toLocaleString() : '—';
    $('qualityIssues').textContent = doc?.report ? rows.filter(row => issue(row.result)).length.toLocaleString() : '—';
    $('qualityApproved').textContent = rows.filter(row => row.review.decision === 'approved').length.toLocaleString();
    $('qualityFixes').textContent = rows.filter(row => row.review.decision === 'needs_fix').length.toLocaleString();
    $('downloadReviews').disabled = !doc?.latest_run || !reviewReady;
    const query = $('qualitySearch').value.trim().toLocaleLowerCase(), filter = $('qualityFilter').value;
    const filtered = rows.filter(row => {
      if ($('qualityPage').value !== '' && row.result.page !== Number($('qualityPage').value)) return false;
      if (filter === 'issues' && !issue(row.result)) return false;
      if (filter === 'unapplied' && row.result.status !== 'NEEDS_REVIEW') return false;
      if (['pending','approved','needs_fix'].includes(filter) && row.review.decision !== filter) return false;
      return !query || [row.source?.text, row.result.text, row.result.id, row.result.reason, row.review.note].join(' ').toLocaleLowerCase().includes(query);
    });
    qualityOffset = Math.min(qualityOffset, Math.max(0, Math.floor((filtered.length - 1)/20)*20));
    const items = filtered.slice(qualityOffset, qualityOffset + 20).map(row => {
      const card = text('button', '', 'quality-row'); card.type='button';
      const heading = text('div','','quality-row-heading');
      heading.append(text('strong',`${row.result.page+1}페이지 · ${row.result.id}`),text('span', appliedLabel(row.result),'status-pill ' + (issue(row.result) ? 'needs_review' : 'ready')),text('span',decisions[row.review.decision],'review-pill'));
      card.append(heading, text('p', row.source?.text || '(원문 없음)', 'source-snippet'), text('p',row.result.text || '원문 유지','translation-snippet'));
      if (issue(row.result)) card.append(text('small',(row.result.review_reasons||[]).join(' ') || row.result.reason || '최종 PDF에서 텍스트를 확인하세요.','issue-reason'));
      if (row.review.note) card.append(text('small', '메모 · ' + row.review.note,'review-memo'));
      card.onclick = () => {
        page = row.result.page; showView('compare'); showPage(); selectRegion(row.result.id);
        document.querySelector('.editor').scrollIntoView({behavior:'smooth',block:'center'}); $('replacement').focus({preventScroll:true});
      };
      return card;
    });
    $('qualityList').replaceChildren(...(items.length ? items : [text('p',doc?.report ? '조건에 맞는 영역이 없습니다.' : '결과를 생성하거나 작업 기록에서 문서를 여세요.','empty-message')]));
    $('qualityCount').textContent = `${filtered.length}개 · ${filtered.length ? qualityOffset + 1 : 0}–${qualityOffset + items.length}`;
    $('qualityPrev').disabled = qualityOffset === 0; $('qualityNext').disabled = qualityOffset + 20 >= filtered.length;
  }
  for (const id of ['qualitySearch','qualityFilter','qualityPage']) $(id).addEventListener(id === 'qualitySearch' ? 'input' : 'change', () => { qualityOffset = 0; renderQuality(); });
  $('qualityPrev').onclick = () => { qualityOffset -= 20; renderQuality(); };
  $('qualityNext').onclick = () => { qualityOffset += 20; renderQuality(); };
  function renderReview() {
    const enabled = !!selected && !!doc?.latest_run && reviewReady && reviewRun === doc.latest_run && !busy;
    const saved = selected ? reviewDrafts[selected.id] || reviewItems[selected.id] : null;
    $('reviewDecision').value = saved?.decision || 'pending'; $('reviewNote').value = saved?.note || '';
    for (const id of ['reviewDecision','reviewNote','saveReview']) $(id).disabled = !enabled;
    $('reviewSaved').textContent = selected && reviewDrafts[selected.id] ? '미저장 변경' : saved?.updated_at ? `저장됨 · ${date(saved.updated_at)}` : doc?.latest_run && !reviewReady ? '검토 기록 불러오는 중…' : '';
  }
  function editReview() {
    if (!selected || !reviewReady) return;
    const value = {decision:$('reviewDecision').value,note:$('reviewNote').value};
    const saved = reviewItems[selected.id] || {decision:'pending', note:''};
    if (value.decision === saved.decision && value.note === saved.note) delete reviewDrafts[selected.id]; else reviewDrafts[selected.id] = value;
    $('reviewSaved').textContent = reviewDrafts[selected.id] ? '미저장 변경' : '';
  }
  $('reviewDecision').onchange = editReview; $('reviewNote').oninput = editReview;
  $('saveReview').onclick = () => {
    if (!selected || busy || !reviewReady) return;
    const id = selected.id, run = doc.latest_run;
    const payload = {run, region_id:id, decision:$('reviewDecision').value, note:$('reviewNote').value};
    task('검토 기록을 저장합니다…', async () => {
      const result = await api(`${base()}/reviews`, {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      reviewItems = result.items; delete reviewDrafts[id]; renderQuality(); renderReview(); notice('현재 실행의 영역 검토 기록을 저장했습니다.');
    });
  };
  $('downloadReviews').onclick = () => {
    if (!doc?.latest_run || !reviewReady) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({document_id:doc.id, name:doc.name, run:reviewRun, reviews:reviewItems},null,2)],{type:'application/json'}));
    const link = document.createElement('a'); link.href=url; link.download=`reviews-${reviewRun.slice(0,8)}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  };

  for (const [target,source] of [['defaultSource','sourceLang'],['defaultTarget','targetLang']]) $(target).replaceChildren(...Array.from($(source).options, option => new Option(option.text,option.value)));
  function settingsForm() {
    $('defaultSource').value=preferences.source; $('defaultTarget').value=preferences.target; $('defaultMode').value=preferences.mode; $('defaultZoom').value=preferences.zoom; $('defaultRegions').checked=preferences.regions; $('defaultSync').checked=preferences.sync;
  }
  function applyViewPreferences() {
    $('showRegions').checked=preferences.regions; $('syncScroll').checked=preferences.sync; $('zoom').value=preferences.zoom;
    $('showRegions').dispatchEvent(new Event('change')); $('zoom').dispatchEvent(new Event('change'));
  }
  function applyUploadDefaults() { $('sourceLang').value=preferences.source; $('targetLang').value=preferences.target; setRunMode(preferences.mode); summary(); }
  $('saveSettings').onclick = () => {
    if ($('defaultSource').value === $('defaultTarget').value) { notice('원문 언어와 번역 언어를 다르게 선택하세요.',true); return; }
    preferences={source:$('defaultSource').value,target:$('defaultTarget').value,mode:$('defaultMode').value,zoom:$('defaultZoom').value,regions:$('defaultRegions').checked,sync:$('defaultSync').checked};
    try { localStorage.setItem(prefKey, JSON.stringify(preferences)); applyViewPreferences(); if (!doc && !busy) applyUploadDefaults(); notice('기본값을 저장했습니다. 다음 문서 업로드부터 적용합니다.'); }
    catch { notice('브라우저 저장소를 사용할 수 없어 기본값을 저장하지 못했습니다.',true); }
  };
  $('resetSettings').onclick = () => { preferences={...defaults}; settingsForm(); $('saveSettings').click(); };
  function jobStatus() {
    const active = displayedJob && ['running','queued'].includes(displayedJob.status);
    $('cancelJob').hidden = !active;
    $('cancelJob').disabled = !!displayedJob?.cancel_requested || displayedJob?.stage === 'finalize';
    $('cancelJob').textContent = displayedJob?.cancel_requested ? '중지 요청됨' : '작업 중지';
    $('workStatus').textContent = active ? displayedJob.cancel_requested ? '중지 대기' : '처리 중' : busy ? '처리 중' : displayedJob?.status === 'failed' ? '실패' : displayedJob?.status === 'cancelled' ? '중지됨' : doc?.report ? '결과 준비됨' : '준비';
  }
  $('cancelJob').onclick = async () => {
    if (!displayedJob) return;
    $('cancelJob').disabled=true;
    try { showJob(await api(`/api/jobs/${displayedJob.id}/cancel`,{method:'POST'})); notice('중지를 요청했습니다. 진행 중인 API 요청·처리 묶음이 끝나면 멈춥니다.'); }
    catch(error) { notice(error.message,true); jobStatus(); }
  };
  document.addEventListener('lab:document', event => { loadDocumentDetails(event.detail.fresh).catch(error => notice(error.message,true)); });
  document.addEventListener('lab:selection', renderReview);
  document.addEventListener('lab:job', () => { jobStatus(); summary(); renderReview(); });
  document.addEventListener('lab:history', loadRecent);
  document.addEventListener('lab:busy', () => { summary(); jobStatus(); renderReview(); });
  document.addEventListener('lab:idle', () => { summary(); jobStatus(); renderReview(); document.querySelectorAll('.file-link').forEach(button => button.disabled=false); if (view === 'history') loadHistory(); });
  new MutationObserver(() => summary()).observe($('apiStatus'), {childList:true,characterData:true,subtree:true});
  window.labDashboard = {showView, includeBaseline:() => runMode === 'compare', canSwitch, applyUploadDefaults};
  settingsForm(); applyUploadDefaults(); applyViewPreferences(); showView(location.hash.slice(1) || (new URLSearchParams(location.search).has('document') ? 'compare' : 'new'), false); loadRecent();
  if (doc) loadDocumentDetails(true);
})();
