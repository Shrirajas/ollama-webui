// Login guard, loaded first in every page's <head>. The page stays hidden until /api/auth/me
// says who is signed in; without a login it goes to login.html and comes back afterwards.
// Once signed in it adds the sidebar (sidebar.js: chats, page links, user menu, Admin Panel).
// Pages can use: await authReady -> the user {id,email,name,role,...}; window.me; signOut().
(function () {
  var root = document.documentElement;
  root.style.visibility = 'hidden';
  function addSidebar() {
    var s = document.createElement('script');
    s.src = '/sidebar.js';
    document.body.appendChild(s);
  }
  function whenReady(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn();
  }
  window.authReady = fetch('/api/auth/me')
    .then(function (r) {
      if (r.status === 401) {
        location.replace('/login.html?next=' + encodeURIComponent(location.pathname + location.search));
        return new Promise(function () {});
      }
      return r.json();
    })
    .then(function (u) { window.me = u; root.style.visibility = ''; whenReady(addSidebar); return u; })
    .catch(function () { root.style.visibility = ''; });  // server unreachable: let the page show its own error
  window.signOut = function () {
    return fetch('/api/auth/signout', { method: 'POST' }).then(function () { location.replace('/login.html'); });
  };
})();
