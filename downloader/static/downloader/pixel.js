/* Small helpers shared by every page. */
(() => {
  'use strict';

  // Buttons with data-confirm need two taps: the first arms them ("SURE?"), the second acts
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-confirm]');
    if (!b || b.classList.contains('armed')) {
      if (b) disarm(b);
      return;
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    b.dataset.label = b.textContent;
    b.textContent = 'SURE?';
    b.classList.add('armed');
    b.armTimer = setTimeout(() => disarm(b, true), 3000);
  }, true);

  function disarm(b, restore) {
    clearTimeout(b.armTimer);
    b.classList.remove('armed');
    if (restore && b.dataset.label) b.textContent = b.dataset.label;
  }

  // A form that takes a while (adding a show reads AnimeUnity) blinks its button and
  // ignores more taps until the page changes
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.dataset.sent) { e.preventDefault(); return; }
    f.dataset.sent = '1';
    const b = e.submitter || f.querySelector('button');
    if (b) b.classList.add('working');
  });
  // Coming back with the browser's back button shows the cached page: make it usable again
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('form[data-sent]').forEach(f => delete f.dataset.sent);
    document.querySelectorAll('.working').forEach(b => b.classList.remove('working'));
  });

  // Covers that fail to load leave the striped placeholder
  document.addEventListener('error', e => {
    if (e.target.tagName === 'IMG' && e.target.closest('.cover')) e.target.remove();
  }, true);
})();
