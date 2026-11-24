/* Complaint form: location picker (Bengaluru only), live category suggestions, and the voice / typed review flow. */
(function () {
  'use strict';
  var BLR = { south: 12.70, north: 13.30, west: 77.30, east: 77.90, center: [12.9716, 77.5946] };
  var $ = function (id) { return document.getElementById(id); };
  var csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';

  /* ---------- map ---------- */
  var map = L.map('pickmap', { maxBounds: [[BLR.south, BLR.west], [BLR.north, BLR.east]], maxBoundsViscosity: 1, minZoom: 10 })
    .setView(BLR.center, 11);
  var tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
  var tileErrors = 0;
  tiles.on('tileerror', function () { if (++tileErrors >= 3) $('mapNotice').classList.remove('d-none'); });
  tiles.on('tileload', function () { tileErrors = 0; $('mapNotice').classList.add('d-none'); });
  var marker = null, lat = $('id_latitude'), lon = $('id_longitude'), wardSel = $('id_ward');
  function inBlr(a, b) { return a >= BLR.south && a <= BLR.north && b >= BLR.west && b <= BLR.east; }
  function setWard(w, note) {
    if (!w) return;
    wardSel.value = String(w.id);
    $('wardHint').textContent = note || ('Estimated ward: ' + w.name + ' (not an official boundary - please confirm)');
  }
  function setPin(a, b) {
    if (!inBlr(a, b)) { $('coordText').textContent = 'That point is outside Bengaluru - please choose a point inside the city.'; return; }
    if (marker) marker.setLatLng([a, b]); else marker = L.marker([a, b]).addTo(map);
    lat.value = a.toFixed(6); lon.value = b.toFixed(6);
    $('coordText').textContent = a.toFixed(5) + ', ' + b.toFixed(5);
    fetch('/api/ward-from-point/?lat=' + a + '&lon=' + b).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ward && !wardSel.value) setWard(d.ward, 'Nearest ward centre: ' + d.ward.name + ' (estimate from past complaint locations - please confirm)');
    }).catch(function () { /* ward hint is optional */ });
  }
  map.on('click', function (e) { setPin(e.latlng.lat, e.latlng.lng); });
  if (lat.value && lon.value) { setPin(parseFloat(lat.value), parseFloat(lon.value)); map.setView([lat.value, lon.value], 15); }
  $('locate').onclick = function () {
    if (!navigator.geolocation) { alert('Geolocation is not supported by this browser. Click the map instead.'); return; }
    navigator.geolocation.getCurrentPosition(function (p) {
      if (!inBlr(p.coords.latitude, p.coords.longitude)) { $('coordText').textContent = 'Your location is outside Bengaluru - click the map to choose a point.'; return; }
      setPin(p.coords.latitude, p.coords.longitude); map.setView([p.coords.latitude, p.coords.longitude], 16);
    }, function () { alert('Location permission denied - click the map instead.'); });
  };

  /* ---------- category suggestions while typing ---------- */
  var timer = null, desc = $('id_description'), title = $('id_title');
  function suggest() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      fetch('/api/suggest-category/?text=' + encodeURIComponent(title.value + ' ' + desc.value)).then(function (r) { return r.json(); }).then(function (d) {
        var box = $('suggestBox'), list = $('suggestList'); list.textContent = '';
        if (!d.suggestions.length) { box.classList.add('d-none'); return; }
        box.classList.remove('d-none');
        d.suggestions.forEach(function (s) {
          var a = document.createElement('a'); a.className = 'list-group-item list-group-item-action sugg d-flex justify-content-between'; a.href = '#';
          var l = document.createElement('span'); l.textContent = s.name + (s.department ? '  [' + s.department + ']' : '');
          var r = document.createElement('span'); r.className = 'text-muted'; r.textContent = Math.round(s.probability * 100) + '%';
          a.appendChild(l); a.appendChild(r);
          a.onclick = function (e) { e.preventDefault(); $('id_category').value = s.id; };
          list.appendChild(a);
        });
      }).catch(function () { /* suggestions are optional */ });
    }, 500);
  }
  desc.addEventListener('input', suggest); title.addEventListener('input', suggest);

  /* ---------- voice / typed review ---------- */
  var vt = $('voiceText'), status = $('voiceStatus'), mic = $('micBtn'), voiceUsed = ($('id_input_method').value === 'VOICE');
  function lang() { return document.querySelector('input[name=vlang]:checked').value; }
  function setLangAttr() { vt.setAttribute('lang', lang()); desc.setAttribute('lang', lang()); title.setAttribute('lang', lang()); }
  function say(msg, cls) { status.textContent = msg; status.className = 'voice-status' + (cls ? ' ' + cls : ''); }
  document.querySelectorAll('input[name=vlang]').forEach(function (r) { r.addEventListener('change', setLangAttr); });
  setLangAttr();
  $('reviewBox').classList.toggle('d-none', !voiceUsed);

  var voice = new CiviVoice({
    getLang: lang,
    onState: function (s) {
      mic.classList.toggle('listening', s === 'listening');
      mic.setAttribute('aria-label', s === 'listening' ? 'Stop recording' : 'Start recording');
      if (s === 'listening') say('Listening (' + CiviVoice.locale(lang()) + ')… speak now.');
    },
    onInterim: function (t) { $('interimText').textContent = t; },
    onFinal: function (t) {
      $('interimText').textContent = '';
      vt.value = (vt.value ? vt.value.replace(/\s+$/, '') + ' ' : '') + t;
      voiceUsed = true; $('id_input_method').value = 'VOICE'; $('reviewBox').classList.remove('d-none');
      say('Recognised. Please read the text, fix any mistakes, then press "Review & fill the form". Press the microphone to add more or record again after clearing.', 'ok');
    },
    onError: function (code, msg) { $('interimText').textContent = ''; say(msg, 'err'); }
  });
  if (!voice.supported) { mic.disabled = true; say(CiviVoice.messages.unsupported, 'err'); }
  mic.onclick = function () { if (voice.listening) voice.stop(); else voice.start(); };
  $('clearVoice').onclick = function () { vt.value = ''; $('interimText').textContent = ''; $('analysis').classList.add('d-none'); say('Cleared. Press the microphone to record again, or type.'); };

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function renderAnalysis(d) {
    var box = $('analysis'); box.textContent = ''; box.classList.remove('d-none');
    d.problems.forEach(function (p) { box.appendChild(el('div', 'text-danger small mb-1', p)); });
    d.notes.forEach(function (p) { box.appendChild(el('div', 'small text-muted mb-1', p)); });
    box.appendChild(el('h6', null, 'Predicted category (click to choose)'));
    var row = el('div', 'mb-2');
    if (!d.categories.length) row.appendChild(el('span', 'small text-muted', 'No prediction - please choose a category in the form.'));
    d.categories.forEach(function (c) {
      var chip = el('span', 'chip', c.name + ' · ' + Math.round(c.probability * 100) + '%' + (c.department ? ' · ' + c.department : ''));
      chip.onclick = function () { $('id_category').value = c.id; row.querySelectorAll('.chip').forEach(function (x) { x.classList.remove('active'); }); chip.classList.add('active'); };
      row.appendChild(chip); row.appendChild(document.createTextNode(' '));
    });
    box.appendChild(row);
    box.appendChild(el('h6', null, 'Recommended priority'));
    var pr = el('div', 'mb-2'); pr.appendChild(el('span', 'prio prio-' + d.priority.level, d.priority.level + ' (' + d.priority.score + '/100)'));
    pr.appendChild(el('span', 'small text-muted ms-2', 'Estimate - officers confirm the final priority.')); box.appendChild(pr);
    box.appendChild(el('h6', null, 'Possible duplicates'));
    if (!d.duplicates.length) box.appendChild(el('div', 'small text-muted', 'No similar reports found.'));
    d.duplicates.forEach(function (x) {
      box.appendChild(el('div', 'small', (x.ref ? x.ref + ' - ' : '') + x.title + ' (' + (x.status || '') + ', similarity ' + x.similarity + (x.distance_m != null ? ', ' + x.distance_m + ' m away' : '') + ')'));
    });
    if (d.duplicates.length) box.appendChild(el('div', 'small text-muted mt-1', 'You can still submit - similar reports help officers see how widespread an issue is.'));
    box.appendChild(el('div', 'small fw-semibold mt-2', 'The form below has been filled in. Check it, then press "Submit complaint" to confirm. Nothing has been submitted yet.'));
  }
  $('analyzeBtn').onclick = function () {
    var text = vt.value.trim();
    if (text.length < 5) { say('Please speak or type your complaint first.', 'err'); return; }
    var body = { text: text, severity: parseInt($('id_severity').value || '3', 10), public_impact: parseInt($('id_public_impact').value || '2', 10) };
    if (lat.value && lon.value) { body.latitude = parseFloat(lat.value); body.longitude = parseFloat(lon.value); }
    $('analyzeBtn').disabled = true; say('Reviewing…');
    fetch($('voiceBox').dataset.analyzeUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.j.error || 'Could not review the text.', 'err'); return; }
        var d = res.j;
        title.value = d.title; desc.value = d.description;           // description = the citizen's own words, unchanged
        if (d.location_text && !$('id_address').value) $('id_address').value = d.location_text;
        if (d.ward && !wardSel.value) setWard(d.ward, d.ward_note);
        if (d.suggested_severity && $('id_severity').value === '3') $('id_severity').value = String(d.suggested_severity);
        $('id_original_language').value = d.language; setLangAttr();
        renderAnalysis(d); suggest();
        say('Review complete - check the form fields below.', 'ok');
      }).catch(function () { say('Could not reach the server. Your text is safe in the box - try again.', 'err'); })
      .then(function () { $('analyzeBtn').disabled = false; });
  };
})();
