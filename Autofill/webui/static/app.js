// app.js — front-end for the Autofill panel (LISA look).
// Calls the backend over HTTP (the Flask blueprint's /api/call/<method> bridge) so it works
// identically whether this runs as the standalone pywebview app OR embedded inside LISA's
// iframe — where window.pywebview is LISA's api, not the extractor's, so the old
// window.pywebview.api.* path had none of these methods and every button silently no-op'd.
// File dialogs run server-side on whatever pywebview window is hosting the process.
const API_BASE = (() => {
  let p = location.pathname;                    // "/autofill/" when merged, "/" standalone
  if (!/\/$/.test(p)) p = p.replace(/[^/]*$/, '');
  return p;
})();
async function _call(method, args) {
  const res = await fetch(API_BASE + 'api/call/' + method, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args: args || [] }),
  });
  if (!res.ok) throw new Error(method + ' HTTP ' + res.status);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || (method + ' failed'));
  return data.result;
}
// Proxy so every existing call site — `const a = api(); await a.some_method(x)` — works
// unchanged, and truthiness guards like `if (!a.pick_block_library)` stay satisfied.
const _API = new Proxy({}, { get: (_t, m) => (...args) => _call(String(m), args) });
const api = () => _API;
let FILES = [], TRAIN = [];

// ── in-page sub-navigation (pills under the single IDP Extraction tab) ──
document.querySelectorAll('.pill[data-page]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const page = btn.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    if (page === 'logic') logicReload();
    if (page === 'sources') srcRefresh();
    if (page === 'schedule') cimapRefresh();
  });
});

// ── files ──
function renderFiles() {
  const ul = document.getElementById('file_list');
  ul.innerHTML = '';
  FILES.forEach((f, i) => {
    const li = document.createElement('li');
    const base = f.split(/[\\/]/).pop();
    li.innerHTML = `<span class="fpath" title="${f}">${base}</span><span class="fx" data-i="${i}" title="Remove this file from the list">✕</span>`;
    li.querySelector('.fx').onclick = () => { FILES.splice(i, 1); renderFiles(); };
    ul.appendChild(li);
  });
}
// auto-name the output "<Site>_FILLED.xlsm" from the project folder, unless the user typed one
async function suggestOutput() {
  const a = api(); if (!a || !FILES.length) return;
  const cur = document.getElementById('output').value.trim();
  const looksAuto = !cur || /_FILLED\.xlsm$/i.test(cur);
  if (!looksAuto) return;
  const p = await a.suggest_output(FILES);
  if (p) document.getElementById('output').value = p;
}
async function pickFiles() {
  const a = api(); if (!a) return;
  const paths = await a.pick_files();
  if (paths) { paths.forEach(p => { if (!FILES.includes(p)) FILES.push(p); }); renderFiles(); suggestOutput(); }
}
async function pickFolder() {
  const a = api(); if (!a) return;
  const r = await a.pick_folder();
  const paths = Array.isArray(r) ? r : (r && r.files) || [];   // {folder, files} or legacy list
  if (paths.length) { paths.forEach(p => { if (!FILES.includes(p)) FILES.push(p); }); renderFiles(); suggestOutput(); }
}
function clearFiles() { FILES = []; renderFiles(); }
async function pickTemplate() { const a = api(); if (!a) return; const p = await a.pick_template(); if (p) document.getElementById('template').value = p; }
async function pickBlockLibrary() {
  const a = api(); if (!a || !a.pick_block_library) return;
  const r = await a.pick_block_library();
  if (r) renderBlockLibrary(r);
}
// ── small DOM helpers ──
const val = id => (document.getElementById(id) || {}).value;
const setVal = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };

// ── version-sync status (hero bar) — plain TEXT: green "Up to date", red "Out of date" ──
function renderUpdate(r) {
  const btn = document.getElementById('btn_update');
  const st = document.getElementById('update_status');
  if (!r) return;
  const outOfDate = !!r.out_of_date;
  if (btn) btn.disabled = !outOfDate;        // greyed unless out of date
  if (!st) return;
  if (!r.reachable) {
    st.textContent = 'Update folder not found';
    st.className = 'update-txt neutral';
  } else if (outOfDate) {
    st.textContent = `Out of date (v${r.current} → v${r.latest})`;
    st.className = 'update-txt bad';
  } else {
    st.textContent = `Up to date (v${r.current})`;
    st.className = 'update-txt good';
  }
}
async function checkUpdate() {
  const a = api(); if (!a || !a.check_update) return;
  try { renderUpdate(await a.check_update((val('update_path') || '').trim())); } catch (e) {}
}
let updTimer = null;
async function runUpdate() {
  const a = api(); if (!a || !a.run_update) return;
  const p = (val('update_path') || '').trim();
  const btn = document.getElementById('btn_update'); const st = document.getElementById('update_status');
  if (btn) btn.disabled = true;
  if (st) { st.textContent = 'Updating…'; st.className = 'update-txt busy'; }
  await a.run_update(p);
  if (updTimer) return;
  updTimer = setInterval(async () => {
    const s = await a.poll_update();
    if (!s) return;
    const logEl = document.getElementById('log');
    if (logEl) { logEl.textContent = s.log; logEl.scrollTop = 1e9; }
    if (!s.running) { clearInterval(updTimer); updTimer = null; checkUpdate(); }
  }, 700);
}
// version-control folder pickers + persistence (hero path + training path)
async function pickVersionDir(which) {
  const a = api(); if (!a || !a.pick_version_dir) return;
  const p = await a.pick_version_dir();
  if (!p) return;
  if (which === 'train') setVal('train_version_path', p); else setVal('update_path', p);
  await saveVersionPaths();
  checkUpdate();
}
async function saveVersionPaths() {
  const a = api(); if (!a || !a.set_version_paths) return;
  try { await a.set_version_paths((val('update_path') || '').trim(), (val('train_version_path') || '').trim()); } catch (e) {}
}
function renderBlockLibrary(r) {
  const inp = document.getElementById('block_lib');
  const st = document.getElementById('block_lib_status');
  if (inp) inp.value = (r && r.dir) || '';
  if (!st) return;
  if (r && r.dir) st.textContent = `${r.blocks || 0} symbols loaded — inferred from here, no AutoCAD scan.`;
  else st.textContent = 'Block library folder not found — set it here so symbols can be inferred.';
  st.classList.toggle('key-ok', !!(r && r.dir && r.blocks));
}
async function pickOutput() { const a = api(); if (!a) return; const p = await a.pick_output(); if (p) document.getElementById('output').value = p; }
// source field defaults to FOLDER selection (a whole project), per Master Cole's spec
document.getElementById('dropzone').addEventListener('click', pickFolder);

// ── run scan ──
let logTimer = null;
async function runScan() {
  const a = api(); if (!a) return;
  const opts = {
    files: FILES,
    template: document.getElementById('template').value,
    output: document.getElementById('output').value,
    mode: document.querySelector('input[name=mode]:checked').value,
    infer: document.getElementById('infer').checked,
    flags: document.getElementById('flags').checked,
    clear_rows: document.getElementById('clearrows').checked,
    nogrey: document.getElementById('nogrey').checked,
    learn: document.getElementById('learn').checked,
    unknown: document.getElementById('unknown').value,
    hi_ocr: document.getElementById('opt_hi_ocr').checked,
    clear_dev: document.getElementById('opt_clear_dev').checked,
    vision_assist: document.getElementById('opt_vision').checked,
    specialized: document.getElementById('opt_specialized').checked,
  };
  document.getElementById('run_btn').disabled = true;
  document.getElementById('run_btn').style.display = 'none';
  const cb = document.getElementById('cancel_btn');
  cb.style.display = 'inline-flex'; cb.disabled = false; cb.textContent = '■  Cancel';
  document.getElementById('spin').style.display = 'flex';
  document.getElementById('log').textContent = '';
  await a.run_scan(opts);
  if (!logTimer) logTimer = setInterval(pollLog, 500);
}
async function cancelScan() {
  const a = api(); if (!a) return;
  const cb = document.getElementById('cancel_btn');
  cb.disabled = true; cb.textContent = '⛔  Cancelling…';
  await a.cancel_scan();
}
async function pollLog() {
  const a = api(); if (!a) return;
  const st = await a.poll();          // {log, running}
  if (st) {
    document.getElementById('log').textContent = st.log;
    document.getElementById('log').scrollTop = 1e9;
    if (!st.running) {
      clearInterval(logTimer); logTimer = null;
      document.getElementById('run_btn').disabled = false;
      document.getElementById('run_btn').style.display = 'inline-flex';
      document.getElementById('cancel_btn').style.display = 'none';
      document.getElementById('spin').style.display = 'none';
    }
  }
}

// ── small helpers ──
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function dl(filename, text, mime){   // trigger an in-browser file download (no native dialog)
  const blob = new Blob([text == null ? '' : text], {type: mime || 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}
// key order MUST match backend logic_store._rkey: (type, match, context, result)
function rkey(r){ return [r.type||'', r.match||'', r.context||'', r.result||'']; }

// ── Remembered Logic ──
let LOGIC_RULES = [];
// Plain-English explanation of what a rule actually DOES, shown on hover.
function ruleTip(r){
  const m = (r.match||'(blank)'), res = (r.result||'(blank)'), ctx = (r.context||'').trim();
  const where = ctx ? `  (only when context = "${ctx}")` : '';
  switch (r.type) {
    case 'symbol_keyword':
      return `Device→symbol: when a source/destination name contains "${m}", the exe assigns the IDP block symbol "${res}".${where}`;
    case 'text_fix':
      return `OCR/text fix: wherever the misread "${m}" appears in the read text, the exe replaces it with "${res}" (whole word, any source) — so the wrong text never reaches the workbook.`;
    case 'value_rule':
      return `Value fix: wherever the value "${m}" appears, the exe writes "${res}" instead (normalizes a spelling/format).${where}`;
    case 'header_alias':
      return `Header alias: a schedule column titled "${m}" is read as the "${res}" column, so a differently-labelled schedule still maps.`;
    case 'note':
      return `Reference note (no automatic action): ${r.note || m}`;
    default:
      return `${r.type}: "${m}" → "${res}"${where}`;
  }
}
async function logicReload() {
  const a = api(); if (!a) return;
  LOGIC_RULES = (await a.logic_rules()) || [];
  const tb = document.querySelector('#logic_tbl tbody'); tb.innerHTML = '';
  LOGIC_RULES.forEach((r, i) => {
    const manual = (r.source === 'manual');
    const tr = document.createElement('tr');
    if (manual) tr.className = 'row-manual';
    const tip = esc(ruleTip(r));
    tr.innerHTML =
      `<td><input type="checkbox" class="logic-chk" data-i="${i}" title="Select this rule for multi-delete"></td>` +
      `<td title="${tip}">${esc(r.type)}</td>` +
      `<td title="${tip}">${esc(r.match)}</td>` +
      `<td class="trash-cell"><span class="trash" title="Delete this rule">🗑</span></td>` +
      `<td title="${tip}">${esc(r.result)}</td>` +
      `<td title="Context — when this rule applies (e.g. conduit_type, or a S/D side). Blank = always.">${esc(r.context)}</td>` +
      `<td title="${esc(r.note||'')}">${esc(r.note)}</td>` +
      `<td><span class="src-badge ${manual?'manual':'gen'}" title="${manual?'You added this rule by hand.':'Learned automatically from training on finished IDPs.'}">${manual?'manual':'generated'}</span></td>`;
    tr.querySelector('.trash').onclick = () => logicDeleteOne(i);
    tb.appendChild(tr);
  });
  const st = document.getElementById('logic_status');
  if (st) st.textContent = `${LOGIC_RULES.length} rule(s), ${LOGIC_RULES.filter(r=>r.source==='manual').length} manual`;
  const all = document.getElementById('logic_all'); if (all) all.checked = false;
}
function logicToggleAll(cb){ document.querySelectorAll('.logic-chk').forEach(c => c.checked = cb.checked); }
function logicAddForm(){ const f=document.getElementById('logic_form'); if(f) f.style.display = (f.style.display==='none' ? 'flex' : 'none'); }
function logicAddCancel(){ const f=document.getElementById('logic_form'); if(f) f.style.display='none'; ['lf_match','lf_result','lf_context','lf_note'].forEach(id=>setVal(id,'')); }
async function logicAddSave(){
  const rule = { type: val('lf_type'), match: (val('lf_match')||'').trim(), result: (val('lf_result')||'').trim(),
                 context: (val('lf_context')||'').trim(), note: (val('lf_note')||'').trim() };
  if (!rule.match) { alert('Enter a "match" value for the rule.'); return; }
  const a = api(); const res = await a.logic_add_rule(rule);
  if (res && res.ok) { logicAddCancel(); logicReload(); }
  else alert('Could not add the rule' + (res && res.error ? ': '+res.error : '.'));
}
async function logicDeleteOne(i){
  const r = LOGIC_RULES[i]; if (!r) return;
  if (!confirm(`Delete this rule?\n\n${r.type}:  ${r.match}  →  ${r.result||'(blank)'}\n\nYou can restore it with "Undo delete".`)) return;
  const a = api(); const res = await a.logic_delete([rkey(r)]);
  if (res && res.ok) logicReload(); else alert('Delete failed' + (res && res.error ? ': '+res.error : '.'));
}
async function logicMultiDelete(){
  const idx = [...document.querySelectorAll('.logic-chk')].filter(c=>c.checked).map(c=>+c.dataset.i);
  if (!idx.length) { alert('Check the rules you want to delete first (the boxes on the left).'); return; }
  if (!confirm(`Delete ${idx.length} selected rule(s)?\n\nYou can restore them with "Undo delete".`)) return;
  const keys = idx.map(i => rkey(LOGIC_RULES[i]));
  const a = api(); const res = await a.logic_delete(keys);
  if (res && res.ok) logicReload(); else alert('Delete failed' + (res && res.error ? ': '+res.error : '.'));
}
async function logicUndo(){
  const a = api(); const res = await a.logic_undo_delete();
  if (res && res.restored) logicReload();
  else alert(res && res.note ? res.note : 'Nothing to undo.');
}
async function logicExport(){
  const a = api(); if (!a || !a.logic_ruleset_csv) return;
  const r = await a.logic_ruleset_csv();          // read LIVE from disk — never stale
  if (r && r.ok) dl('remembered_logic_rules.csv', r.text, 'text/csv;charset=utf-8');
  else alert('Could not export the rule set' + (r && r.error ? ': '+r.error : '.'));
}
async function logicAdd(){ logicAddForm(); }   // legacy alias

// ── Conduit Index Mapping ──
async function cimapRefresh(){
  const a = api(); if (!a || !a.conduit_index) return;
  const d = await a.conduit_index();
  const tb = document.querySelector('#cimap_tbl tbody'); tb.innerHTML = '';
  (d.rows||[]).forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${esc(r.name)}</td><td>${esc(r.source)}</td><td>${esc(r.dest)}</td><td>${esc(r.size)}</td><td>${esc(r.type)}</td><td>${esc(r.fills)}</td>`;
    tb.appendChild(tr);
  });
  const st = document.getElementById('cimap_status');
  if (st) st.textContent = d.count ? `${d.count} conduit(s) from the last scan.` : 'No conduits yet — run a scan, then Refresh.';
}
async function cimapExport(){
  const a = api(); const r = await a.conduit_index_csv();
  if (r && r.ok && r.rows) dl('conduit_index.csv', r.text, 'text/csv;charset=utf-8');
  else alert('Nothing to export — run a scan first.');
}

// ── sources ──
async function srcRefresh() {
  const a = api(); if (!a) return;
  const rows = await a.provenance();
  const tb = document.querySelector('#src_tbl tbody'); tb.innerHTML = '';
  (rows || []).forEach(r => { const tr = document.createElement('tr'); tr.innerHTML = `<td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td>`; tb.appendChild(tr); });
  const st = document.getElementById('src_status'); if (st) st.textContent = (rows||[]).length ? `${rows.length} provenance row(s).` : 'No provenance yet — run a scan.';
}
async function srcExport() {
  const a = api(); const r = await a.provenance_csv();
  if (r && r.ok && r.rows) dl('provenance.csv', r.text, 'text/csv;charset=utf-8');
  else alert('Nothing to export — run a scan first.');
}
// ── training: download the uncertainty packet as a .txt (and still write the packet) ──
async function downloadUncertainties(){
  const a = api(); if (!a || !a.download_uncertainties) return;
  // Prefer a native "choose where to save" (Save-As) dialog so the user picks the location.
  try {
    const s = await a.save_uncertainties();
    if (s && s.ok) {
      if (s.cancelled) return;                       // user closed the dialog — do nothing
      if (s.path) { alert('Saved uncertainties to:\n' + s.path); pollTrain(); return; }
      if (s.note) { alert(s.note); return; }         // nothing open to save
    }
    // any other outcome falls through to the in-browser download below
  } catch (e) { /* native dialog unavailable — fall back */ }
  const r = await a.download_uncertainties();
  if (r && r.ok) {
    if (r.text) dl('uncertainties_for_claude.txt', r.text, 'text/plain;charset=utf-8');
    else alert(r.note || 'No open uncertainties — run a scan or Compare & Learn first.');
    pollTrain();
  } else alert('Could not build the uncertainties packet' + (r && r.error ? ': '+r.error : '.'));
}

// ── training ── three input groups: plans / finished (ground truth) / generated
const TRAIN_STORES = { plans: [], finished: [], generated: [] };
function renderTrain(kind) {
  const ul = document.getElementById('train_' + kind + '_list');
  if (!ul) return;
  ul.innerHTML = '';
  TRAIN_STORES[kind].forEach((f, i) => {
    const li = document.createElement('li');
    const base = f.split(/[\\/]/).filter(Boolean).pop() || f;
    li.innerHTML = `<span class="fpath" title="${f}">${base}</span><span class="fx" data-i="${i}" title="Remove this file from the list">✕</span>`;
    li.querySelector('.fx').onclick = () => { TRAIN_STORES[kind].splice(i, 1); renderTrain(kind); };
    ul.appendChild(li);
  });
}
async function trainAddFiles(kind) {
  const a = api(); if (!a) return;
  const paths = await a.pick_files();
  (paths || []).forEach(p => { if (!TRAIN_STORES[kind].includes(p)) TRAIN_STORES[kind].push(p); });
  renderTrain(kind);
}
async function trainAddFolder(kind) {
  const a = api(); if (!a) return;
  const d = a.pick_dir ? await a.pick_dir() : '';   // folder path, passed to the comparator as-is
  if (d && !TRAIN_STORES[kind].includes(d)) { TRAIN_STORES[kind].push(d); renderTrain(kind); }
}
function trainClear(kind) { TRAIN_STORES[kind] = []; renderTrain(kind); }
async function runTraining() {
  const a = api(); if (!a) return;
  document.getElementById('train_btn').disabled = true;
  await a.run_training(TRAIN_STORES.plans, TRAIN_STORES.finished, TRAIN_STORES.generated);
  pollTrain();
}
async function askClaude() {
  const a = api(); if (!a || !a.ask_claude) return;
  await a.ask_claude();
  pollTrain();
}
let trainTimer = null;
function pollTrain() {
  const a = api(); if (!a) return;
  if (trainTimer) return;
  trainTimer = setInterval(async () => {
    const s = await a.poll_training();
    if (s) {
      document.getElementById('train_log').textContent = s.log;
      document.getElementById('train_log').scrollTop = 1e9;
      if (!s.running) { clearInterval(trainTimer); trainTimer = null; document.getElementById('train_btn').disabled = false; }
    }
  }, 500);
}
async function saveApiKey() {
  const a = api(); if (!a || !a.set_api_key) return;
  const el = document.getElementById('api_key');
  const r = await a.set_api_key(el.value.trim());
  el.value = '';
  updateKeyStatus(r && r.has_key);
}
function updateKeyStatus(hasKey) {
  const el = document.getElementById('key_status');
  if (!el) return;
  el.textContent = hasKey
    ? '✔ API key set — “Ask Claude” asks Claude directly and applies the rules automatically.'
    : 'No API key set — “Ask Claude” will write a packet your attached Claude Code chat can resolve.';
  el.classList.toggle('key-ok', !!hasKey);
}

// ── startup: pull remembered settings (designated template + recent projects) ──
let RECENT_PROJECTS = [];
async function initSettings() {
  const a = api(); if (!a || !a.get_settings) return;
  try {
    const s = await a.get_settings();
    if (s) {
      const tpl = document.getElementById('template');
      if (s.template && !tpl.value) tpl.value = s.template;   // designated template default
      RECENT_PROJECTS = s.recent_projects || [];
      renderRecents();
      if (s.block_library) renderBlockLibrary(s.block_library);
      if (s.version) {
        if (s.version.pull && !val('update_path')) setVal('update_path', s.version.pull);
        if (s.version.push && !val('train_version_path')) setVal('train_version_path', s.version.push);
      }
    }
    // persist + re-check when either version-folder field is edited
    const up = document.getElementById('update_path');
    if (up) up.addEventListener('change', async () => { await saveVersionPaths(); checkUpdate(); });
    const tvp = document.getElementById('train_version_path');
    if (tvp) tvp.addEventListener('change', saveVersionPaths);
    if (a.get_api_key_status) { const k = await a.get_api_key_status(); updateKeyStatus(k && k.has_key); }
    checkUpdate();   // async, non-blocking — paints the green/red status after first render
  } catch (e) { /* plain browser / no api */ }
}
// ── training password gate ──
let TRAIN_UNLOCKED = false;
async function unlockTraining() {
  const a = api(); if (!a || !a.check_training_password) return;
  const status = document.getElementById('train_pw_status');
  let ok = false;
  try { const r = await a.check_training_password(val('train_pw')); ok = !!(r && r.ok); } catch (e) {}
  if (ok) {
    TRAIN_UNLOCKED = true;
    const gate = document.getElementById('train_gate'); if (gate) gate.style.display = 'none';
    const body = document.getElementById('train_body'); if (body) body.style.display = '';
    setVal('train_pw', '');
    if (status) { status.textContent = 'Unlocked.'; status.style.color = ''; }
  } else if (status) {
    status.textContent = 'Incorrect password.'; status.style.color = '#ff6b6b';
  }
}
function renderRecents() {
  const host = document.getElementById('recent_projects');
  if (!host) return;
  host.innerHTML = '';
  if (!RECENT_PROJECTS.length) { host.style.display = 'none'; return; }
  host.style.display = 'flex';
  const lbl = document.createElement('span'); lbl.className = 'recent-lbl'; lbl.textContent = 'Recent:';
  host.appendChild(lbl);
  RECENT_PROJECTS.slice(0, 6).forEach(p => {
    const base = p.split(/[\\/]/).filter(Boolean).pop() || p;
    const chip = document.createElement('button');
    chip.className = 'recent-chip'; chip.title = 'Reload this project — ' + p; chip.textContent = base;
    chip.onclick = () => loadRecentProject(p);
    host.appendChild(chip);
  });
}
async function loadRecentProject(folder) {
  const a = api(); if (!a || !a.scan_folder) return;
  const r = await a.scan_folder(folder);
  const paths = (r && r.files) || [];
  if (paths.length) { paths.forEach(p => { if (!FILES.includes(p)) FILES.push(p); }); renderFiles(); suggestOutput(); }
}
// pywebview injects its api asynchronously; hook both the ready event and DOMContentLoaded
window.addEventListener('pywebviewready', initSettings);
if (document.readyState !== 'loading') initSettings();
else document.addEventListener('DOMContentLoaded', initSettings);
