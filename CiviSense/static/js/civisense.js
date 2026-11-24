function csPlot(scriptId, divId){
  const el=document.getElementById(divId), s=document.getElementById(scriptId);
  if(!el||!s) return;
  const spec=JSON.parse(s.textContent);
  // Presentation-only: remap the legacy Bootstrap chart palette onto the CiviSense theme.
  const MAP={'#0d6efd':'#f5a623','#20c997':'#111c29','#fd7e14':'#2f7fb8','#6f42c1':'#3d5573','#d63384':'#c2701a','#198754':'#1f9d6b','#ffc107':'#f7c56b','#0dcaf0':'#7fb3d5','#dc3545':'#d9443a','#6c757d':'#8a96a8','#9ec5fe':'#f8d79c'};
  const remap=v=>{ if(typeof v==='string'){ const k=v.toLowerCase(); return MAP[k]||v; } if(Array.isArray(v)) return v.map(remap); if(v&&typeof v==='object'){ for(const k in v){ if(k==='colorscale'&&v[k]==='Blues'){ v[k]=[[0,'#fffaf0'],[0.5,'#f5a623'],[1,'#111c29']]; } else v[k]=remap(v[k]); } } return v; };
  (spec.data||[]).forEach(remap);
  const layout=Object.assign({}, spec.layout||{});
  // Presentation-only theme: keeps the original data and any explicit settings.
  layout.font=Object.assign({family:"Inter, 'Noto Sans Kannada', 'Nirmala UI', system-ui, sans-serif", color:'#111c29', size:12}, layout.font||{});
  if(!layout.paper_bgcolor) layout.paper_bgcolor='rgba(0,0,0,0)';
  if(!layout.plot_bgcolor) layout.plot_bgcolor='rgba(0,0,0,0)';
  if(!layout.colorway) layout.colorway=['#f5a623','#111c29','#2f7fb8','#1f9d6b','#d9443a','#8a96a8','#f7c56b','#3d5573'];
  ['xaxis','yaxis'].forEach(a=>{ layout[a]=Object.assign({gridcolor:'#efe9df', zerolinecolor:'#e6dfd2', linecolor:'#e6dfd2'}, layout[a]||{}); });
  Plotly.newPlot(el, spec.data, layout, {responsive:true, displaylogo:false});
}
document.addEventListener('DOMContentLoaded',()=>{
  const t=document.getElementById('sidebarToggle'), sb=document.querySelector('.cs-sidebar'), bd=document.querySelector('.cs-backdrop');
  const toggle=()=>{ sb.classList.toggle('show'); if(bd) bd.classList.toggle('show'); };
  if(t&&sb) t.addEventListener('click',toggle);
  if(bd&&sb) bd.addEventListener('click',toggle);
  // Loading state on submit buttons (does not block submission)
  document.querySelectorAll('form[method="post"]').forEach(f=>f.addEventListener('submit',()=>{
    const b=f.querySelector('button[type=submit],button:not([type])');
    if(b && !b.dataset.noload){ setTimeout(()=>{ b.disabled=true; b.insertAdjacentHTML('afterbegin','<span class="spinner-border spinner-border-sm me-1"></span>'); },0); }
  }));
});
