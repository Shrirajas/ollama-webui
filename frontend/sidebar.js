// Left sidebar for every page, modelled on Open WebUI's: New Chat, search, the saved chats
// grouped by date (click one to go back to it), page links, and the user menu with Admin Panel
// (admins only) and Sign out. Loaded by auth.js once the user is known (window.me).
(function () {
  if (window.Sidebar || !window.me) return;
  var ME = window.me, KEY = 'fastshot.sidebar', WIDTH = 260;
  var onChatPage = location.pathname === '/' || location.pathname === '/index.html';

  var css = [
    ':root{--sbw:' + WIDTH + 'px}',
    '.sb{position:fixed;left:0;top:0;bottom:0;width:var(--sbw);z-index:60;display:flex;flex-direction:column;background:#0e1116;border-right:1px solid rgba(214,228,255,.10);color:#ECECEF;font:13px/1.45 Inter,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;transform:translateX(-100%);transition:transform .2s ease;visibility:hidden}',
    'html.sb-on .sb{transform:none;visibility:visible}',
    '.sb *{box-sizing:border-box}',
    '.sb button,.sb input{font:inherit;color:inherit;background:none;border:0}',
    '.sb a{color:inherit;text-decoration:none}',
    '.sb :focus-visible,.sb-open:focus-visible{outline:2px solid #F8B285;outline-offset:-2px;border-radius:8px}',
    '.sb-top{display:flex;align-items:center;gap:6px;padding:10px 8px 6px}',
    '.sb-brand{display:flex;align-items:center;gap:8px;flex:1;padding:4px 6px;border-radius:10px;font-size:14px}',
    '.sb-brand svg{width:20px;height:20px;flex:none}',
    '.sb-x,.sb-open{width:30px;height:30px;display:grid;place-items:center;border-radius:8px;cursor:pointer;color:#98999C}',
    '.sb-x:hover,.sb-open:hover,.sb-item:hover,.sb-chat:hover,.sb-user:hover,.sb-pop button:hover,.sb-pop a:hover{background:rgba(255,255,255,.07)}',
    '.sb-scroll{flex:1;overflow-y:auto;padding:4px 6px 8px;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.18) transparent}',
    '.sb-item{display:flex;align-items:center;gap:10px;width:100%;padding:7px 9px;border-radius:10px;cursor:pointer;text-align:left}',
    '.sb-item svg{width:16px;height:16px;flex:none;color:#98999C}',
    '.sb-item[aria-current=page]{background:rgba(255,255,255,.10)}',
    '.sb-search{display:flex;align-items:center;gap:8px;margin:6px 0;padding:6px 9px;border-radius:10px;background:rgba(255,255,255,.05)}',
    '.sb-search svg{width:14px;height:14px;flex:none;color:#77787B}',
    '.sb-search input{flex:1;min-width:0;outline:0}',
    '.sb-search input::placeholder{color:#77787B}',
    '.sb-nav{margin-bottom:8px}',
    '.sb-group{padding:14px 9px 4px;font-size:11.5px;color:#77787B}',
    '.sb-chat{position:relative;display:flex;align-items:center;border-radius:10px;padding:0 4px 0 9px;min-height:32px}',
    '.sb-chat.on{background:rgba(255,255,255,.10)}',
    '.sb-chat a{flex:1;min-width:0;padding:6px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.sb-more{width:26px;height:26px;border-radius:7px;cursor:pointer;color:#98999C;opacity:0;flex:none}',
    '.sb-chat:hover .sb-more,.sb-chat:focus-within .sb-more,.sb-more[aria-expanded=true]{opacity:1}',
    '.sb-more:hover{background:rgba(255,255,255,.10)}',
    '.sb-empty{padding:10px 9px;color:#77787B}',
    '.sb-bottom{padding:6px;border-top:1px solid rgba(214,228,255,.08)}',
    '.sb-user{display:flex;align-items:center;gap:10px;width:100%;padding:7px 8px;border-radius:10px;cursor:pointer;text-align:left}',
    '.sb-av{width:26px;height:26px;border-radius:50%;background:#9C86CE;color:#14121c;display:grid;place-items:center;font-weight:600;font-size:12px;flex:none}',
    '.sb-who{min-width:0;flex:1}.sb-who b{display:block;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.sb-who small{display:block;color:#77787B;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.sb-pop{position:fixed;z-index:70;min-width:170px;padding:5px;border-radius:12px;background:#1b1e25;border:1px solid rgba(214,228,255,.14);box-shadow:0 12px 32px rgba(0,0,0,.45);font:13px Inter,-apple-system,"Segoe UI",Roboto,sans-serif;color:#ECECEF}',
    '.sb-pop button,.sb-pop a{display:block;width:100%;padding:7px 10px;border-radius:8px;cursor:pointer;text-align:left;color:inherit;text-decoration:none;background:none;border:0;font:inherit}',
    '.sb-pop .bad{color:#FBBC94}',
    '.sb-back{position:fixed;inset:0;z-index:55;background:rgba(0,0,0,.5);display:none}',
    '.sb-open{position:fixed;left:8px;top:8px;z-index:50;background:rgba(14,17,22,.7);backdrop-filter:blur(6px)}',
    'html.sb-on .sb-open{display:none}',
    '@media (min-width:900px){html.sb-on body{padding-left:var(--sbw)}html.sb-on .stage{left:var(--sbw)}}',
    '@media (max-width:899px){.sb-open{top:auto;bottom:10px}html.sb-on .sb-back{display:block}}'
  ].join('\n');
  var style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  var ICON = {
    pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    side: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>',
    find: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
    dots: '<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
    logo: '<svg viewBox="0 0 34 34" aria-hidden="true"><circle cx="17" cy="17" r="17" fill="#9C86CE"/><circle cx="17" cy="17" r="8.6" fill="#fff"/><circle cx="17" cy="17" r="3.7" fill="#151519"/></svg>'
  };
  function h(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function txt(tag, cls, t) { var n = h(tag, cls); n.textContent = t; return n; }

  var aside = h('aside', 'sb'); aside.setAttribute('aria-label', 'Sidebar');
  var top = h('div', 'sb-top');
  var brand = h('a', 'sb-brand', ICON.logo + '<span>Fastshot</span>'); brand.href = '/';
  var close = h('button', 'sb-x', ICON.side); close.setAttribute('aria-label', 'Close sidebar');
  top.append(brand, close);

  var scroll = h('div', 'sb-scroll');
  var newChat = h('a', 'sb-item', ICON.pen + '<span>New Chat</span>'); newChat.href = '/';
  var search = h('label', 'sb-search', ICON.find); var q = h('input'); q.placeholder = 'Search chats'; q.setAttribute('aria-label', 'Search chats'); search.append(q);
  var nav = h('nav', 'sb-nav'); nav.setAttribute('aria-label', 'Pages');
  [['Documents', '/documents.html'], ['Word document', '/word.html'], ['Invoices', '/invoices.html'],
    ['Workspaces', '/workspaces.html'], ['Databases', '/databases.html']].forEach(function (p) {
    var a = h('a', 'sb-item', ICON.doc + '<span></span>'); a.lastChild.textContent = p[0]; a.href = p[1];
    if (location.pathname === p[1]) a.setAttribute('aria-current', 'page');
    nav.append(a);
  });
  var list = h('div', 'sb-chats');
  scroll.append(newChat, search, nav, list);

  var bottom = h('div', 'sb-bottom');
  var user = h('button', 'sb-user'); user.setAttribute('aria-haspopup', 'menu');
  user.append(txt('span', 'sb-av', (ME.name || ME.email || '?').trim().charAt(0).toUpperCase()));
  var who = h('span', 'sb-who'); who.append(txt('b', '', ME.name), txt('small', '', ME.role === 'admin' ? 'Administrator' : ME.email)); user.append(who);
  bottom.append(user);

  var back = h('div', 'sb-back'), open = h('button', 'sb-open', ICON.side); open.setAttribute('aria-label', 'Open sidebar');
  aside.append(top, scroll, bottom);
  document.body.append(aside, back, open);

  // ── open / closed (remembered; closed on phones) ──
  var root = document.documentElement;
  function setOpen(on, remember) {
    root.classList.toggle('sb-on', on);
    if (remember !== false) try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) {}
  }
  var saved = null; try { saved = localStorage.getItem(KEY); } catch (e) {}
  setOpen(saved === null ? innerWidth >= 900 : saved === '1', false);
  close.onclick = function () { setOpen(false); };
  open.onclick = function () { setOpen(true); };
  back.onclick = function () { setOpen(false); };

  // ── popup menus (chat ⋯ and user) ──
  var pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } document.querySelectorAll('.sb-more[aria-expanded=true]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); }); }
  function openPop(anchor, items, above) {
    closePop();
    pop = h('div', 'sb-pop'); pop.setAttribute('role', 'menu');
    items.forEach(function (it) {
      var n = it.href ? h('a') : h('button'); n.textContent = it.label; if (it.href) n.href = it.href; if (it.bad) n.className = 'bad';
      n.setAttribute('role', 'menuitem');
      if (it.run) n.onclick = function (e) { e.stopPropagation(); closePop(); it.run(); };
      pop.append(n);
    });
    document.body.append(pop);
    var r = anchor.getBoundingClientRect(), w = pop.offsetWidth, ph = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    pop.style.top = (above ? Math.max(8, r.top - ph - 6) : Math.min(r.bottom + 4, innerHeight - ph - 8)) + 'px';
    pop.querySelector('a,button').focus();
  }
  document.addEventListener('click', function (e) { if (pop && !pop.contains(e.target)) closePop(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePop(); });

  var items = [];
  if (ME.role === 'admin') items.push({ label: 'Admin Panel', href: '/admin.html' });
  items.push({ label: 'Memory', run: memories });
  items.push({ label: 'Change password', run: changePassword });
  items.push({ label: 'Sign out', run: function () { window.signOut(); } });

  // A small dialog: current password, new one twice. The server ends the user's other sessions and keeps this one.
  function changePassword() {
    var d = document.createElement('dialog');
    d.style.cssText = 'margin:auto;background:#1f232b;color:#ECECEF;border:1px solid rgba(214,228,255,.14);border-radius:14px;padding:18px;width:min(340px,92vw);font:14px Inter,sans-serif';
    d.innerHTML = '<form method="dialog" style="display:grid;gap:10px"><b>Change password</b>' +
      ['Current password', 'New password (8+ characters)', 'New password again'].map(function (t, i) {
        return '<label style="display:grid;gap:4px;font-size:12.5px;color:#98999C">' + t + '<input type="password" required autocomplete="' + (i ? 'new' : 'current') + '-password" style="padding:8px 10px;border-radius:9px;background:#0a0d12;border:1px solid rgba(214,228,255,.14);color:#ECECEF;font:inherit"></label>';
      }).join('') + '<div class="msg" style="min-height:18px;font-size:12.5px;color:#FBBC94"></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="x" style="padding:7px 14px;border-radius:9px;background:rgba(255,255,255,.06);color:inherit">Cancel</button><button class="ok" style="padding:7px 14px;border-radius:9px;background:#ECECEF;color:#0a0d12;font-weight:500">Save</button></div></form>';
    var f = d.querySelector('form'), inp = d.querySelectorAll('input'), msg = d.querySelector('.msg');
    d.querySelector('.x').onclick = function () { d.close(); };
    d.onclose = function () { d.remove(); };
    f.onsubmit = function (e) {
      e.preventDefault();
      if (inp[1].value !== inp[2].value) { msg.textContent = 'The two new passwords differ.'; return; }
      fetch('/api/auth/password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current: inp[0].value, new: inp[1].value }) })
        .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
        .then(function (a) {
          if (!a[0]) { msg.textContent = typeof a[1].detail === 'string' ? a[1].detail : 'Could not change the password.'; return; }
          msg.style.color = '#8FD3A8'; msg.textContent = 'Password changed.'; setTimeout(function () { d.close(); }, 1200);
        }).catch(function () { msg.textContent = 'Could not reach the server.'; });
    };
    document.body.append(d);
    d.showModal();
  }
  // What the chat model remembers about this user (/api/memories, their own only): list, add, edit in place, delete.
  function memories() {
    var FIELD = 'flex:1;min-width:0;padding:8px 10px;border-radius:9px;background:#0a0d12;border:1px solid rgba(214,228,255,.14);color:#ECECEF;font:inherit';
    var BTN = 'padding:7px 12px;border-radius:9px;background:rgba(255,255,255,.06);color:inherit;cursor:pointer';
    var d = document.createElement('dialog');
    d.style.cssText = 'margin:auto;background:#1f232b;color:#ECECEF;border:1px solid rgba(214,228,255,.14);border-radius:14px;padding:18px;width:min(460px,92vw);font:14px Inter,sans-serif';
    d.innerHTML = '<div style="display:grid;gap:10px"><b>Memory</b>' +
      '<div class="off" style="font-size:12.5px;color:#FBBC94" hidden>An admin turned Memory off: these are kept, but the chat does not use them.</div>' +
      '<div class="list" style="display:grid;gap:6px;max-height:50vh;overflow-y:auto"></div>' +
      '<form style="display:flex;gap:6px"><input maxlength="500" placeholder="Something the chat should remember about you" aria-label="New memory" required style="' + FIELD + '"><button style="' + BTN + ';background:#ECECEF;color:#0a0d12;font-weight:500">Add</button></form>' +
      '<div class="msg" style="min-height:18px;font-size:12.5px;color:#FBBC94"></div>' +
      '<div style="display:flex;justify-content:flex-end"><button type="button" class="x" style="' + BTN + '">Close</button></div></div>';
    var list = d.querySelector('.list'), msg = d.querySelector('.msg'), f = d.querySelector('form'), add = f.querySelector('input');
    function call(path, body) {
      return fetch('/api/memories' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Could not save.'); return j; }); });
    }
    function fail(e) { msg.textContent = e instanceof TypeError ? 'Could not reach the server.' : e.message; }
    function load() {
      call('').then(function (j) {
        d.querySelector('.off').hidden = j.on !== false;
        list.replaceChildren();
        if (!j.memories.length) list.append(txt('div', '', 'Nothing yet. Add a fact, or tell the chat "remember that …".'));
        j.memories.forEach(function (m) {
          var row = h('div'); row.style.cssText = 'display:flex;gap:6px';
          var t = h('input'); t.value = m.text; t.maxLength = 500; t.setAttribute('aria-label', 'Memory'); t.style.cssText = FIELD;
          t.onchange = function () {
            msg.textContent = '';
            if (!t.value.trim()) { t.value = m.text; return; }
            call('/' + encodeURIComponent(m.id), { text: t.value }).then(function (s) { m.text = t.value = s.text; }).catch(function (e) { t.value = m.text; fail(e); });
          };
          var del = txt('button', '', 'Delete'); del.type = 'button'; del.style.cssText = BTN + ';color:#FBBC94';
          del.onclick = function () { msg.textContent = ''; call('/' + encodeURIComponent(m.id) + '/delete', {}).then(load).catch(fail); };
          row.append(t, del); list.append(row);
        });
      }).catch(fail);
    }
    f.onsubmit = function (e) {
      e.preventDefault(); msg.textContent = '';
      call('', { text: add.value }).then(function () { add.value = ''; load(); }).catch(fail);
    };
    d.querySelector('.x').onclick = function () { d.close(); };
    d.onclose = function () { d.remove(); };
    document.body.append(d);
    d.showModal();
    load();
  }
  user.onclick = function (e) { e.stopPropagation(); pop ? closePop() : openPop(user, items, true); };

  // ── chat history ──
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function range(ts) {
    var now = new Date(), d = new Date(ts * 1000), day = 86400000;
    var sod = function (x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(); };
    var diff = Math.round((sod(now) - sod(d)) / day);
    if (diff <= 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff <= 7) return 'Previous 7 days';
    if (diff <= 30) return 'Previous 30 days';
    return now.getFullYear() === d.getFullYear() ? MONTHS[d.getMonth()] : String(d.getFullYear());
  }
  function activeId() { return onChatPage ? new URLSearchParams(location.search).get('c') : null; }
  var timer = 0, lastQuery = null;
  function api(path, method, body) {
    return fetch('/api/chats' + path, { method: method || 'GET', headers: { 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) })
      .then(function (r) { if (r.status === 401) { location.replace('/login.html'); throw 0; } if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  function refresh() {
    var query = q.value.trim(); lastQuery = query;
    api('?q=' + encodeURIComponent(query)).then(function (chats) {
      if (query !== lastQuery) return;   // a newer search is already on its way
      list.replaceChildren();
      if (!chats.length) { list.append(txt('div', 'sb-empty', query ? 'No chats match.' : 'No chats yet. Send a message and it shows up here.')); return; }
      var cur = activeId(), prev = null;
      chats.forEach(function (c) {
        var g = range(c.updated_at);
        if (g !== prev) { list.append(txt('div', 'sb-group', g)); prev = g; }
        var row = h('div', 'sb-chat' + (c.id === cur ? ' on' : ''));
        var a = txt('a', '', c.title); a.href = '/?c=' + encodeURIComponent(c.id); a.title = c.title;
        if (c.id === cur) a.setAttribute('aria-current', 'page');
        a.onclick = function (e) {
          if (onChatPage && window.Chat) {
            e.preventDefault(); history.pushState(null, '', a.getAttribute('href')); window.Chat.open(c.id); refresh();
          }
          if (innerWidth < 900) setOpen(false, false);
        };
        var more = h('button', 'sb-more', ICON.dots); more.setAttribute('aria-label', 'Chat options'); more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', 'false');
        more.onclick = function (e) {
          e.stopPropagation(); more.setAttribute('aria-expanded', 'true');
          openPop(more, [
            { label: 'Rename', run: function () { var t = prompt('Rename chat', c.title); if (t && t.trim()) api('/' + c.id, 'POST', { title: t.trim() }).then(refresh); } },
            { label: 'Delete', bad: true, run: function () {
              if (!confirm('Delete this chat?')) return;
              api('/' + c.id + '/delete', 'POST').then(function () {
                if (c.id === activeId() && window.Chat) { history.pushState(null, '', '/'); window.Chat.reset(); }
                refresh();
              });
            } }
          ]);
        };
        row.append(a, more); list.append(row);
      });
    }).catch(function () {});
  }
  q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(refresh, 200); });
  newChat.onclick = function (e) {
    if (onChatPage && window.Chat) { e.preventDefault(); history.pushState(null, '', '/'); window.Chat.reset(); refresh(); }
    if (innerWidth < 900) setOpen(false, false);
  };
  brand.onclick = newChat.onclick;
  window.addEventListener('chats:changed', refresh);
  window.addEventListener('popstate', refresh);
  window.Sidebar = { refresh: refresh };
  refresh();
})();
