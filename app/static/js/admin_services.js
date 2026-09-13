const svcEls = {
  skeleton: document.getElementById('skeleton'),
  table: document.getElementById('svc-table'),
  tbody: document.getElementById('svc-tbody'),
  empty: document.getElementById('empty'),
  includeInactive: document.getElementById('include-inactive'),
  newBtn: document.getElementById('new-svc-btn'),
  panel: document.getElementById('new-svc-panel'),
  form: document.getElementById('svc-form'),
  cancel: document.getElementById('svc-cancel'),
  banner: document.getElementById('svc-banner'),
  title: document.getElementById('svc-form-title'),
  idField: document.getElementById('svc-id'),
  nameField: document.getElementById('svc-name'),
  durationField: document.getElementById('svc-duration'),
  descField: document.getElementById('svc-desc'),
};

function openForm(svc) {
  svcEls.form.reset();
  if (svc) {
    svcEls.title.textContent = `Edit ${svc.name}`;
    svcEls.idField.value = svc.id;
    svcEls.nameField.value = svc.name;
    svcEls.durationField.value = svc.duration_minutes;
    svcEls.descField.value = svc.description || '';
  } else {
    svcEls.title.textContent = 'Add service';
    svcEls.idField.value = '';
  }
  showBanner(svcEls.banner, null);
  svcEls.panel.hidden = false;
}

svcEls.newBtn.addEventListener('click', () => openForm(null));
svcEls.cancel.addEventListener('click', () => { svcEls.panel.hidden = true; });

async function loadServices() {
  svcEls.skeleton.hidden = false;
  svcEls.table.classList.add('hidden');
  svcEls.empty.classList.add('hidden');
  try {
    const includeInactive = svcEls.includeInactive.checked;
    const services = await Api.get(`/api/v1/scheduling/services?include_inactive=${includeInactive}`);
    svcEls.skeleton.hidden = true;
    if (services.length === 0) {
      svcEls.empty.classList.remove('hidden');
      return;
    }
    svcEls.tbody.innerHTML = services
      .map(
        (s) => `
        <tr data-id="${s.id}">
          <td>${escapeHtml(s.name)}</td>
          <td class="mono">${s.duration_minutes} min</td>
          <td class="muted">${escapeHtml(s.description || '—')}</td>
          <td><span class="stamp ${s.active ? 'stamp-confirmed' : 'stamp-cancelled'}">${s.active ? 'active' : 'inactive'}</span></td>
          <td class="row" style="justify-content:flex-end;">
            <button class="btn btn-small btn-secondary" data-edit="${s.id}">Edit</button>
            ${s.active ? `<button class="btn btn-small btn-danger" data-deactivate="${s.id}">Deactivate</button>` : ''}
          </td>
        </tr>`
      )
      .join('');
    svcEls.table.classList.remove('hidden');
    svcEls.tbody._services = services;
  } catch (err) {
    svcEls.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

svcEls.tbody.addEventListener('click', async (e) => {
  const editBtn = e.target.closest('button[data-edit]');
  const deactivateBtn = e.target.closest('button[data-deactivate]');
  if (editBtn) {
    const svc = (svcEls.tbody._services || []).find((s) => s.id === editBtn.dataset.edit);
    if (svc) openForm(svc);
  }
  if (deactivateBtn) {
    if (!(await confirmDialog('Deactivate this service? It will stop showing on the booking page.', { danger: true }))) return;
    try {
      await Api.del(`/api/v1/scheduling/services/${deactivateBtn.dataset.deactivate}`);
      toast('Service deactivated');
      loadServices();
    } catch (err) {
      toast(err.detail || err.message, 'error');
    }
  }
});

svcEls.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(svcEls.banner, null);
  const id = svcEls.idField.value;
  const body = {
    name: svcEls.nameField.value.trim(),
    duration_minutes: Number(svcEls.durationField.value),
    description: svcEls.descField.value.trim() || null,
  };
  const submitBtn = document.getElementById('svc-submit');
  submitBtn.disabled = true;
  try {
    if (id) {
      await Api.patch(`/api/v1/scheduling/services/${id}`, body);
      toast('Service updated');
    } else {
      await Api.post('/api/v1/scheduling/services', body);
      toast('Service added');
    }
    svcEls.panel.hidden = true;
    loadServices();
  } catch (err) {
    showBanner(svcEls.banner, err.detail || err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

svcEls.includeInactive.addEventListener('change', loadServices);

loadServices();
