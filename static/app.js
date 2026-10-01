const $ = (selector) => document.querySelector(selector);
const docsEl = $('#documents'), statusEl = $('#upload-status'), answerEl = $('#answer'), sourcesEl = $('#sources');

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function refreshDocuments() {
  const documents = await request('/api/documents');
  $('#doc-count').textContent = documents.length;
  if (!documents.length) { docsEl.innerHTML = '<div class="empty-library">Your sources will appear here.</div>'; return; }
  docsEl.innerHTML = documents.map(doc => `<div class="doc-item"><span class="doc-glyph">▤</span><div class="doc-info"><strong title="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</strong><span>${doc.chunks} CHUNKS · ${doc.page_count ? `${doc.page_count} PAGES` : 'TEXT FILE'}</span></div><button class="doc-delete" title="Remove source" data-delete="${doc.id}">×</button></div>`).join('');
}

function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function safeMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>').replace(/\n/g, '<br>');
}

$('#documents').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-delete]'); if (!button) return;
  button.disabled = true;
  try { await request(`/api/documents/${button.dataset.delete}`, {method:'DELETE'}); await refreshDocuments(); }
  catch (error) { alert(error.message); button.disabled = false; }
});

async function addFile(file) {
  if (!file) return;
  statusEl.textContent = `Reading ${file.name}…`;
  const form = new FormData(); form.append('file', file);
  try {
    const result = await request('/api/documents', {method:'POST', body:form});
    statusEl.textContent = `Added ${result.name}: ${result.chunks} evidence passages indexed.`;
    await refreshDocuments();
  } catch (error) { statusEl.textContent = error.message; }
  $('#file-input').value = '';
}

$('#file-input').addEventListener('change', event => addFile(event.target.files[0]));
const dropzone = $('#dropzone');
for (const type of ['dragenter','dragover']) dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add('drag'); });
for (const type of ['dragleave','drop']) dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove('drag'); });
dropzone.addEventListener('drop', event => addFile(event.dataTransfer.files[0]));

$('#ask-button').addEventListener('click', async () => {
  const question = $('#question').value.trim();
  if (question.length < 4) { $('#question').focus(); return; }
  const button = $('#ask-button'); button.disabled = true; button.textContent = 'Searching…';
  answerEl.classList.remove('hidden'); answerEl.textContent = 'Retrieving evidence and checking source references…';
  try {
    const result = await request('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question})});
    answerEl.innerHTML = safeMarkdown(result.answer);
    $('#mode-label').textContent = result.mode === 'grounded_generation' ? 'Grounded AI enabled' : 'Evidence-only mode';
    $('#source-count').textContent = `${result.sources.length} PASSAGES RETRIEVED`;
    sourcesEl.innerHTML = result.sources.length ? result.sources.map(source => `<article class="source-card"><div class="source-head"><span class="source-name">[${escapeHtml(source.source_id)}] ${escapeHtml(source.name)}${source.page ? ` · PAGE ${source.page}` : ''}</span><span class="source-tag">${Math.round(source.term_coverage*100)}% TERM MATCH</span></div><p>${escapeHtml(source.text)}</p><div class="source-meta"><span>RETRIEVAL ${source.retrieval_score.toFixed(3)}</span><span>CHUNK ${source.chunk_id}</span></div></article>`).join('') : '<div class="source-placeholder"><span>↳</span><div><strong>No matching sources yet.</strong><p>Add relevant documents and try again.</p></div></div>';
  } catch (error) { answerEl.textContent = error.message; }
  finally { button.disabled = false; button.innerHTML = 'Find evidence <span>↗</span>'; }
});

$('#question').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') $('#ask-button').click(); });
request('/api/health').then(result => { $('#mode-label').textContent = result.answer_mode === 'grounded_generation' ? 'Grounded AI enabled' : 'Evidence-only mode'; }).catch(() => { $('#mode-label').textContent = 'Start the local server'; });
refreshDocuments().catch(() => { docsEl.innerHTML = '<div class="empty-library">Start the local server to connect.</div>'; });
