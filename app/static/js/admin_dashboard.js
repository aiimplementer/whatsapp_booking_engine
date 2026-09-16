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
  slotPickerContainer: document.getElementById('na-slot-picker'),
  chosenSlotDisplay: document.getElementById('na-chosen-slot'),
  selectedSlotIso: document.getElementById('na-selected-slot-iso'),
  selectedSlotDuration: document.getElementById('na-selected-slot-duration'),
};

// Tenant-wide default appointment length (from the "no service selected"
// scheduling config), used to prefill Duration when the Service dropdown
// is left on "Not Applicable". Falls back to 30 if the config can't be loaded
// so the field is never left blank/invalid.
let tenantDefaultDuration = 30;

// Slot picker instance (initialized after services are loaded)
let slotPicker = null;

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
    // Initialize slot picker after services are loaded
    initializeSlotPicker();
  } catch (err) {
    // Non-fatal — manual appointments can still be created without a service.
  }
}

async function loadDefaultDuration() {
  try {
    const configs = await Api.get('/api/v1/scheduling/configs');
    // The tenant-wide default is the one config row with no service_id
    // pinned (service-specific configs override start/end times per
    // service, not the fallback duration used when staff pick "Not Applicable").
    const tenantConfig = configs.find((c) => !c.service_id);
    if (tenantConfig) tenantDefaultDuration = tenantConfig.appointment_duration_minutes;
  } catch (err) {
    // Non-fatal — falls back to the hardcoded default above.
  } finally {
    // Only set the field if the staff member hasn't already picked a
    // service (which would have set its own duration) or typed a value.
    if (!els.serviceSelect.value && !els.durationInput.value) {
      els.durationInput.value = tenantDefaultDuration;
    }
  }
}

// Duration is read-only (set from the selected service or the tenant
// default — see the service "change" handler and loadDefaultDuration
// above). The `readonly` attribute alone still lets some browsers change
// a focused number input's value with the mouse wheel or the spinner
// arrows, which would silently desync it from the service that's
// actually selected — blur immediately so it never stays focused/editable.
els.durationInput.addEventListener('focus', () => els.durationInput.blur());
els.durationInput.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });

function initializeSlotPicker() {
  const tz = Api.getTenantTimezone();
  slotPicker = new SlotPicker({
    container: els.slotPickerContainer,
    apiEndpoint: '/api/v1/appointments/available-slots',
    auth: true, // staff-only endpoint — requires the admin's bearer token
    tenantTimezone: tz,
    onSlotSelected: (slot) => {
      els.selectedSlotIso.value = slot.iso;
      els.selectedSlotDuration.value = slot.duration;
      const { date, time } = fmtDateTime(slot.iso, tz);
      els.chosenSlotDisplay.textContent = `✓ ${date} at ${time} (${slot.duration} min)`;
      els.chosenSlotDisplay.classList.remove('hidden');
    },
    onError: (err) => {
      showBanner(els.newBanner, `Could not load available slots: ${err.message}`);
    }
  });
}

els.serviceSelect.addEventListener('change', () => {
  // When service changes, reload available slots for that service, and
  // reset Duration to that service's own length (or the tenant default
  // for "Not Applicable") — staff can still edit it by hand afterward for a
  // one-off longer/shorter booking.
  const selectedServiceId = els.serviceSelect.value || null;
  const selectedOption = els.serviceSelect.selectedOptions[0];
  els.durationInput.value = selectedOption && selectedOption.dataset.duration
    ? selectedOption.dataset.duration
    : tenantDefaultDuration;
  if (slotPicker) {
    slotPicker.clearSelection();
    els.chosenSlotDisplay.classList.add('hidden');
    els.selectedSlotIso.value = '';
    els.selectedSlotDuration.value = '';
    slotPicker.loadSlots(selectedServiceId);
  }
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
  if (!els.newPanel.hidden && slotPicker) {
    // When opening the form, load slots for the currently selected service
    const selectedServiceId = els.serviceSelect.value || null;
    slotPicker.loadSlots(selectedServiceId);
    // form.reset() (Cancel/after save) clears Duration back to blank since
    // it has no static HTML default — restore it here so the field never
    // opens empty.
    if (!els.durationInput.value) {
      const selectedOption = els.serviceSelect.selectedOptions[0];
      els.durationInput.value = selectedOption && selectedOption.dataset.duration
        ? selectedOption.dataset.duration
        : tenantDefaultDuration;
    }
  }
});
els.newCancel.addEventListener('click', () => {
  els.newPanel.hidden = true;
  els.newForm.reset();
  els.durationInput.value = tenantDefaultDuration;
  if (slotPicker) {
    slotPicker.clearSelection();
    els.chosenSlotDisplay.classList.add('hidden');
    els.selectedSlotIso.value = '';
    els.selectedSlotDuration.value = '';
  }
});

els.newForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(els.newBanner, null);
  
  // The slot picker provides scheduled_at; duration_minutes comes from the
  // (editable) Duration field, which defaults to the picked slot's own
  // length but staff can override it for a one-off longer/shorter booking
  // — the server re-validates the result against business hours and
  // existing appointments regardless of what's sent here.
  const scheduledAt = els.selectedSlotIso.value;
  const durationMinutes = Number(els.durationInput.value);
  
  if (!scheduledAt) {
    showBanner(els.newBanner, 'Please select a date and time from the available slots');
    return;
  }
  if (!durationMinutes || durationMinutes <= 0) {
    showBanner(els.newBanner, 'Please enter a valid duration');
    return;
  }
  
  const body = {
    customer_name: document.getElementById('na-name').value.trim(),
    customer_phone: document.getElementById('na-phone').value.trim(),
    service_id: els.serviceSelect.value || null,
    duration_minutes: durationMinutes,
    // The slot picker already returns a UTC ISO string, so use it directly
    scheduled_at: scheduledAt,
    notes: document.getElementById('na-notes').value.trim() || null,
    status: 'CONFIRMED',
  };
  const submitBtn = document.getElementById('na-submit');
  submitBtn.disabled = true;
  try {
    await Api.post('/api/v1/appointments', body);
    toast('Appointment saved');
    els.newForm.reset();
    els.durationInput.value = tenantDefaultDuration;
    els.newPanel.hidden = true;
    if (slotPicker) {
      slotPicker.clearSelection();
      els.chosenSlotDisplay.classList.add('hidden');
      els.selectedSlotIso.value = '';
      els.selectedSlotDuration.value = '';
    }
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
loadDefaultDuration();
loadAppointments();