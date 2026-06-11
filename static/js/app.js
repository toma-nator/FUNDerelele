// Sidebar collapse — persisted across pages via <html> class set early in <head>
function toggleSidebar() {
  const collapsed = document.documentElement.classList.toggle('sidebar-collapsed');
  localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0');
}

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

// ── Sidebar customize: drag-to-reorder + hide tabs, persisted server-side ──
(function () {
  const nav = document.getElementById('sidebarNav');
  if (!nav) return;
  let dragEl = null;

  function persist() {
    const sections = {}, hidden = [];
    nav.querySelectorAll('.nav-section').forEach(sec => {
      sections[sec.dataset.section] = [...sec.querySelectorAll('.nav-item')].map(it => {
        if (it.classList.contains('nav-hidden')) hidden.push(it.dataset.tab);
        return it.dataset.tab;
      });
    });
    fetch('/sidebar-layout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sections, hidden })
    }).catch(() => {});
  }

  // The item the cursor is currently above within a section (or null = drop at end).
  function insertBeforeFor(section, y) {
    const items = [...section.querySelectorAll('.nav-item:not(.dragging)')];
    let best = { offset: -Infinity, el: null };
    for (const child of items) {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > best.offset) best = { offset, el: child };
    }
    return best.el;
  }

  // In customize mode a tab click must not navigate — the handle drags, the eye hides.
  nav.addEventListener('click', (e) => {
    if (!document.body.classList.contains('customizing')) return;
    const item = e.target.closest('.nav-item');
    if (!item) return;
    e.preventDefault();
    if (e.target.closest('.nav-hide')) {
      const nowHidden = item.classList.toggle('nav-hidden');   // CSS turns the ⊘ red when hidden
      const eye = item.querySelector('.nav-hide');
      if (eye) eye.title = nowHidden ? 'Tab hidden — click to show' : 'Hide this tab';
      persist();
    }
  });

  nav.addEventListener('dragstart', (e) => {
    if (!document.body.classList.contains('customizing')) return;
    const item = e.target.closest('.nav-item');
    if (!item || item.classList.contains('nav-pinned')) return;   // Settings is pinned
    dragEl = item;
    item.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  nav.addEventListener('dragover', (e) => {
    if (!dragEl) return;
    e.preventDefault();
    const section = e.target.closest('.nav-section');
    if (!section) return;
    nav.querySelectorAll('.drop-target').forEach(s => s.classList.remove('drop-target'));
    section.classList.add('drop-target');
    const before = insertBeforeFor(section, e.clientY);
    if (before) section.insertBefore(dragEl, before);
    else section.appendChild(dragEl);
  });

  nav.addEventListener('dragend', () => {
    if (!dragEl) return;
    dragEl.classList.remove('dragging');
    nav.querySelectorAll('.drop-target').forEach(s => s.classList.remove('drop-target'));
    dragEl = null;
    persist();
  });

  window.toggleCustomize = function () {
    const on = document.body.classList.toggle('customizing');
    nav.querySelectorAll('.nav-item:not(.nav-pinned)').forEach(it => it.setAttribute('draggable', on ? 'true' : 'false'));
    const gear = document.getElementById('navCustomizeBtn');
    if (gear) {
      gear.textContent = on ? '✓' : '⚙';
      gear.title = on ? 'Done customizing' : 'Customize sidebar — reorder & hide tabs';
    }
  };
})();
