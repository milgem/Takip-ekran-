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
    table.querySelectorAll('tr[data-row]').forEach(tr => {
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
