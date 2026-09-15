const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/* ---- working-hours config form ----------------------------------------- */

const cfgEls = {
  service: document.getElementById('cfg-service'),
  days: document.getElementById('cfg-days'),
  windows: document.getElementById('cfg-windows'),
  addWindow: document.getElementById('cfg-add-window'),
  duration: document.getElementById('cfg-duration'),
  buffer: document.getElementById('cfg-buffer'),
  advance: document.getElementById('cfg-advance'),
  form: document.getElementById('cfg-form'),
  banner: document.getElementById('cfg-banner'),
  skeleton: document.getElementById('cfg-skeleton'),
  table: document.getElementById('cfg-table'),
  tbody: document.getElementById('cfg-tbody'),
};

let servicesCache = [];
let configsCache = [];

function renderDayCheckboxes(mask = 62) {
  cfgEls.days.innerHTML = DOW.map(
    (label, i) => `
    <label style="font-weight:400; display:flex; align-items:center; gap:0.3rem; width:auto;">
      <input type="checkbox" data-day="${i}" style="width:auto;" ${mask & (1 << i) ? 'checked' : ''}/>
      ${label}
    </label>`
  ).join('');
}

function addWindowRow(win) {
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `
    <select class="win-day" style="width:auto;">
      ${DOW.map((d, i) => `<option value="${i}" ${win && win.day === i ? 'selected' : ''}>${d}</option>`).join('')}
    </select>
    <input type="time" class="win-start" value="${win ? win.start : '09:00'}" style="width:auto;" />
    <span class="muted">to</span>
    <input type="time" class="win-end" value="${win ? win.end : '17:00'}" style="width:auto;" />
    <button type="button" class="btn btn-small btn-secondary" data-remove-window>Remove</button>
  `;
  cfgEls.windows.appendChild(row);
}

cfgEls.windows.addEventListener('click', (e) => {
  if (e.target.closest('[data-remove-window]')) {
    e.target.closest('.row').remove();
  }
});
cfgEls.addWindow.addEventListener('click', () => addWindowRow(null));

function readMask() {
  let mask = 0;
  cfgEls.days.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    if (cb.checked) mask |= 1 << Number(cb.dataset.day);
  });
  return mask;
}

function readWindows() {
  return Array.from(cfgEls.windows.querySelectorAll('.row')).map((row) => ({
    day: Number(row.querySelector('.win-day').value),
    start: row.querySelector('.win-start').value,
    end: row.querySelector('.win-end').value,
  }));
}

function resetCfgForm() {
  cfgEls.service.value = '';
  renderDayCheckboxes(62);
  cfgEls.windows.innerHTML = '';
  addWindowRow({ day: 1, start: '09:00', end: '17:00' });
  cfgEls.duration.value = 20;
  cfgEls.buffer.value = 0;
  cfgEls.advance.value = 30;
  showBanner(cfgEls.banner, null);
}

function loadConfigIntoForm(cfg) {
  cfgEls.service.value = cfg.service_id || '';
  renderDayCheckboxes(cfg.working_days);
  cfgEls.windows.innerHTML = '';
  (cfg.time_slots.length ? cfg.time_slots : [{ day: 1, start: '09:00', end: '17:00' }]).forEach(
    addWindowRow
  );
  cfgEls.duration.value = cfg.appointment_duration_minutes;
  cfgEls.buffer.value = cfg.buffer_minutes;
  cfgEls.advance.value = cfg.advance_booking_days;
  showBanner(cfgEls.banner, null);
  cfgEls.form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/**
 * Fill the form's day/window/duration fields from an existing config, but
 * keep `selectedServiceId` in the "Applies to" dropdown rather than
 * switching it to `baseline`'s own service_id — this is used to pre-fill a
 * *new* config (for a selection that has no saved config of its own) with
 * sensible values, not to open baseline itself for editing.
 */
function applyBaseline(baseline, selectedServiceId) {
  cfgEls.service.value = selectedServiceId;
  renderDayCheckboxes(baseline.working_days);
  cfgEls.windows.innerHTML = '';
  (baseline.time_slots.length ? baseline.time_slots : [{ day: 1, start: '09:00', end: '17:00' }]).forEach(
    addWindowRow
  );
  cfgEls.duration.value = baseline.appointment_duration_minutes;
  cfgEls.buffer.value = baseline.buffer_minutes;
  cfgEls.advance.value = baseline.advance_booking_days;
  showBanner(cfgEls.banner, null);
}

/**
 * Pick what to show in the form for the current "Applies to" selection.
 *   1. An exact saved config for this selection -> load it (editing).
 *   2. No exact match, but a tenant-wide default already exists -> prefill
 *      from it. That's the schedule actually in effect for this selection
 *      today (per _load_config's fallback in app/services/slots.py), so
 *      it's a far more useful starting point than an arbitrary placeholder.
 *   3. No tenant-wide default either, but SOME config exists (e.g. the
 *      original default service's schedule from signup) -> prefill from
 *      the oldest one. This is what lets creating the tenant-wide default
 *      for the first time inherit the business's actual established hours
 *      instead of resetting to a generic Mon-only window.
 *   4. Genuinely no configs anywhere yet (brand-new tenant) -> the generic
 *      placeholder is the only option left.
 */
function syncFormToSelection() {
  const selectedServiceId = cfgEls.service.value;
  const exact = configsCache.find((c) => (c.service_id || '') === selectedServiceId);
  if (exact) {
    loadConfigIntoForm(exact);
    return;
  }
  const tenantWideDefault = configsCache.find((c) => !c.service_id);
  if (tenantWideDefault) {
    applyBaseline(tenantWideDefault, selectedServiceId);
    return;
  }
  if (configsCache.length > 0) {
    const oldest = [...configsCache].sort(
      (a, b) => new Date(a.created_at) - new Date(b.created_at)
    )[0];
    applyBaseline(oldest, selectedServiceId);
    return;
  }
  resetCfgForm();
  cfgEls.service.value = selectedServiceId;
}

cfgEls.service.addEventListener('change', syncFormToSelection);

async function loadServiceOptionsForConfig() {
  try {
    servicesCache = await Api.get('/api/v1/scheduling/services?include_inactive=true');
    for (const s of servicesCache) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name;
      cfgEls.service.appendChild(opt);
    }
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
}

function serviceName(id) {
  if (!id) return 'Tenant-wide default';
  const s = servicesCache.find((x) => x.id === id);
  return s ? s.name : 'Unknown service';
}

function maskSummary(mask) {
  const days = DOW.filter((_, i) => mask & (1 << i));
  return days.length === 7 ? 'Every day' : days.length ? days.join(', ') : 'No days set';
}

async function loadConfigs() {
  cfgEls.skeleton.hidden = false;
  cfgEls.table.classList.add('hidden');
  try {
    configsCache = await Api.get('/api/v1/scheduling/configs');
    cfgEls.skeleton.hidden = true;
    if (configsCache.length === 0) {
      cfgEls.tbody.innerHTML = `<tr><td colspan="5" class="muted">No schedule set yet — use the form above.</td></tr>`;
      cfgEls.table.classList.remove('hidden');
      syncFormToSelection();
      return;
    }
    cfgEls.tbody.innerHTML = configsCache
      .map(
        (c) => `
      <tr>
        <td>${escapeHtml(serviceName(c.service_id))}</td>
        <td class="muted">${maskSummary(c.working_days)}</td>
        <td class="mono">${c.appointment_duration_minutes}m / ${c.buffer_minutes}m buffer</td>
        <td class="mono">${c.advance_booking_days}d</td>
        <td><button class="btn btn-small btn-secondary" data-edit-cfg="${c.service_id || ''}">Edit</button></td>
      </tr>`
      )
      .join('');
    cfgEls.table.classList.remove('hidden');
    syncFormToSelection();
  } catch (err) {
    cfgEls.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

cfgEls.tbody.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-edit-cfg]');
  if (!btn) return;
  const svcId = btn.dataset.editCfg;
  const cfg = configsCache.find((c) => (c.service_id || '') === svcId);
  if (cfg) loadConfigIntoForm(cfg);
});

cfgEls.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(cfgEls.banner, null);
  const windows = readWindows();
  for (const w of windows) {
    if (w.end <= w.start) {
      showBanner(cfgEls.banner, 'Each window\'s end time must be after its start time.');
      return;
    }
  }
  const body = {
    service_id: cfgEls.service.value || null,
    working_days: readMask(),
    time_slots: windows,
    appointment_duration_minutes: Number(cfgEls.duration.value),
    buffer_minutes: Number(cfgEls.buffer.value),
    advance_booking_days: Number(cfgEls.advance.value),
  };
  const btn = document.getElementById('cfg-submit');
  btn.disabled = true;
  try {
    await Api.put('/api/v1/scheduling/configs', body);
    toast('Schedule saved');
    loadConfigs();
  } catch (err) {
    showBanner(cfgEls.banner, err.detail || err.message);
  } finally {
    btn.disabled = false;
  }
});

/* ---- holidays ------------------------------------------------------------ */

const holForm = document.getElementById('holiday-form');
const holList = document.getElementById('hol-list');

async function loadHolidays() {
  try {
    const holidays = await Api.get('/api/v1/scheduling/holidays');
    holList.innerHTML = holidays.length
      ? holidays
          .map(
            (h) => `
        <div class="ledger-row" style="grid-template-columns: 120px 1fr auto;">
          <div class="slot-time">${h.date}</div>
          <div class="meta">${escapeHtml(h.reason || '—')}</div>
          <div class="actions"><button class="btn btn-small btn-danger" data-del-hol="${h.id}">Remove</button></div>
        </div>`
          )
          .join('')
      : '<p class="muted">No holidays added.</p>';
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
}

holForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    date: document.getElementById('hol-date').value,
    reason: document.getElementById('hol-reason').value.trim() || null,
  };
  try {
    await Api.post('/api/v1/scheduling/holidays', body);
    toast('Holiday added');
    holForm.reset();
    loadHolidays();
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
});

holList.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-del-hol]');
  if (!btn) return;
  try {
    await Api.del(`/api/v1/scheduling/holidays/${btn.dataset.delHol}`);
    toast('Holiday removed');
    loadHolidays();
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
});

/* ---- blocked times --------------------------------------------------------- */

const blkForm = document.getElementById('blocked-form');
const blkList = document.getElementById('blk-list');

async function loadBlockedTimes() {
  try {
    const blocks = await Api.get('/api/v1/scheduling/blocked-times');
    blkList.innerHTML = blocks.length
      ? blocks
          .map((b) => {
            const start = fmtDateTime(b.start_datetime, Api.getTenantTimezone());
            const end = fmtDateTime(b.end_datetime, Api.getTenantTimezone());
            return `
        <div class="ledger-row" style="grid-template-columns: 200px 1fr auto;">
          <div class="slot-time">${start.date} ${start.time} → ${end.time}</div>
          <div class="meta">${escapeHtml(b.reason || '—')}</div>
          <div class="actions"><button class="btn btn-small btn-danger" data-del-blk="${b.id}">Remove</button></div>
        </div>`;
          })
          .join('')
      : '<p class="muted">No blocked times added.</p>';
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
}

blkForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const startVal = document.getElementById('blk-start').value;
  const endVal = document.getElementById('blk-end').value;
  if (!startVal || !endVal) return;
  const body = {
    // See api.js zonedTimeToUtcIso for why this can't be a plain
    // `new Date(startVal).toISOString()` — that would interpret the
    // entered wall-clock time in the admin's own browser timezone instead
    // of the tenant's, silently blocking the wrong hours.
    start_datetime: zonedTimeToUtcIso(startVal, Api.getTenantTimezone()),
    end_datetime: zonedTimeToUtcIso(endVal, Api.getTenantTimezone()),
    reason: document.getElementById('blk-reason').value.trim() || null,
  };
  try {
    await Api.post('/api/v1/scheduling/blocked-times', body);
    toast('Block added');
    blkForm.reset();
    loadBlockedTimes();
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
});

blkList.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-del-blk]');
  if (!btn) return;
  try {
    await Api.del(`/api/v1/scheduling/blocked-times/${btn.dataset.delBlk}`);
    toast('Block removed');
    loadBlockedTimes();
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
});

/* ---- daily (recurring) blocked times --------------------------------------- */

const recEls = {
  form: document.getElementById('rec-form'),
  start: document.getElementById('rec-start'),
  end: document.getElementById('rec-end'),
  reason: document.getElementById('rec-reason'),
  days: document.getElementById('rec-days'),
  toggleRange: document.getElementById('rec-toggle-range'),
  range: document.getElementById('rec-range'),
  startDate: document.getElementById('rec-start-date'),
  endDate: document.getElementById('rec-end-date'),
  editId: document.getElementById('rec-edit-id'),
  submit: document.getElementById('rec-submit'),
  cancelEdit: document.getElementById('rec-cancel-edit'),
  banner: document.getElementById('rec-banner'),
  skeleton: document.getElementById('rec-skeleton'),
  table: document.getElementById('rec-table'),
  tbody: document.getElementById('rec-tbody'),
};

let recurringCache = [];

// Defaults to every day (mask 127) — a lunch-break block is meant to apply
// daily, so "every day" is the sensible starting point rather than forcing
// the admin to tick all seven boxes themselves.
function renderRecDayCheckboxes(mask = 127) {
  recEls.days.innerHTML = DOW.map(
    (label, i) => `
    <label style="font-weight:400; display:flex; align-items:center; gap:0.3rem; width:auto;">
      <input type="checkbox" data-rec-day="${i}" style="width:auto;" ${mask & (1 << i) ? 'checked' : ''}/>
      ${label}
    </label>`
  ).join('');
}

function readRecMask() {
  let mask = 0;
  recEls.days.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    if (cb.checked) mask |= 1 << Number(cb.dataset.recDay);
  });
  return mask;
}

function resetRecForm() {
  recEls.start.value = '';
  recEls.end.value = '';
  recEls.reason.value = '';
  renderRecDayCheckboxes(127);
  recEls.range.hidden = true;
  recEls.startDate.value = '';
  recEls.endDate.value = '';
  recEls.editId.value = '';
  recEls.submit.textContent = 'Add daily block';
  recEls.cancelEdit.hidden = true;
  showBanner(recEls.banner, null);
}

recEls.toggleRange.addEventListener('click', () => {
  recEls.range.hidden = !recEls.range.hidden;
});

recEls.cancelEdit.addEventListener('click', () => resetRecForm());

function recRangeSummary(r) {
  if (!r.start_date && !r.end_date) return 'Every day, ongoing';
  if (r.start_date && r.end_date) return `${r.start_date} → ${r.end_date}`;
  if (r.start_date) return `From ${r.start_date}`;
  return `Until ${r.end_date}`;
}

async function loadRecurringBlockedTimes() {
  recEls.skeleton.hidden = false;
  recEls.table.classList.add('hidden');
  try {
    recurringCache = await Api.get(
      '/api/v1/scheduling/recurring-blocked-times?include_inactive=true'
    );
    recEls.skeleton.hidden = true;
    if (recurringCache.length === 0) {
      recEls.tbody.innerHTML = `<tr><td colspan="5" class="muted">No daily blocks added yet — use the form above.</td></tr>`;
      recEls.table.classList.remove('hidden');
      return;
    }
    recEls.tbody.innerHTML = recurringCache
      .map(
        (r) => `
      <tr>
        <td class="mono">${r.start_time} – ${r.end_time}</td>
        <td class="muted">${maskSummary(r.days_of_week)} · ${recRangeSummary(r)}</td>
        <td>${escapeHtml(r.reason || '—')}</td>
        <td>${r.active ? 'Active' : '<span class="muted">Paused</span>'}</td>
        <td style="display:flex; gap:0.4rem; flex-wrap:wrap; justify-content:flex-end;">
          <button class="btn btn-small btn-secondary" data-edit-rec="${r.id}">Edit</button>
          <button class="btn btn-small btn-secondary" data-toggle-rec="${r.id}">${r.active ? 'Pause' : 'Resume'}</button>
          <button class="btn btn-small btn-danger" data-del-rec="${r.id}">Remove</button>
        </td>
      </tr>`
      )
      .join('');
    recEls.table.classList.remove('hidden');
  } catch (err) {
    recEls.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

recEls.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(recEls.banner, null);
  const startVal = recEls.start.value;
  const endVal = recEls.end.value;
  if (!startVal || !endVal) return;
  if (endVal <= startVal) {
    showBanner(recEls.banner, 'End time must be after start time.');
    return;
  }
  const mask = readRecMask();
  if (mask === 0) {
    showBanner(recEls.banner, 'Pick at least one day.');
    return;
  }
  // Date fields are only sent if the admin explicitly opened the optional
  // range — collapsed (the default), this posts start_date/end_date as
  // null, which the API/slot engine treats as "no boundary" i.e. every
  // matching weekday, indefinitely.
  const body = {
    start_time: startVal,
    end_time: endVal,
    days_of_week: mask,
    reason: recEls.reason.value.trim() || null,
    start_date: recEls.range.hidden ? null : recEls.startDate.value || null,
    end_date: recEls.range.hidden ? null : recEls.endDate.value || null,
  };
  const editId = recEls.editId.value;
  recEls.submit.disabled = true;
  try {
    if (editId) {
      await Api.patch(`/api/v1/scheduling/recurring-blocked-times/${editId}`, body);
      toast('Daily block updated');
    } else {
      await Api.post('/api/v1/scheduling/recurring-blocked-times', body);
      toast('Daily block added');
    }
    resetRecForm();
    loadRecurringBlockedTimes();
  } catch (err) {
    showBanner(recEls.banner, err.detail || err.message);
  } finally {
    recEls.submit.disabled = false;
  }
});

recEls.tbody.addEventListener('click', async (e) => {
  const editBtn = e.target.closest('[data-edit-rec]');
  if (editBtn) {
    const r = recurringCache.find((x) => x.id === editBtn.dataset.editRec);
    if (!r) return;
    recEls.start.value = r.start_time;
    recEls.end.value = r.end_time;
    recEls.reason.value = r.reason || '';
    renderRecDayCheckboxes(r.days_of_week);
    if (r.start_date || r.end_date) {
      recEls.range.hidden = false;
      recEls.startDate.value = r.start_date || '';
      recEls.endDate.value = r.end_date || '';
    } else {
      recEls.range.hidden = true;
      recEls.startDate.value = '';
      recEls.endDate.value = '';
    }
    recEls.editId.value = r.id;
    recEls.submit.textContent = 'Save changes';
    recEls.cancelEdit.hidden = false;
    recEls.form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }

  const toggleBtn = e.target.closest('[data-toggle-rec]');
  if (toggleBtn) {
    const r = recurringCache.find((x) => x.id === toggleBtn.dataset.toggleRec);
    if (!r) return;
    try {
      await Api.patch(`/api/v1/scheduling/recurring-blocked-times/${r.id}`, {
        active: !r.active,
      });
      toast(r.active ? 'Daily block paused' : 'Daily block resumed');
      loadRecurringBlockedTimes();
    } catch (err) {
      toast(err.detail || err.message, 'error');
    }
    return;
  }

  const delBtn = e.target.closest('[data-del-rec]');
  if (delBtn) {
    try {
      await Api.del(`/api/v1/scheduling/recurring-blocked-times/${delBtn.dataset.delRec}`);
      toast('Daily block removed');
      loadRecurringBlockedTimes();
    } catch (err) {
      toast(err.detail || err.message, 'error');
    }
  }
});

/* ---- init ------------------------------------------------------------------ */

(async function init() {
  resetCfgForm();
  resetRecForm();
  await loadServiceOptionsForConfig();
  loadConfigs();
  loadHolidays();
  loadBlockedTimes();
  loadRecurringBlockedTimes();
})();
