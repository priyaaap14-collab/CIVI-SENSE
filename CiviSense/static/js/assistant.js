/* AI assistant UI. Talks to /api/assistant/* (NDJSON stream). Shows a tool-progress line ONLY when the server reports that
 * the tool is really running, and an explicit Confirm / Cancel card for anything that changes data. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var root = $('chat'), log = $('chatLog'), txt = $('chatText'), sendBtn = $('sendBtn'), status = $('voiceStatus');
  var csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || getCookie('csrftoken');
  var busy = false, voiceUsed = false, liveTool = null;
  function getCookie(n) { var m = document.cookie.match('(?:^|; )' + n + '=([^;]*)'); return m ? decodeURIComponent(m[1]) : ''; }
  function lang() { return document.querySelector('input[name=clang]:checked').value; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function scroll() { log.scrollTop = log.scrollHeight; }
  function say(m, c) { status.textContent = m; status.className = 'voice-status flex-grow-1' + (c ? ' ' + c : ''); }

  var EXAMPLES = {
    CITIZEN: ['I want to report a pothole near my house', 'Check the status of my complaint CS-2026-000001', 'ನನ್ನ ಮನೆ ಹತ್ತಿರ ಕಸ ರಾಶಿ ಬಿದ್ದಿದೆ', 'Are there garbage complaints near 12.9716, 77.5946?'],
    OFFICER: ['Show my active tasks', 'What are the complaint hotspots for my department?', 'Priority breakdown of CS-2026-000001'],
    ADMIN: ['Which wards have the biggest backlog? Start with Koramangala', 'Show unassigned live complaints', 'Hotspots for Garbage & Sanitation']
  };
  (EXAMPLES[root.dataset.role] || EXAMPLES.CITIZEN).forEach(function (t) {
    var c = el('span', 'chip', t); c.onclick = function () { txt.value = t; txt.focus(); }; $('suggestions').appendChild(c);
  });
  if (root.dataset.configured !== '1') { sendBtn.disabled = true; txt.disabled = true; }

  function bubble(kind, text, via) {
    var b = el('div', 'msg ' + kind, text);
    if (/[ಀ-೿]/.test(text)) b.setAttribute('lang', 'kn');
    if (via) b.appendChild(el('span', 'via', via));
    log.appendChild(b); scroll(); return b;
  }
  function toolLine(label) { var l = el('div', 'toolline'); l.appendChild(el('span', 'spinner-border spinner-border-sm')); l.appendChild(el('span', null, label)); log.appendChild(l); scroll(); return l; }
  function finishTool(l, ok, note) {
    if (!l) return; l.textContent = ''; l.className = 'toolline ' + (ok ? 'done' : 'fail');
    l.appendChild(el('i', 'bi ' + (ok ? 'bi-check-circle' : 'bi-x-circle'))); l.appendChild(el('span', null, note));
  }

  function kv(dl, k, v) { if (v == null || v === '' || (Array.isArray(v) && !v.length)) return; dl.appendChild(el('dt', null, k)); var d = el('dd', null, typeof v === 'object' ? JSON.stringify(v) : String(v)); if (typeof v === 'string' && /[ಀ-೿]/.test(v)) d.setAttribute('lang', 'kn'); dl.appendChild(d); }
  function confirmCard(ev) {
    var p = ev.preview || {}, card = el('div', 'confirm-card'), dl = el('dl');
    var head = ev.tool === 'submit_complaint' ? 'Please confirm: submit this complaint' : ev.tool === 'update_complaint_status' ? 'Please confirm: change complaint status' : 'Please confirm this action';
    card.appendChild(el('div', 'fw-semibold', head));
    if (ev.tool === 'submit_complaint') {
      var s = p.will_store_exactly || {}; kv(dl, 'Title', s.title); kv(dl, 'Description (saved exactly as shown)', s.description); kv(dl, 'Address', s.address); kv(dl, 'Ward', p.ward);
      kv(dl, 'Category', p.category && (p.category.name + (p.category.confidence ? ' (' + Math.round(p.category.confidence * 100) + '%)' : '') + ' - ' + p.category.source));
      kv(dl, 'Priority (estimate)', p.recommended_priority && (p.recommended_priority.level + ' ' + p.recommended_priority.score + '/100'));
      kv(dl, 'Severity / impact', p.severity + ' / ' + p.public_impact); kv(dl, 'Location', p.coordinates); kv(dl, 'Input', p.input_method); kv(dl, 'Language', s.language === 'kn' ? 'Kannada' : 'English');
      (p.similar_existing_reports || []).forEach(function (x, i) { kv(dl, i ? '' : 'Similar reports', (x.ref ? x.ref + ' ' : '') + (x.title || '') + ' [' + x.status + ', ' + x.similarity + ']'); });
      (p.warnings || []).forEach(function (w) { kv(dl, 'Warning', w); });
    } else {
      Object.keys(p).forEach(function (k) { kv(dl, k.replace(/_/g, ' '), p[k]); });
    }
    card.appendChild(dl);
    var row = el('div', 'd-flex gap-2');
    var ok = el('button', 'btn btn-primary btn-sm', 'Confirm'), no = el('button', 'btn btn-outline-secondary btn-sm', 'Cancel');
    ok.type = no.type = 'button';
    function decide(a) { ok.disabled = no.disabled = true; card.classList.add('opacity-75'); stream($('chat').dataset.decideUrl, { pending_id: ev.pending_id, approve: a }); }
    ok.onclick = function () { decide(true); }; no.onclick = function () { decide(false); };
    row.appendChild(ok); row.appendChild(no); card.appendChild(row); log.appendChild(card); scroll();
  }

  function handle(ev) {
    switch (ev.type) {
      case 'text': bubble('bot', ev.text); break;
      case 'tool_start': liveTool = toolLine(ev.label); break;
      case 'tool_done':
        finishTool(liveTool, ev.ok, ev.note || (ev.ok ? 'Done' : 'Could not complete'));
        if (ev.ref) bubble('bot', 'Your complaint reference number is ' + ev.ref + '.');
        liveTool = null; break;
      case 'confirmation': finishTool(liveTool, true, ev.label); liveTool = null; confirmCard(ev); break;
      case 'error': bubble('bot', ev.message); if (liveTool) finishTool(liveTool, false, 'Stopped'); break;
      default: break;
    }
  }

  function stream(url, body) {
    if (busy) return; busy = true; sendBtn.disabled = true;
    fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: JSON.stringify(body) })
      .then(function (resp) {
        if (!resp.ok) return resp.json().then(function (j) { throw new Error(j.error || ('Request failed (' + resp.status + ')')); });
        var reader = resp.body.getReader(), dec = new TextDecoder('utf-8'), buf = '';
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) { if (buf.trim()) handle(JSON.parse(buf)); return; }
            buf += dec.decode(r.value, { stream: true });
            var lines = buf.split('\n'); buf = lines.pop();
            lines.forEach(function (ln) { if (ln.trim()) { try { handle(JSON.parse(ln)); } catch (e) { /* ignore partial line */ } } });
            return pump();
          });
        }
        return pump();
      })
      .catch(function (e) { bubble('bot', e.message || 'Something went wrong.'); })
      .then(function () { busy = false; sendBtn.disabled = root.dataset.configured !== '1'; liveTool = null; });
  }

  function send() {
    var t = txt.value.trim(); if (!t || busy) return;
    bubble('user', t, voiceUsed ? 'sent by voice' : null);
    stream(root.dataset.messageUrl, { message: t, language: lang(), voice: voiceUsed });
    txt.value = ''; voiceUsed = false;
  }
  sendBtn.onclick = send;
  txt.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('resetBtn').onclick = function () {
    fetch(root.dataset.resetUrl, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf } }).then(function () { log.textContent = ''; greet(); });
  };

  /* voice: text goes into the box for review first - it is never sent automatically */
  var mic = $('micBtn');
  var voice = new CiviVoice({
    getLang: lang,
    onState: function (s) { mic.classList.toggle('listening', s === 'listening'); if (s === 'listening') say('Listening (' + CiviVoice.locale(lang()) + ')…'); },
    onInterim: function (t) { say(t); },
    onFinal: function (t) { txt.value = (txt.value ? txt.value + ' ' : '') + t; txt.setAttribute('lang', lang()); voiceUsed = true; say('Recognised - check or edit the text, then press Send.', 'ok'); },
    onError: function (c, m) { say(m, 'err'); }
  });
  if (!voice.supported) { mic.disabled = true; say(CiviVoice.messages.unsupported, 'err'); }
  mic.onclick = function () { if (voice.listening) voice.stop(); else voice.start(); };
  txt.addEventListener('input', function () { if (!txt.value) voiceUsed = false; });

  function greet() { bubble('bot', 'Hello! I can help you register and track complaints, check for duplicates and look at civic hotspots. Ask in English or ಕನ್ನಡ.'); }
  greet();
  try { var pend = JSON.parse($('pendingData').textContent); if (pend && pend.length) { bubble('bot', 'You have an action waiting for your confirmation:'); confirmCard({ pending_id: pend[0].pending_id, tool: pend[0].tool, preview: pend[0].preview }); } } catch (e) { /* none */ }
})();
