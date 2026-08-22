(function () {
  var root = document.documentElement;
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;

  function isDark() {
    return root.getAttribute('data-theme') === 'dark';
  }

  function label() {
    btn.textContent = isDark() ? 'Light' : 'Dark';
    btn.setAttribute('aria-pressed', String(isDark()));
  }

  label();

  btn.addEventListener('click', function () {
    var dark = !isDark();
    if (dark) root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');
    try {
      localStorage.setItem('fm-theme', dark ? 'dark' : 'light');
    } catch (e) {}
    label();
  });
})();
