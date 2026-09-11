const els = {
  skeleton: document.getElementById('list-skeleton'),
  ledger: document.getElementById('ledger'),
  empty: document.getElementById('empty'),
  filterForm: document.getElementById('filter-form'),
  newBtn: document.getElementById('new-appt-btn'),
  newPanel: document.getElementById('new-appt-panel'),
  newForm: document.getElementById('new-appt-form'),
  newCancel: document.getElementById('na-cancel'),
  newBanner: document.getElementById('new-appt-banner'),
  serviceSelect: document.getElementById('na-service'),
  durationInput: document.getElementById('na-duration'),
};

const NEXT_ACTIONS = {
  PENDING: [
    { to: 'CONFIRMED', label: 'Confirm', cls: 'btn' },
    { to: 'CANCELLED', label: 'Cancel', cls: 'btn-danger' },
  ],
  CONFIRMED: [
    { to: 'CHECKED_IN', label: 'Check in', cls: 'btn' },
    { to: 'NO_SHOW', label: 'No-show', cls: 'btn-secondary' },
    { to: 'CANCELLED', label: 'Cancel', cls: 'btn-danger' },
  ],
  CHECKED_IN: [
    { to: 'COMPLETED', label: 'Complete', cls: 'btn' },
    { to: 'CANCELLED', label: 'Cancel', cls: 'btn-danger' },
  ],
  RESCHEDULED: [
    { to: 'CONFIRMED', label: 'Confirm', cls: 'btn' },
    { to: 'CANCELLED', label: 'Cancel', cls: 'btn-danger' },
  ],
};

async function loadServiceOptions() {
  try {
    const services = await Api.get('/api/v1/scheduling/services');
    for (const s of services) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = `${s.name} (${s.duration_minutes} min)`;
      opt.dataset.duration = s.duration_minutes;
      els.serviceSelect.appendChild(opt);
    }
  } catch (err) {
    // Non-fatal — manual appointments can still be created without a service.
  }
}
els.serviceSelect.addEventListener('change', () => {
  const opt = els.serviceSelect.selectedOptions[0];
  if (opt && opt.dataset.duration) els.durationInput.value = opt.dataset.duration;
});

function renderRow(a) {
  const { date, time } = fmtDateTime(a.scheduled_at);
  const actions = (NEXT_ACTIONS[a.status] || [])
    .map(
      (t) =>
        `<button class="btn btn-small ${t.cls}" data-transition="${t.to}" data-id="${a.id}">${t.label}</button>`
    )
    .join('');
  const email = a.customer_email ? ` · ${escapeHtml(a.customer_email)}` : '';
  const notes = a.notes ? `<div class="meta">${escapeHtml(a.notes)}</div>` : '';
  return `
    <div class="ledger-row" data-row="${a.id}">
      <div class="slot-time"><span class="date-part">${date}</span>${time}</div>
      <div>
        <div class="who">${escapeHtml(a.customer_name)}</div>
        <div class="meta">${escapeHtml(a.customer_phone)}${email} · <span class="mono">${a.booking_ref}</span> · ${a.duration_minutes} min</div>
        ${notes}
      </div>
      <div class="actions">
        <span class="stamp stamp-${a.status.toLowerCase()}">${statusLabel(a.status)}</span>
        ${actions}
      </div>
    </div>`;
}

async function loadAppointments() {
  els.skeleton.hidden = false;
  els.ledger.hidden = true;
  els.empty.hidden = true;

  const params = new URLSearchParams();
  const status = document.getElementById('f-status').value;
  const phone = document.getElementById('f-phone').value.trim();
  const from = document.getElementById('f-from').value;
  const to = document.getElementById('f-to').value;
  if (status) params.set('status', status);
  if (phone) params.set('customer_phone', phone);
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);

  try {
    const appts = await Api.get(`/api/v1/appointments?${params.toString()}`);
    els.skeleton.hidden = true;
    if (appts.length === 0) {
      els.empty.hidden = false;
      return;
    }
    els.ledger.innerHTML = appts.map(renderRow).join('');
    els.ledger.hidden = false;
  } catch (err) {
    els.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

els.ledger.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-transition]');
  if (!btn) return;
  const id = btn.dataset.id;
  const to = btn.dataset.transition;
  btn.disabled = true;
  try {
    await Api.patch(`/api/v1/appointments/${id}`, { status: to });
    toast(`Marked ${statusLabel(to)}`);
    loadAppointments();
  } catch (err) {
    toast(err.detail || err.message, 'error');
    btn.disabled = false;
  }
});

els.filterForm.addEventListener('submit', (e) => {
  e.preventDefault();
  loadAppointments();
});

els.newBtn.addEventListener('click', () => {
  els.newPanel.hidden = !els.newPanel.hidden;
});
els.newCancel.addEventListener('click', () => {
  els.newPanel.hidden = true;
  els.newForm.reset();
});

els.newForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(els.newBanner, null);
  const whenLocal = document.getElementById('na-when').value;
  if (!whenLocal) return;
  const body = {
    customer_name: document.getElementById('na-name').value.trim(),
    customer_phone: document.getElementById('na-phone').value.trim(),
    service_id: els.serviceSelect.value || null,
    duration_minutes: Number(els.durationInput.value),
    scheduled_at: new Date(whenLocal).toISOString(),
    notes: document.getElementById('na-notes').value.trim() || null,
    status: 'CONFIRMED',
  };
  const submitBtn = document.getElementById('na-submit');
  submitBtn.disabled = true;
  try {
    await Api.post('/api/v1/appointments', body);
    toast('Appointment saved');
    els.newForm.reset();
    els.newPanel.hidden = true;
    loadAppointments();
  } catch (err) {
    showBanner(els.newBanner, err.detail || err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

loadServiceOptions();
loadAppointments();
