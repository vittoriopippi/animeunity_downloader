/* Queue page: fills each show's row from /api/queue/status/ and keeps it live. */
(() => {
  'use strict';
  const list = document.getElementById('queue');
  if (!list) return;

  const $ = (sel, el) => el.querySelector(sel);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const token = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const OPEN_KEY = 'animedl-open';
  const open = new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || '[]'));

  const LED = { completed: 'up', downloading: 'busy', pending: 'part', failed: 'err', skipped: 'off', cancelled: 'off' };
  const PILL = {
    pending: ['wait', 'WAITING'], completed: ['done', 'DONE'], failed: ['fail', 'FAILED'],
    skipped: ['skip', 'SKIPPED'], cancelled: ['stop', 'CANCELLED'],
  };
  // episode status -> [action, label, color, needs a second tap]
  const EP_ACT = {
    pending: ['skip', 'SKIP', 'warm'], downloading: ['cancel', 'CANCEL', 'bad', true],
    cancelled: ['resume', 'RESUME', 'go'], skipped: ['resume', 'RESUME', 'go'], failed: ['retry', 'RETRY', 'go'],
  };
  const shown = {};  // what each element shows now, so unchanged parts aren't redrawn (an armed button stays armed)

  function segs(pct, n, cls) {
    const on = Math.round((pct / 100) * n);
    let h = '';
    for (let i = 0; i < n; i++) h += i < on ? '<i class="on"></i>' : '<i></i>';
    return `<span class="segs ${cls}">${h}</span>`;
  }

  function epName(n) {
    return /^\d+$/.test(n) ? 'EP ' + n.padStart(2, '0') : esc(n.toUpperCase());
  }

  function epHTML(ep) {
    let st;
    if (ep.status === 'downloading') {
      st = segs(ep.progress, 10, 'dl sm') + `<span class="ep-pct">${ep.progress}%</span>`;
    } else {
      const [c, t] = PILL[ep.status] || ['wait', ep.status.toUpperCase()];
      st = `<span class="pill ${c}">${t}</span>`;
    }
    const a = EP_ACT[ep.status];
    const btn = a ? `<button class="mini ${a[2]}" type="button" data-act="ep" data-id="${ep.id}" data-op="${a[0]}"${a[3] ? ' data-confirm' : ''}>${a[1]}</button>` : '';
    const err = ep.status === 'failed' && ep.error_message ? `<div class="ep-err">${esc(ep.error_message)}</div>` : '';
    return `<span class="ep-n">${epName(ep.number)}</span><span class="ep-st">${st}</span>${btn}${err}`;
  }

  function button(id, op, label, cls, confirm) {
    return `<button class="btn ${cls}" type="button" data-act="anime" data-id="${id}" data-op="${op}"${confirm ? ' data-confirm' : ''}>${label}</button>`;
  }

  function set(el, key, html) {
    if (shown[key] === html) return;
    shown[key] = html;
    el.innerHTML = html;
  }

  function renderAnime(a) {
    const li = document.getElementById('anime-' + a.id);
    if (!li) return;
    const eps = a.episodes;
    const total = eps.length;
    const n = s => eps.filter(e => e.status === s).length;
    const done = n('completed'), failed = n('failed'), waiting = n('pending');
    const dl = eps.filter(e => e.status === 'downloading');
    const pct = total ? Math.floor((done * 100 + dl.reduce((s, e) => s + e.progress, 0)) / total) : 0;

    li.className = `app st-${a.status}${open.has(a.id) ? ' open' : ''}`;
    $('[data-f=led]', li).className = 'led ' + (LED[a.status] || 'off');

    const bits = [`${done}/${total} DONE`];
    if (dl.length) bits.push(`${dl.length} DOWNLOADING`);
    if (waiting) bits.push(`${waiting} WAITING`);
    if (failed) bits.push(`${failed} FAILED`);
    $('[data-f=sub]', li).textContent = bits.join(' · ');

    let badges = '';
    if (dl.length) badges += '<span class="badge dl">DL</span>';
    if (failed) badges += '<span class="badge err">!</span>';
    if (a.language) badges += `<span class="badge lang-${esc(a.language)}">${esc(a.language.toUpperCase())}</span>`;
    set($('[data-f=badges]', li), 'b' + a.id, badges);

    const body = $('.app-body', li);
    body.hidden = !open.has(a.id);
    $('.app-head', li).setAttribute('aria-expanded', String(open.has(a.id)));
    if (body.hidden) return;  // the details are drawn when the row is opened

    const cls = a.status === 'failed' ? 'crit' : pct >= 100 ? 'ok' : 'dl';
    set($('[data-f=meter]', li), 'm' + a.id, `<span class="k">ALL</span>${segs(pct, 20, cls)}<span class="v">${pct}%</span>`);

    let acts = '';
    if (a.status === 'pending' || a.status === 'downloading') {
      acts += button(a.id, 'skip', 'SKIP ALL', 'warm') + button(a.id, 'cancel', 'CANCEL ALL', 'bad', true);
    } else if (['cancelled', 'skipped', 'failed'].includes(a.status)) {
      acts += button(a.id, 'resume', a.status === 'failed' ? 'RETRY ALL' : 'RESUME ALL', 'go');
    }
    acts += `<a class="btn link" href="/anime/${a.id}/">INFO</a>` + button(a.id, 'delete', 'DELETE', 'bad', true);
    set($('[data-f=actions]', li), 'a' + a.id, acts);

    const ul = $('[data-f=eps]', li);
    const ids = eps.map(e => e.id).join(',');
    if (shown['l' + a.id] !== ids) {
      shown['l' + a.id] = ids;
      ul.innerHTML = eps.map(e => `<li class="ep" id="ep-${e.id}"></li>`).join('');
    }
    for (const e of eps) {
      const row = document.getElementById('ep-' + e.id);
      row.className = 'ep st-' + e.status;
      set(row, 'e' + e.id, epHTML(e));
    }
  }

  let last = JSON.parse(document.getElementById('queue-data').textContent);

  function render(data) {
    last = data;
    const ids = new Set(data.animes.map(a => a.id));
    list.querySelectorAll('.app').forEach(li => { if (!ids.has(+li.dataset.id)) li.remove(); });
    data.animes.forEach(renderAnime);
    const all = data.animes.flatMap(a => a.episodes);
    const dl = all.filter(e => e.status === 'downloading').length;
    const left = all.filter(e => e.status === 'pending').length;
    document.getElementById('sub').textContent =
      `${data.animes.length} SHOW${data.animes.length === 1 ? '' : 'S'} · ` +
      (dl || left ? `${dl} DOWNLOADING · ${left} WAITING` : 'ALL DOWNLOADED');
  }

  async function refresh() {
    try {
      const r = await fetch(window.QUEUE_STATUS_URL, { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (err) { /* offline for a moment: keep what's shown */ }
  }

  async function post(url) {
    await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': token } });
  }

  list.addEventListener('click', async e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    if (act === 'toggle') {
      const id = +b.closest('.app').dataset.id;
      open.has(id) ? open.delete(id) : open.add(id);
      sessionStorage.setItem(OPEN_KEY, JSON.stringify([...open]));
      render(last);
      return;
    }
    b.classList.add('working');
    const { id, op } = b.dataset;
    const endpoint = op === 'retry' ? 'resume' : op;
    if (act === 'anime') {
      await post(`/api/anime/${id}/${endpoint}/`);
      if (op === 'delete') {
        document.getElementById('anime-' + id)?.remove();
        open.delete(+id);
      }
    } else if (act === 'ep') {
      await post(`/api/episode/${id}/${endpoint}/`);
    }
    refresh();
  });

  render(last);
  let timer = setInterval(refresh, 3000);
  // No polling while the app is in the background
  document.addEventListener('visibilitychange', () => {
    clearInterval(timer);
    if (!document.hidden) { refresh(); timer = setInterval(refresh, 3000); }
  });
})();
