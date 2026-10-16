/* Live behaviour: chat, payment status, dashboard refresh, search, location. Pages work without it. */
(function () {
  const csrf = document.querySelector('meta[name="csrf"]')?.content || '';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const icons = () => window.lucide && window.lucide.createIcons({ attrs: { class: 'ic' } });
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const getJSON = async url => { const r = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' }); if (!r.ok) throw new Error(r.status); return r.json(); };
  const hidden = () => document.visibilityState === 'hidden';
  icons();

  /* ---------- chat ---------- */
  $$('[data-chat]').forEach(box => {
    const url = box.dataset.chat, me = box.dataset.me, list = $('.msgs', box), form = $('form', box), input = $('input', form);
    let last = +box.dataset.last || 0, busy = false;
    const fmt = iso => new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Africa/Nairobi' });
    const add = m => {
      if (m.id <= last) return; last = m.id;
      $('.none', list)?.remove();
      const d = document.createElement('div');
      d.className = 'bubble' + (m.sender === me ? ' me' : '');
      d.innerHTML = `<span class="who">${esc(m.name)} · ${esc(fmt(m.when))}</span>${esc(m.text)}`;
      list.appendChild(d);
    };
    const scroll = () => { list.scrollTop = list.scrollHeight; };
    scroll();
    const poll = async () => {
      if (busy || hidden()) return; busy = true;
      try { const rows = await getJSON(`${url}?after=${last}`); const near = list.scrollHeight - list.scrollTop - list.clientHeight < 80; rows.forEach(add); if (rows.length && near) scroll(); }
      catch (e) { /* offline for a moment; try again next tick */ }
      busy = false;
    };
    setInterval(poll, 3000);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const text = input.value.trim(); if (!text) return;
      input.value = ''; input.focus();
      try {
        const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ text }) });
        if (!r.ok) throw new Error(r.status);
        add(await r.json()); scroll();
      } catch (err) { input.value = text; alert('Message not sent. Check your connection and try again.'); }
    });
  });

  /* ---------- ticket status (payment prompt, negotiation answers) ---------- */
  const watch = $('[data-ticket-state]');
  if (watch) {
    let stamp = watch.dataset.stamp;
    setInterval(async () => {
      if (hidden()) return;
      try { const s = await getJSON(watch.dataset.ticketState); if (s.stamp !== stamp) location.reload(); } catch (e) {}
    }, +watch.dataset.every || 3000);
  }

  /* ---------- dashboards: tell people when something new arrived ---------- */
  if (document.body.dataset.pulse) {
    let base = null, shown = false;
    const tick = async () => {
      if (hidden() || shown) return;
      try {
        const { stamp } = await getJSON('/api/pulse');
        if (base === null) { base = stamp; return; }
        if (stamp !== base) {
          shown = true;
          const busyForm = $$('input[type=text],textarea').some(i => i.value && i.closest('form:not([data-chat] form)'));
          if (!busyForm && !document.activeElement?.matches('input,textarea,select')) { location.reload(); return; }
          const bar = document.createElement('div');
          bar.className = 'livebar'; bar.setAttribute('role', 'status');
          bar.innerHTML = '<span class="pulse-dot"></span>New activity <button class="btn sm" type="button">Refresh</button>';
          $('button', bar).onclick = () => location.reload();
          document.body.appendChild(bar);
        }
      } catch (e) {}
    };
    tick(); setInterval(tick, 6000);
  }

  /* ---------- search as you type ---------- */
  $$('[data-search]').forEach(input => {
    const out = document.getElementById(input.dataset.search);
    let t;
    input.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = input.value.trim();
        if (q.length < 2) { out.innerHTML = ''; return; }
        try {
          const hits = await getJSON('/api/search?q=' + encodeURIComponent(q));
          out.innerHTML = hits.length ? hits.map(h => `<a class="hit" href="/tukusaidie/services/${h.cat}?open=${h.idx}#i${h.idx}"><span class="ico"><i data-lucide="${esc(h.icon)}"></i></span><span class="grow"><b>${esc(h.title)}</b><span class="meta">${esc(h.cat_name)}</span></span></a>`).join('')
            : '<p class="small muted">No match. Pick a service below or try another word.</p>';
          icons();
        } catch (e) {}
      }, 180);
    });
  });

  /* ---------- location ---------- */
  $$('[data-geo]').forEach(btn => {
    btn.addEventListener('click', () => {
      const form = btn.closest('form'), msg = document.getElementById(btn.dataset.geo);
      const say = t => { if (msg) msg.textContent = t; };
      if (!navigator.geolocation) return say('This device cannot share its location. Type your town or building instead.');
      say('Finding you…');
      navigator.geolocation.getCurrentPosition(p => {
        const { latitude, longitude, accuracy } = p.coords;
        if (btn.dataset.go) { location.href = `${btn.dataset.go}?lat=${latitude.toFixed(5)}&lng=${longitude.toFixed(5)}`; return; }
        form.querySelector('[name=lat]').value = latitude.toFixed(5);
        form.querySelector('[name=lng]').value = longitude.toFixed(5);
        const acc = form.querySelector('[name=acc]'); if (acc) acc.value = Math.round(accuracy);
        say(`Location saved, accurate to about ${Math.round(accuracy)} m.`);
      }, err => say(err.code === 1 ? 'Location permission was refused. Type your town or building instead.' : 'Could not get your location. Type your town or building instead.'),
      { enableHighAccuracy: true, timeout: 15000 });
    });
  });

  /* ---------- small helpers ---------- */
  $$('form[data-confirm]').forEach(f => f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
  $$('form').forEach(f => f.addEventListener('submit', () => {
    if (f.closest('[data-chat]') || f.dataset.confirm && !f.dataset.ok) return;
    setTimeout(() => $$('button[type=submit],button:not([type])', f).forEach(b => { b.disabled = true; }), 0);
  }));
  $$('[data-pay]').forEach(group => {
    const show = () => { const v = $('input[name=method]:checked', group)?.value; $$('[data-pane]', group.closest('form')).forEach(p => { p.hidden = p.dataset.pane !== v; }); };
    group.addEventListener('change', show); show();
  });
  $$('details[data-count]').forEach(d => {
    const out = $('.count', d), upd = () => { const n = $$('input:checked', d).length; out.textContent = n ? `${n} selected` : ''; };
    d.addEventListener('change', upd); upd();
  });
  document.querySelector('.issue[open]')?.scrollIntoView({ block: 'start' });
})();
