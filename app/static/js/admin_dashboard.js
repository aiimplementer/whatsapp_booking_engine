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

// Kept so the click handler can look up an appointment's details (name,
// time, phone, service) by id when building the confirmation message,
// without re-fetching or stashing data on the button itself.
let currentAppointments = [];

// One line per action describing what's about to happen, in the same
// window.confirm() style already used elsewhere in the app (see
// admin_staff.js, admin_services.js, public_book.js). Cancel/no-show get
// the fuller appointment details since those are the ones staff most need
// to double-check before acting; the rest stay a short, quick prompt.
const CONFIRM_COPY = {
  CONFIRMED: (a, when) => `Confirm the appointment for ${a.customer_name} at ${when.time} on ${when.date}?`,
  CHECKED_IN: (a, when) => `Check in ${a.customer_name} for their ${when.time} appointment?`,
  COMPLETED: (a, when) => `Mark ${a.customer_name}'s ${when.time} appointment as completed?`,
  NO_SHOW: (a, when) =>
    `Mark this appointment as a no-show?\n\n` +
    `${a.customer_name} · ${a.customer_phone}\n` +
    `${when.date} at ${when.time} · ${a.duration_minutes} min\n` +
    `Ref: ${a.booking_ref}`,
  CANCELLED: (a, when) =>
    `Cancel this appointment? This can't be undone.\n\n` +
    `${a.customer_name} · ${a.customer_phone}\n` +
    `${when.date} at ${when.time} · ${a.duration_minutes} min\n` +
    `Ref: ${a.booking_ref}`,
};

function confirmTransition(appt, to) {
  const tz = Api.getTenantTimezone();
  const when = fmtDateTime(appt.scheduled_at, tz);
  const build = CONFIRM_COPY[to];
  const message = build ? build(appt, when) : `Mark this appointment as ${statusLabel(to)}?`;
  return confirmDialog(message, { danger: to === 'CANCELLED' });
}

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
  const tz = Api.getTenantTimezone();
  const { time } = fmtDateTime(a.scheduled_at, tz);
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
      <div class="slot-time">${time}</div>
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

// Groups appointments (already sorted by scheduled_at from the API) into
// day buckets by the tenant's local calendar date — the business's own
// timezone (set in Business settings), not the viewer's browser timezone —
// so the list reads like pages in a diary — one date header per day —
// instead of one long flat run where you have to read every row's small
// date label to tell days apart, and so "Today" always means today at the
// front desk, not today wherever the logged-in staff member's laptop
// happens to think it is.
function groupByDate(appts, tz) {
  const groups = [];
  let currentKey = null;
  for (const a of appts) {
    const d = new Date(a.scheduled_at);
    const key = tzDateKey(d, tz);
    if (key !== currentKey) {
      currentKey = key;
      groups.push({ key, date: d, items: [] });
    }
    groups[groups.length - 1].items.push(a);
  }
  return groups;
}

function dateHeaderLabel(group, tz) {
  const todayKey = tzDateKey(new Date(), tz);
  const diffDays = Math.round((dateKeyToUtc(group.key) - dateKeyToUtc(todayKey)) / 86400000);
  const full = new Intl.DateTimeFormat(undefined, {
    timeZone: tz, weekday: "long", month: "long", day: "numeric", year: "numeric",
  }).format(group.date);
  if (diffDays === 0) return `Today · ${full}`;
  if (diffDays === 1) return `Tomorrow · ${full}`;
  if (diffDays === -1) return `Yesterday · ${full}`;
  return full;
}

// Cancelled/no-show/expired appointments still show up in the list (staff
// need to see them), but they're not really "on the books" for the day —
// counting them in the day header would overstate how much is actually
// happening that day.
const INACTIVE_STATUSES = new Set(['CANCELLED', 'NO_SHOW', 'EXPIRED']);

function renderLedger(appts) {
  const tz = Api.getTenantTimezone();
  return groupByDate(appts, tz)
    .map((g) => {
      const count = g.items.filter((a) => !INACTIVE_STATUSES.has(a.status)).length;
      return `
        <div class="ledger-date-header">
          <span>${dateHeaderLabel(g, tz)}</span>
          <span class="muted">${count} appointment${count === 1 ? "" : "s"}</span>
        </div>
        ${g.items.map(renderRow).join("")}
      `;
    })
    .join("");
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
    currentAppointments = appts;
    els.ledger.innerHTML = renderLedger(appts);
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
  const appt = currentAppointments.find((a) => String(a.id) === String(id));
  if (appt && !(await confirmTransition(appt, to))) return;
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
    // The datetime-local input's value has no timezone info attached, so
    // `new Date(whenLocal)` would parse it in the ADMIN'S OWN browser
    // timezone rather than the business's — an admin entering "9:00 AM"
    // for a tenant in a different timezone than their own would silently
    // get a wrong appointment time (potentially outside working hours,
    // since nothing downstream re-checks an already-computed timestamp).
    // zonedTimeToUtcIso interprets the entered wall-clock time as being in
    // the tenant's own timezone instead.
    scheduled_at: zonedTimeToUtcIso(whenLocal, Api.getTenantTimezone()),
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

// A calendar-page day cell (see admin_calendar.js) links here with
// ?date=YYYY-MM-DD, and a customers-page row links here with ?phone=... —
// pre-fill and apply the matching filter so the click actually lands on the
// relevant appointments instead of the full list.
const presetParams = new URLSearchParams(window.location.search);
const presetDate = presetParams.get('date');
const presetPhone = presetParams.get('phone');
if (presetDate) {
  document.getElementById('f-from').value = presetDate;
  document.getElementById('f-to').value = presetDate;
} else {
  // Default view opens on today's page of the ledger (tenant-local date),
  // not the oldest appointment on record — otherwise a business running
  // for months loads with last spring buried at the top and "today"
  // scrolled miles down. Staff can still clear "From" to pull up history.
  document.getElementById('f-from').value = tzDateKey(new Date(), Api.getTenantTimezone());
}
if (presetPhone) {
  document.getElementById('f-phone').value = presetPhone;
}

loadServiceOptions();
loadAppointments();
