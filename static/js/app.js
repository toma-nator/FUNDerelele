function refreshPrices(btn) {
  const original = btn.textContent;
  btn.textContent = '↻ Refreshing…';
  btn.disabled = true;

  fetch('/api/refresh-prices', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      btn.textContent = '✓ Refreshed';
      setTimeout(() => location.reload(), 800);
    })
    .catch(() => {
      btn.textContent = '✗ Failed';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = original; }, 2000);
    });
}

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity .4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 5000);
  });
});
