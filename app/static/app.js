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
