const els = {
  label: document.getElementById('cal-month-label'),
  grid: document.getElementById('cal-grid'),
  skeleton: document.getElementById('cal-skeleton'),
  prev: document.getElementById('cal-prev'),
  next: document.getElementById('cal-next'),
};

const today = new Date();
let viewYear = today.getFullYear();
let viewMonth = today.getMonth() + 1; // 1-12, matches the API's `month` param

function pad(n) {
  return String(n).padStart(2, '0');
}

function dateKey(y, m, d) {
  return `${y}-${pad(m)}-${pad(d)}`;
}

async function loadMonth() {
  els.skeleton.hidden = false;
  els.grid.classList.add('hidden');
  els.label.textContent = new Date(viewYear, viewMonth - 1, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  });

  try {
    const data = await Api.get(`/api/v1/appointments/calendar-summary?year=${viewYear}&month=${viewMonth}`);
    renderGrid(data.counts);
  } catch (err) {
    els.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

function renderGrid(counts) {
  const firstOfMonth = new Date(viewYear, viewMonth - 1, 1);
  const daysInMonth = new Date(viewYear, viewMonth, 0).getDate();
  const startWeekday = firstOfMonth.getDay(); // 0 = Sunday
  const todayKey = dateKey(today.getFullYear(), today.getMonth() + 1, today.getDate());

  const cells = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(
    (w) => `<div class="month-weekday">${w}</div>`
  );

  for (let i = 0; i < startWeekday; i++) {
    cells.push('<div class="month-day month-day-empty"></div>');
  }

  for (let d = 1; d <= daysInMonth; d++) {
    const key = dateKey(viewYear, viewMonth, d);
    const count = counts[key] || 0;
    const classes = ['month-day'];
    if (key === todayKey) classes.push('is-today');
    if (!count) classes.push('is-empty');
    cells.push(`
      <a class="${classes.join(' ')}" href="/admin?date=${key}" title="${count} active appointment${count === 1 ? '' : 's'}">
        <span class="month-day-num">${d}</span>
        ${count ? `<span class="stamp stamp-confirmed month-day-count">${count}</span>` : ''}
      </a>
    `);
  }

  els.grid.innerHTML = cells.join('');
  els.skeleton.hidden = true;
  els.grid.classList.remove('hidden');
}

els.prev.addEventListener('click', () => {
  viewMonth -= 1;
  if (viewMonth < 1) { viewMonth = 12; viewYear -= 1; }
  loadMonth();
});
els.next.addEventListener('click', () => {
  viewMonth += 1;
  if (viewMonth > 12) { viewMonth = 1; viewYear += 1; }
  loadMonth();
});

loadMonth();
