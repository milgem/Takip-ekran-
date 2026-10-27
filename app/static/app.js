function toast(msg, ms) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), ms || 4000);
}
async function post(url, btn, reload) {
  if (btn) { btn.disabled = true; btn._t = btn.textContent; btn.textContent = 'Bekleyin…'; }
  try {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin' });
    const b = await r.json().catch(() => ({ ok: r.ok, message: r.statusText }));
    toast((b.ok ? '✓ ' : '✕ ') + b.message, b.ok ? 4000 : 9000);
    if (b.ok && reload) setTimeout(() => location.reload(), reload);
  } catch (e) { toast('✕ ' + e.message, 8000); }
  finally { if (btn) { btn.disabled = false; btn.textContent = btn._t; } }
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function tl(v) { return '₺' + Math.round(v).toLocaleString('tr-TR'); }

/* ---- Tablolar: başlığa tıklayınca sıralama, arama ve açılır filtreler ---- */
function cellValue(td) {
  if (!td) return '';
  if (td.dataset.v !== undefined) { const n = parseFloat(td.dataset.v); return isNaN(n) ? td.dataset.v.toLowerCase() : n; }
  const t = (td.querySelector('b') || td).textContent.trim();
  if (t === '' || t === '–' || t === '—') return null;
  const num = t.replace(/[₺%\s]|saat/g, '').replace(/\./g, '').replace(',', '.');
  if (/^-?\d+(\.\d+)?$/.test(num)) return parseFloat(num);
  return t.toLocaleLowerCase('tr-TR');
}
function makeSortable(table) {
  const head = table.querySelector(':scope > tbody > tr, :scope > tr');
  if (!head) return;
  head.querySelectorAll('th').forEach((th, i) => {
    if (th.hasAttribute('data-nosort')) return;
    th.classList.add('sortable');
    th.tabIndex = 0;
    th.title = 'Sırala';
    const go = () => {
      const dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
      head.querySelectorAll('th').forEach(x => { delete x.dataset.dir; });
      th.dataset.dir = dir;
      const rows = [...table.querySelectorAll(':scope > tbody > tr, :scope > tr')].slice(1).filter(r => r.querySelector('td') && !r.classList.contains('detail') && !r.querySelector(':scope > td[colspan]'));
      rows.sort((a, b) => {
        const x = cellValue(a.children[i]), y = cellValue(b.children[i]);
        if (x === null && y === null) return 0;
        if (x === null) return 1;            // boş değerler hep sonda
        if (y === null) return -1;
        const c = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x).localeCompare(String(y), 'tr');
        return dir === 'asc' ? c : -c;
      });
      const parent = rows.length ? rows[0].parentNode : null;
      rows.forEach(r => {
        const d = r.nextElementSibling && r.nextElementSibling.classList.contains('detail') ? r.nextElementSibling : null;
        parent.appendChild(r);
        if (d) parent.appendChild(d);
      });
    };
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
  });
}
function wireFilters(bar) {
  const table = document.getElementById(bar.dataset.filterFor);
  if (!table) return;
  const inputs = bar.querySelectorAll('input[data-f], select[data-f]');
  const count = bar.querySelector('[data-count]');
  const apply = () => {
    let shown = 0, total = 0;
    table.querySelectorAll('[data-row]').forEach(tr => {
      total++;
      let ok = true;
      inputs.forEach(el => {
        const v = el.type === 'checkbox' ? (el.checked ? el.value : '') : el.value.trim();
        if (!v) return;
        if (el.dataset.f === 'q') {
          ok = ok && (tr.dataset.q || tr.textContent).toLocaleLowerCase('tr-TR').includes(v.toLocaleLowerCase('tr-TR'));
        } else {
          ok = ok && (tr.dataset[el.dataset.f] || '') === v;
        }
      });
      tr.classList.toggle('f-out', !ok);
      if (ok) shown++;
    });
    if (count) count.textContent = shown === total ? total + ' kayıt' : shown + ' / ' + total + ' kayıt';
  };
  inputs.forEach(el => el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input', apply));
  apply();
}
function wireCardSort(sel) {
  const grid = document.getElementById(sel.dataset.sortCards);
  if (!grid) return;
  const apply = () => {
    const key = sel.value;
    const cards = [...grid.children];
    cards.sort((a, b) => key === 'name'
      ? a.dataset.name.localeCompare(b.dataset.name, 'tr')
      : (parseFloat(b.dataset[key]) || -1) - (parseFloat(a.dataset[key]) || -1));
    cards.forEach(c => grid.appendChild(c));
    try { localStorage.setItem('cardSort', key); } catch (e) {}
  };
  try { const k = localStorage.getItem('cardSort'); if (k && sel.querySelector('option[value="' + k + '"]')) sel.value = k; } catch (e) {}
  sel.addEventListener('change', apply);
  if (sel.value !== 'default') apply();
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('table.sortable-table').forEach(makeSortable);
  document.querySelectorAll('[data-filter-for]').forEach(wireFilters);
  document.querySelectorAll('select[data-sort-cards]').forEach(wireCardSort);
});

/* ---- Açılır satırlar: ürüne tıklayınca beden kırılımı ---- */
function wireExpandable(table) {
  table.querySelectorAll('tr.parent').forEach(tr => {
    const d = document.getElementById(tr.getAttribute('aria-controls'));
    if (!d) return;
    const toggle = () => {
      const open = tr.getAttribute('aria-expanded') !== 'true';
      tr.setAttribute('aria-expanded', open ? 'true' : 'false');
      d.hidden = !open;
    };
    tr.addEventListener('click', e => { if (!e.target.closest('a,button,input')) toggle(); });
    tr.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  });
}
document.addEventListener('DOMContentLoaded', () => document.querySelectorAll('table.expandable').forEach(wireExpandable));

/* ---- Firma sayfası sekmeleri ---- */
document.addEventListener('DOMContentLoaded', () => {
  const nav = document.querySelector('nav.tabs[data-tabs="local"]');
  if (!nav) return;
  const panels = [...document.querySelectorAll('.tab-panel')];
  const names = panels.map(p => p.dataset.tab);
  const show = (name, push) => {
    if (!names.includes(name)) name = names[0];
    panels.forEach(p => { p.hidden = p.dataset.tab !== name; });
    nav.querySelectorAll('a[data-tab]').forEach(a => {
      const on = a.dataset.tab === name;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    if (push) history.replaceState(null, '', '#' + name);
    // gizliyken çizilen grafikler görünür olunca boyutlansın
    if (window.Chart) document.querySelectorAll('#tab-' + name + ' canvas').forEach(c => { const ch = Chart.getChart(c); if (ch) ch.resize(); });
    // tarih seçimi linkleri açık sekmeyi korusun
    document.querySelectorAll('.chips a[href^="/firma"], .chips a[href^="?"], form#custom').forEach(el => {
      const attr = el.tagName === 'FORM' ? 'action' : 'href';
      const v = (el.getAttribute(attr) || '').split('#')[0];
      el.setAttribute(attr, v + '#' + name);
    });
  };
  nav.querySelectorAll('a[data-tab]').forEach(a => a.addEventListener('click', e => {
    e.preventDefault(); show(a.dataset.tab, true); window.scrollTo({ top: nav.offsetTop - 70, behavior: 'smooth' });
  }));
  show(location.hash.slice(1), false);
  window.addEventListener('hashchange', () => show(location.hash.slice(1), false));
});

document.addEventListener('click', e => {
  const a = e.target.closest('[data-goto-tab]');
  if (!a) return;
  const t = document.querySelector('nav.tabs a[data-tab="' + a.dataset.gotoTab + '"]');
  if (t) { e.preventDefault(); t.click(); setTimeout(() => { const el = document.getElementById('odemesiz'); if (el) el.scrollIntoView({ behavior: 'smooth' }); }, 50); }
});

document.addEventListener('click', e => {
  const a = e.target.closest('a[data-goto]');
  if (!a) return;
  const t = document.querySelector('nav.tabs a[data-tab="' + a.dataset.goto + '"]');
  if (t) { e.preventDefault(); t.click(); }
});

document.addEventListener('click', e => {
  const a = e.target.closest('[data-best]');
  if (!a) return;
  e.preventDefault();
  const box = a.closest('.card');
  box.querySelectorAll('[data-best]').forEach(x => x.classList.toggle('on', x === a));
  box.querySelectorAll('[data-best-panel]').forEach(p => { p.hidden = p.dataset.bestPanel !== a.dataset.best; });
});

/* ---- Ürün analizi: özet kartına tıklayınca sadece o bölüm ---- */
document.addEventListener('DOMContentLoaded', () => {
  const kpis = document.querySelectorAll('[data-focus]');
  if (!kpis.length) return;
  const sections = [...document.querySelectorAll('[data-section]')];
  const bar = document.getElementById('focus-bar'), label = document.getElementById('focus-label');
  const names = { 'tukenen': 'Sadece satışı olup stoğu tükenen bedenler gösteriliyor', 'azalan': 'Sadece stoğu azalan bedenler gösteriliyor',
                  'cok-satan': 'Sadece çok satan ürünler gösteriliyor', 'bedenler': 'Sadece beden bazlı satışlar gösteriliyor' };
  const focus = (id, push) => {
    const ok = sections.some(s => s.id === id);
    sections.forEach(s => { s.hidden = ok && s.id !== id; });
    kpis.forEach(k => k.classList.toggle('on', ok && k.dataset.focus === id));
    bar.hidden = !ok;
    if (ok) { label.textContent = names[id] || ''; document.getElementById(id).style.marginTop = '0'; }
    else sections.forEach(s => { s.style.marginTop = ''; });
    if (push) history.replaceState(null, '', ok ? '#' + id : location.pathname + location.search);
  };
  kpis.forEach(k => k.addEventListener('click', e => {
    e.preventDefault();
    focus(k.classList.contains('on') ? '' : k.dataset.focus, true);
    bar.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }));
  document.getElementById('focus-clear').addEventListener('click', e => { e.preventDefault(); focus('', true); });
  if (location.hash) focus(location.hash.slice(1), false);
  window.addEventListener('hashchange', () => focus(location.hash.slice(1), false));
});

/* ---- Firma kartlarındaki bildirim zili ---- */
document.addEventListener('click', e => {
  const btn = e.target.closest('button.bell');
  const open = document.querySelector('button.bell[aria-expanded="true"]');
  if (btn) {
    e.preventDefault(); e.stopPropagation();
    const pop = document.getElementById(btn.getAttribute('aria-controls'));
    const willOpen = btn.getAttribute('aria-expanded') !== 'true';
    if (open && open !== btn) { open.setAttribute('aria-expanded', 'false'); document.getElementById(open.getAttribute('aria-controls')).hidden = true; }
    btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    pop.hidden = !willOpen;
    return;
  }
  if (open && !e.target.closest('.notes')) {
    open.setAttribute('aria-expanded', 'false');
    document.getElementById(open.getAttribute('aria-controls')).hidden = true;
  }
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const open = document.querySelector('button.bell[aria-expanded="true"]');
  if (open) { open.setAttribute('aria-expanded', 'false'); document.getElementById(open.getAttribute('aria-controls')).hidden = true; open.focus(); }
});

/* ---- Sipariş önerisi: listeyi panoya kopyala (sekmeyle ayrılmış, Excel'e yapıştırılabilir) ---- */
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-copy]');
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const t = document.querySelector('#' + b.dataset.copy + ' table.copy-src');
  if (!t) return;
  const text = [...t.querySelectorAll('tr')].map(r => [...r.children].map(c => c.innerText.trim()).join('\t')).join('\n');
  try { await navigator.clipboard.writeText(text); toast('✓ Liste kopyalandı; Excel ya da e-postaya yapıştırabilirsin.'); }
  catch (err) { toast('Kopyalanamadı: ' + err.message, 6000); }
});

/* ---- Ürün kartları: sıralama ve kopyalama ---- */
document.addEventListener('change', e => {
  const sel = e.target.closest('select[data-sort-list]');
  if (!sel) return;
  const box = document.getElementById(sel.dataset.sortList);
  const key = sel.value;
  [...box.children].sort((a, b) => key === 'name' ? a.dataset.name.localeCompare(b.dataset.name, 'tr')
    : key === 'urgency' ? (+a.dataset.urgency) - (+b.dataset.urgency) : (+b.dataset[key]) - (+a.dataset[key]))
    .forEach(c => box.appendChild(c));
});
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-copy-card]');
  if (!b) return;
  e.preventDefault();
  const card = b.closest('details');
  const t = card.querySelector('table.copy-src');
  const head = card.querySelector('.pc-title').innerText.replace('›', '').trim();
  const text = head + '\n' + [...t.querySelectorAll('tr')].map(r => [...r.children].map(c => c.innerText.trim()).join('\t')).join('\n');
  try { await navigator.clipboard.writeText(text); toast('✓ Liste kopyalandı; Excel ya da e-postaya yapıştırabilirsin.'); }
  catch (err) { toast('Kopyalanamadı: ' + err.message, 6000); }
});

/* ---- Uzun tablolar: ilk açılışta 10 satır, "Tümünü göster" ---- */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('table.limited').forEach(t => {
    const rows = [...t.querySelectorAll(':scope > tbody > tr, :scope > tr')].filter(r => r.querySelector('td') && !r.classList.contains('detail'));
    if (rows.length <= 12) return;
    rows.slice(10).forEach(r => { r.classList.add('lim-hide'); const d = r.nextElementSibling; if (d && d.classList.contains('detail')) d.classList.add('lim-hide'); });
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'btn sm lim-more'; b.textContent = 'Tümünü göster (' + rows.length + ')';
    b.addEventListener('click', () => { t.querySelectorAll('.lim-hide').forEach(r => r.classList.remove('lim-hide')); b.remove(); });
    (t.closest('.table-scroll') || t).after(b);
  });
  // yardım balonu dışarı tıklayınca kapansın
  document.addEventListener('click', e => { document.querySelectorAll('details.help[open], details.fswitch[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; }); });
});

/* ---- Tema: otomatik -> açık -> koyu ---- */
document.addEventListener('DOMContentLoaded', () => {
  const b = document.getElementById('themeBtn'); if (!b) return;
  const names = {auto: 'otomatik (cihaza göre)', light: 'açık', dark: 'koyu'};
  const cur = () => document.documentElement.dataset.theme || 'auto';
  const label = () => { const t = 'Tema: ' + names[cur()]; b.title = t; b.setAttribute('aria-label', t); };
  label();
  b.addEventListener('click', () => {
    const next = {auto: 'light', light: 'dark', dark: 'auto'}[cur()];
    if (next === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
    try { next === 'auto' ? localStorage.removeItem('tema') : localStorage.setItem('tema', next); } catch (e) {}
    label();
    if (window.Chart) setTimeout(() => location.reload(), 50);
  });
});
