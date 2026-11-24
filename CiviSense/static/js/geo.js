/* Geographical analysis map. Every marker is a REAL complaint coordinate from the database; nothing is generated,
 * jittered or guessed. Points stored at exactly the same coordinate arrive grouped (one marker, count n). */
(function () {
  'use strict';
  var BLR = { south: 12.70, north: 13.30, west: 77.30, east: 77.90, center: [12.9716, 77.5946] };
  var $ = function (id) { return document.getElementById(id); };
  var colors = ['#dc3545', '#0d6efd', '#198754', '#fd7e14', '#6f42c1', '#d63384', '#0dcaf0', '#ffc107'];
  var map = L.map('map', { preferCanvas: true, maxBounds: [[BLR.south, BLR.west], [BLR.north, BLR.east]], maxBoundsViscosity: 0.9, minZoom: 10 }).setView(BLR.center, 11);
  var tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
  var tileErrors = 0;
  tiles.on('tileerror', function () { if (++tileErrors >= 3) $('mapNotice').classList.remove('d-none'); });
  tiles.on('tileload', function () { tileErrors = 0; $('mapNotice').classList.add('d-none'); });
  var pointLayer = L.layerGroup().addTo(map), circleLayer = L.layerGroup().addTo(map), last = null, filters = {};

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function popupFor(pt, marker) {
    var box = el('div'); box.style.minWidth = '220px'; box.appendChild(el('div', 'text-muted small', 'Loading…'));
    var q = new URLSearchParams({ lat: pt[0], lon: pt[1], category: filters.category, ward: filters.ward, source: filters.source });
    fetch(window.GEO.pointUrl + '?' + q).then(function (r) { return r.json(); }).then(function (d) {
      box.textContent = '';
      box.appendChild(el('div', 'fw-semibold mb-1', d.total + (d.total === 1 ? ' complaint' : ' complaints') + ' at ' + pt[0].toFixed(5) + ', ' + pt[1].toFixed(5)));
      box.appendChild(el('div', 'small text-muted mb-1', pt[2] >= 0 ? 'Part of hotspot #' + pt[2] : 'Not part of a hotspot'));
      if (pt[8]) box.appendChild(el('div', 'small text-warning-emphasis mb-1', 'Note: this pin is about ' + pt[8] + ' km from the usual area of its recorded ward - the recorded location may be inaccurate. It is shown exactly as stored.'));
      if (d.total > 1) box.appendChild(el('div', 'small text-muted mb-1', 'Several complaints share this exact coordinate (possibly a default or approximate location).'));
      var list = el('div'); list.style.maxHeight = '220px'; list.style.overflow = 'auto';
      d.complaints.forEach(function (c) {
        var row = el('div', 'border-top py-1 small');
        var a = el('a', 'fw-semibold', c.ref); a.href = window.GEO.complaintUrl.replace('ZZREFZZ', encodeURIComponent(c.ref)); row.appendChild(a);
        row.appendChild(el('span', 'ms-1', c.priority + ' · ' + c.status));
        row.appendChild(el('div', null, c.title));
        row.appendChild(el('div', 'text-muted', c.category + ' · ' + c.ward + ' · ' + c.reported + (c.address ? ' · ' + c.address : '')));
        list.appendChild(row);
      });
      box.appendChild(list);
      if (d.total > d.shown) box.appendChild(el('div', 'small text-muted pt-1', 'Showing the ' + d.shown + ' highest-priority of ' + d.total + '.'));
      marker.getPopup().update();
    }).catch(function () { box.textContent = 'Could not load details.'; });
    return box;
  }

  function draw() {
    pointLayer.clearLayers(); circleLayer.clearLayers();
    if (!last) return;
    var showNoise = $('t-noise').checked;
    // draw noise first so hotspot points stay on top
    last.points.slice().sort(function (a, b) { return (a[2] >= 0) - (b[2] >= 0); }).forEach(function (pt) {
      var clustered = pt[2] >= 0; if (!clustered && !showNoise) return;
      var m = L.circleMarker([pt[0], pt[1]], { radius: (clustered ? 4 : 3) + Math.min(Math.sqrt(pt[3] - 1) * 1.5, 9), weight: pt[3] > 1 ? 1.5 : 0.5, color: pt[8] ? '#000' : '#fff',
        fillOpacity: clustered ? 0.8 : 0.5, fillColor: clustered ? colors[pt[2] % colors.length] : '#8a949e' });
      m.bindPopup(function () { return popupFor(pt, m); }, { maxWidth: 340 });
      if (pt[3] > 1) m.bindTooltip(pt[3] + ' complaints at this coordinate');
      m.addTo(pointLayer);
    });
    if ($('t-circles').checked) last.result.clusters.slice(0, 60).forEach(function (c) {
      L.circle([c.lat, c.lon], { radius: Math.max(c.radius_m, 60) + 40, color: colors[c.id % colors.length], weight: 2, fillOpacity: 0.08 })
        .bindTooltip('Hotspot #' + c.id + ': ' + c.size + ' complaints (DBSCAN)').addTo(circleLayer);
    });
  }

  function load() {
    filters = { category: $('f-cat').value, ward: $('f-ward').value, source: $('f-src').value };
    var p = new URLSearchParams({ category: filters.category, ward: filters.ward, source: filters.source, eps: $('f-eps').value, min_samples: $('f-ms').value });
    $('stats').textContent = 'Running DBSCAN…';
    fetch(window.GEO.apiUrl + '?' + p).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function (d) {
      last = d; var r = d.result; draw();
      if (d.bounds) map.fitBounds(d.bounds, { padding: [30, 30], maxZoom: 15 }); else map.setView(BLR.center, 11);
      var tb = $('clusters'); tb.textContent = '';
      r.clusters.slice(0, 60).forEach(function (c) {
        var tr = el('tr'); tr.style.cursor = 'pointer';
        [c.id, c.size, c.top_category + ' (' + Math.round(c.top_category_share * 100) + '%)', c.top_ward].forEach(function (v) { tr.appendChild(el('td', null, String(v))); });
        tr.onclick = function () { map.setView([c.lat, c.lon], 16); }; tb.appendChild(tr);
      });
      if (!r.clusters.length) { var tr0 = el('tr'), td = el('td', 'text-muted p-3', 'No hotspots for these settings. Try a larger radius or fewer minimum complaints.'); td.colSpan = 4; tr0.appendChild(td); tb.appendChild(tr0); }
      var st = $('stats'); st.textContent = '';
      function line(b, t) { var d0 = el('div'); d0.appendChild(el('b', null, String(b))); d0.appendChild(document.createTextNode(' ' + t)); st.appendChild(d0); }
      line(r.n_clusters, 'hotspot clusters'); line(r.noise_points, 'noise points');
      line(r.clustered_points || 0, 'clustered of ' + r.n_points + ' mapped complaints (' + d.points.length + ' distinct map points; ' + d.stacked_points + ' hold several complaints at one coordinate)');
      line(d.without_coordinates, 'matching complaints without coordinates (not plotted)');
      if (d.outside_bengaluru) line(d.outside_bengaluru, 'complaints with coordinates outside the Bengaluru area (not plotted)');
      if (r.silhouette !== null && r.silhouette !== undefined) st.appendChild(el('div', null, 'Silhouette (clustered points, haversine): ' + r.silhouette.toFixed(3)));
      var w = $('wards'); w.textContent = '';
      d.top_wards.forEach(function (x) { var row = el('div', 'd-flex justify-content-between'); row.appendChild(el('span', null, x.ward)); row.appendChild(el('b', null, String(x.n))); w.appendChild(row); });
      if (!d.top_wards.length) w.appendChild(el('span', 'text-muted', 'No data'));
    }).catch(function (e) { $('stats').textContent = 'Could not load map data (' + e.message + ').'; });
  }
  $('apply').onclick = load; $('t-noise').onchange = draw; $('t-circles').onchange = draw; load();
})();
