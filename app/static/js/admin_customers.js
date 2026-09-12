const state = { search: '', sort: 'visits', page: 1, pageSize: 25 };

const els = {
  skeleton: document.getElementById('skeleton'),
  table: document.getElementById('cust-table'),
  tbody: document.getElementById('cust-tbody'),
  empty: document.getElementById('empty'),
  pagination: document.getElementById('pagination'),
  search: document.getElementById('cust-search'),
  sort: document.getElementById('cust-sort'),
};

function buildQuery() {
  const params = new URLSearchParams({ page: state.page, page_size: state.pageSize, sort: state.sort });
  if (state.search) params.set('search', state.search);
  return params.toString();
}

function renderRow(c) {
  const tz = Api.getTenantTimezone();
  const { date: customerSince } = fmtDateTime(c.first_visit_at, tz);
  const { date: lastVisit } = fmtDateTime(c.last_visit_at, tz);
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${escapeHtml(c.name)}</td>
    <td><a class="quiet mono" href="/admin?phone=${encodeURIComponent(c.phone)}">${escapeHtml(c.phone)}</a></td>
    <td>${c.total_appointments}</td>
    <td>${c.completed_appointments}</td>
    <td>${customerSince}</td>
    <td>${lastVisit}</td>
  `;
  return tr;
}

function renderPagination(meta) {
  els.pagination.innerHTML = '';
  if (meta.pages <= 1) return;
  const mkBtn = (label, page, disabled) => {
    const b = document.createElement('button');
    b.className = 'btn btn-secondary btn-small';
    b.textContent = label;
    b.disabled = disabled;
    b.addEventListener('click', () => { state.page = page; load(); });
    return b;
  };
  els.pagination.appendChild(mkBtn('Prev', meta.page - 1, meta.page <= 1));
  const span = document.createElement('span');
  span.className = 'muted mono';
  span.style.padding = '0 0.5rem';
  span.textContent = `Page ${meta.page} of ${meta.pages}`;
  els.pagination.appendChild(span);
  els.pagination.appendChild(mkBtn('Next', meta.page + 1, meta.page >= meta.pages));
}

async function load() {
  els.skeleton.hidden = false;
  els.table.classList.add('hidden');
  els.empty.hidden = true;

  try {
    const data = await Api.get(`/api/v1/customers?${buildQuery()}`);
    els.skeleton.hidden = true;
    if (data.items.length === 0) {
      els.empty.hidden = false;
      els.pagination.innerHTML = '';
      return;
    }
    els.tbody.innerHTML = '';
    data.items.forEach((c) => els.tbody.appendChild(renderRow(c)));
    els.table.classList.remove('hidden');
    renderPagination(data);
  } catch (err) {
    els.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

let searchDebounce;
els.search.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.search = els.search.value.trim();
    state.page = 1;
    load();
  }, 300);
});

els.sort.addEventListener('change', () => {
  state.sort = els.sort.value;
  state.page = 1;
  load();
});

load();
