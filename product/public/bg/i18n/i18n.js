/* Vitrina i18n loader (v13)
 *
 * Single dictionary at /i18n/strings.json keyed by `<page>.<key>` with
 * { en, bg } values. Default language is Bulgarian.
 *
 * Resolution order: ?lang=… → localStorage.vitrina_lang → 'bg'
 *
 * Bindings:
 *   <el data-i18n="key">…</el>          → innerHTML = strings[key][lang]
 *   <el data-i18n-attr="placeholder:waitlist.phone.ph,aria-label:home.cta.aria">
 *                                       → element[attr] = strings[key][lang]
 *   <title data-i18n-title="key">       → document.title = strings[key][lang]
 *
 * The active language is exposed as `window.__vitrinaLang`. Callers can
 * read `window.__vitrinaT(key)` for click-time lookups (WhatsApp body etc.).
 */
(function () {
  'use strict';
  const STORAGE_KEY = 'atelier_vitrina_lang';
  const DEFAULT_LANG = 'bg';
  const SUPPORTED = ['bg', 'en'];

  function detectLang() {
    try {
      const url = new URL(window.location.href);
      const q = (url.searchParams.get('lang') || '').toLowerCase();
      if (SUPPORTED.includes(q)) return q;
    } catch (e) {}
    try {
      const stored = (localStorage.getItem(STORAGE_KEY) || '').toLowerCase();
      if (SUPPORTED.includes(stored)) return stored;
    } catch (e) {}
    return DEFAULT_LANG;
  }

  let DICT = {};
  let LANG = detectLang();
  window.__vitrinaLang = LANG;
  window.__vitrinaT = function (key, fallback) {
    const row = DICT[key];
    if (row && (row[LANG] || row.en)) return row[LANG] || row.en;
    if (typeof fallback === 'string') return fallback;
    return key;
  };
  // Returns the value of `key` in EVERY supported language (lowercased,
  // string-only, deduped). Used by the waitlist quiz to filter
  // community-contributed chips against preset labels in all languages
  // — so a BG user doesn't see the EN preset re-suggested as a
  // "community" chip just because some other signup happened to be on
  // the EN flow.
  window.__vitrinaTAll = function (key) {
    const row = DICT[key];
    if (!row) return [];
    const out = new Set();
    SUPPORTED.forEach(lang => {
      const v = row[lang];
      if (typeof v === 'string' && v.trim()) out.add(v.trim().toLowerCase());
    });
    return [...out];
  };

  function applyAll() {
    document.documentElement.setAttribute('lang', LANG);
    try { localStorage.setItem(STORAGE_KEY, LANG); } catch (e) {}
    window.__vitrinaLang = LANG;

    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const row = DICT[key];
      if (!row) {
        if (window.__vitrinaI18nDebug) console.warn('[i18n] missing key:', key);
        return;
      }
      const v = row[LANG] || row.en;
      if (typeof v === 'string') el.innerHTML = v;
    });

    document.querySelectorAll('[data-i18n-attr]').forEach(el => {
      const spec = el.getAttribute('data-i18n-attr') || '';
      spec.split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
        const [attr, key] = pair.split(':').map(s => s && s.trim());
        if (!attr || !key) return;
        const row = DICT[key];
        if (!row) return;
        const v = row[LANG] || row.en;
        if (typeof v === 'string') el.setAttribute(attr, v);
      });
    });

    const titleEl = document.querySelector('title[data-i18n-title]');
    if (titleEl) {
      const row = DICT[titleEl.getAttribute('data-i18n-title')];
      if (row && (row[LANG] || row.en)) document.title = row[LANG] || row.en;
    }

    document.querySelectorAll('.lang-pill[data-lang]').forEach(p => {
      const isActive = p.getAttribute('data-lang') === LANG;
      p.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });

    // Notify consumers (e.g. waitlist chip renderer, position card) that the
    // dictionary is ready and the DOM has been swapped. Fires on both initial
    // load and on every user-driven setLang() call.
    window.dispatchEvent(new CustomEvent('vitrina:langchange', { detail: { lang: LANG } }));
  }

  function setLang(lang) {
    if (!SUPPORTED.includes(lang) || lang === LANG) return;
    LANG = lang;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('lang', LANG);
      history.replaceState(null, '', url.toString());
    } catch (e) {}
    // applyAll() emits the vitrina:langchange event itself.
    applyAll();
  }
  window.__vitrinaSetLang = setLang;

  function wireToggles() {
    document.querySelectorAll('.lang-pill[data-lang]').forEach(p => {
      p.addEventListener('click', () => setLang(p.getAttribute('data-lang')));
    });
  }

  fetch('/i18n/strings.json', { cache: 'no-cache' })
    .then(r => r.ok ? r.json() : {})
    .then(j => { DICT = j || {}; })
    .catch(() => { DICT = {}; })
    .finally(() => {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { wireToggles(); applyAll(); });
      } else {
        wireToggles();
        applyAll();
      }
    });
})();
