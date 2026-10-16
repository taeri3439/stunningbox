const $ = id => document.getElementById(id);
const id = new URLSearchParams(location.search).get('document');
let doc, busy = false;
const base = `/api/native/${encodeURIComponent(id || '')}`;
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '요청에 실패했습니다.');
  return data;
}
function message(text) { $('notice').textContent = text; }
function selection() {
  const option = doc?.result?.exports.find(x => x.format === $('format').value);
  $('exportHelp').textContent = option?.description || '';
  $('download').disabled = busy || !option;
}
function show(value) {
  if (!doc && value.result?.source_lang) {
    $('sourceLang').value = value.result.source_lang;
    $('targetLang').value = value.result.target_lang;
  }
  doc = value;
  $('title').textContent = doc.name;
  $('summary').textContent = `${doc.source_format.toUpperCase()} 입력 → 기본 출력도 ${doc.source_format.toUpperCase()}`;
  $('preserve').disabled = busy || doc.busy;
  $('translate').disabled = busy || doc.busy || !doc.can_translate;
  $('translationSupport').textContent = doc.can_translate ? '이미지 속 글자와 삽입 개체는 번역 범위에서 제외됩니다.' : '현재 설치된 엔진은 이 형식의 번역 저장을 지원하지 않습니다. 원본 보존 또는 호환 형식 변환을 이용하세요.';
  $('format').replaceChildren(...(doc.result?.exports || []).map(x => new Option(x.label, x.format)));
  $('format').value = doc.result?.default_format || '';
  $('format').disabled = busy || !doc.result;
  $('resultStatus').textContent = !doc.result ? '결과를 먼저 생성하세요.' : doc.result.mode === 'translated' ? '번역 결과 · 레이아웃 검토 필요' : '원본 보존 결과 · 번역 미적용';
  $('warnings').replaceChildren(...[...(doc.warnings || []), ...(doc.result?.warnings || [])].map(text => {const li=document.createElement('li');li.textContent=text;return li;}));
  for (const which of ['source','output']) {
    const available = which === 'source' ? doc.source_preview : doc.result?.preview;
    $(`${which}Preview`).hidden = !available;
    $(`${which}Missing`).hidden = !!available;
    if (available) {
      const url = `${base}/preview/${which}?run=${doc.latest_run || ''}`;
      if ($(`${which}Preview`).getAttribute('src') !== url) $(`${which}Preview`).src = url;
    }
  }
  selection();
}
async function process(translate) {
  if (busy) return;
  busy = true; show(doc); message(translate ? '원본 구조를 유지하며 번역·변환 결과를 검증하고 있습니다…' : '원본을 보존하고 호환 Export를 검증하고 있습니다…');
  try {
    show(await api(base + '/process', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({translate, source_lang:$('sourceLang').value, target_lang:$('targetLang').value})}));
    message('완료했습니다. 결과 미리보기와 검토 사항을 확인하세요.');
  } catch(error) { message(error.message); }
  finally { busy = false; show(doc); }
}
$('preserve').onclick = () => process(false);
$('translate').onclick = () => process(true);
$('format').onchange = selection;
$('download').onclick = () => {
  if (busy || !doc?.result) return;
  const link = document.createElement('a');
  link.href = `${base}/export/${$('format').value}?run=${doc.latest_run}`;
  link.download = ''; link.click();
};
api(base).then(show).catch(error => message(error.message));
