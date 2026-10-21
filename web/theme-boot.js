// Runs before first paint so the page never flashes the wrong theme.
// /theme-config.js (from hub.theme in devices.yml) names the cookie to read;
// pointing it at a cookie shared across your domain lets another site's
// theme pick carry over. localStorage is the per-site fallback.
(function () {
  var THEMES = ['dark', 'light', 'sepia', 'cyberpunk', 'matrix', 'ocean'];
  var cfg = window.DC_THEME || { cookie: 'dc-theme', storage: 'dc-theme' };
  var m = document.cookie.match(new RegExp('(?:^|; )' + cfg.cookie.replace(/[^\w-]/g, '') + '=([^;]+)'));
  var t = m && m[1];
  if (THEMES.indexOf(t) < 0) {
    try { t = localStorage.getItem(cfg.storage); } catch (e) { t = null; }
  }
  if (THEMES.indexOf(t) < 0) t = THEMES.indexOf(cfg['default']) < 0 ? 'dark' : cfg['default'];
  document.documentElement.setAttribute('data-theme', t);
})();
