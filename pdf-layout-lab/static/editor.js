const params = new URLSearchParams(location.search);
const documentId = params.get('document'), run = params.get('run');
const status = document.getElementById('status'), save = document.getElementById('save');
let session, dirty = false, lastSavedRevision = 0, editGeneration = 0;
async function request(url, method = 'GET') {
  const response = await fetch(url, {method});
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || '요청 실패');
  return body;
}
const base = `/api/documents/${encodeURIComponent(documentId)}`;
document.getElementById('download').href = `${base}/download/new-docx?run=${encodeURIComponent(run)}`;
(async () => {
  try {
    const data = await request(`${base}/editor-config?run=${encodeURIComponent(run)}`);
    session = data.session;
    const script = document.createElement('script');
    script.src = `${data.document_server_url.replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`;
    await new Promise((resolve, reject) => {script.onload = resolve; script.onerror = () => reject(new Error('OnlyOffice에 연결하지 못했습니다.')); document.head.append(script);});
    new DocsAPI.DocEditor('editor', {...data.config, width:'100%', height:'100%', events:{
      onDocumentReady: () => {save.disabled = false; status.textContent = '텍스트 상자를 선택해 편집하세요. 표 선·그림은 배경으로 보존됩니다. 다운로드 전에 저장 버튼을 눌러주세요.';},
      onDocumentStateChange: event => {if (event.data) {dirty = true; editGeneration++;}},
      onError: event => {status.textContent = (event.data?.errorDescription || '편집기 오류').replace(/<br\s*\/?>/gi, ' ');}
    }});
  } catch (error) {status.textContent = error.message;}
})();
save.onclick = async () => {
  save.disabled = true;
  const savingGeneration = editGeneration;
  try {
    status.textContent = '문서 서버의 저장 완료를 기다립니다…';
    const initial = await request(`/api/editor-sessions/${session}`);
    let result = await request(`/api/editor-sessions/${session}/save`, 'POST');
    // An immediate click can reach CommandService before the browser's changes.
    // Error 4 alone must not claim that unsynchronized edits have been saved.
    for (let retry = 0; result.error === 4 && dirty && initial.revision <= lastSavedRevision && retry < 15; retry++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await request(`/api/editor-sessions/${session}/save`, 'POST');
    }
    if (result.error === 4 && dirty && initial.revision <= lastSavedRevision) throw new Error('변경 내용이 문서 서버에 아직 도착하지 않았습니다. 잠시 후 다시 저장하세요.');
    if (result.error !== 0 && result.error !== 4) throw new Error(`저장 요청 실패 (${result.error}). 잠시 후 다시 시도하세요.`);
    if (result.error === 0) {
      let saved = false;
      for (let attempt = 0; attempt < 60; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const state = await request(`/api/editor-sessions/${session}`);
        if (state.revision > initial.revision) {lastSavedRevision = state.revision; saved = true; break;}
      }
      if (!saved) throw new Error('저장 완료 확인 시간이 초과되었습니다. 미리보기 갱신 전에 다시 시도하세요.');
    }
    status.textContent = '저장된 DOCX로 미리보기를 만드는 중…';
    await request(`${base}/preview?run=${encodeURIComponent(run)}`, 'POST');
    lastSavedRevision = (await request(`/api/editor-sessions/${session}`)).revision;
    dirty = editGeneration !== savingGeneration;
    status.textContent = dirty ? '이전 수정본은 저장되었습니다. 저장 중 추가 변경이 있어 다시 저장하세요.' : '저장과 미리보기 갱신이 완료되었습니다. 비교 화면을 새로고침하거나 DOCX를 다운로드하세요.';
  } catch (error) {status.textContent = error.message;}
  finally {save.disabled = false;}
};
