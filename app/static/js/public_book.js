const slug = document.querySelector('.booking-wrap').dataset.slug;
const api = (path, opts = {}) => Api.request(`/api/v1/public/${slug}${path}`, { auth: false, ...opts });

const pbEls = {
  loadError: document.getElementById('load-error'),
  root: document.getElementById('booking-root'),
  bizName: document.getElementById('biz-name'),
  serviceList: document.getElementById('service-list'),
  stepSlots: document.getElementById('step-slots'),
  dayTabs: document.getElementById('day-tabs'),
  slotGrid: document.getElementById('slot-grid'),
  noSlots: document.getElementById('no-slots'),
  stepDetails: document.getElementById('step-details'),
  chosenSummary: document.getElementById('chosen-slot-summary'),
  bookingForm: document.getElementById('booking-form'),
  bookingBanner: document.getElementById('booking-banner'),
  stepConfirmation: document.getElementById('step-confirmation'),
  confRef: document.getElementById('conf-ref'),
  confWhen: document.getElementById('conf-when'),
  confDetails: document.getElementById('conf-details'),
  lookupForm: document.getElementById('lookup-form'),
  lookupResult: document.getElementById('lookup-result'),
  tabNew: document.getElementById('tab-new'),
  tabLookup: document.getElementById('tab-lookup'),
  bookingAppView: document.getElementById('booking-app'),
  lookupView: document.getElementById('lookup-view'),
  bookingSide: document.getElementById('booking-side'),
  sideService: document.getElementById('side-service'),
  sideTime: document.getElementById('side-time'),
  sideHint: document.getElementById('side-hint'),
};

/* ---- New booking / Look up switch --------------------------------------- */
// A segmented toggle rather than stacking both flows on the page — only one
// is visible at a time, so the page doesn't grow tall just to accommodate
// an occasional-use lookup form underneath the main flow.
function setMode(mode) {
  const isNew = mode === 'new';
  pbEls.tabNew.classList.toggle('active', isNew);
  pbEls.tabLookup.classList.toggle('active', !isNew);
  pbEls.tabNew.setAttribute('aria-selected', String(isNew));
  pbEls.tabLookup.setAttribute('aria-selected', String(!isNew));
  pbEls.bookingAppView.classList.toggle('hidden', !isNew);
  pbEls.lookupView.classList.toggle('hidden', isNew);
  // The recap sidebar only means anything for the booking flow — hide it
  // while looking up/cancelling so that view isn't left with a stale or
  // pointless "Not selected yet" card beside it.
  pbEls.bookingSide.classList.toggle('hidden', !isNew);
}
pbEls.tabNew.addEventListener('click', () => setMode('new'));
pbEls.tabLookup.addEventListener('click', () => setMode('lookup'));

let services = [];
let selectedServiceId = null;
let slotsByDay = new Map();
let selectedDayKey = null;
let selectedSlot = null;

function dayKey(iso) {
  const d = new Date(iso);
  return d.toDateString();
}

function renderServices() {
  pbEls.serviceList.innerHTML = services
    .map(
      (s) => `
    <div class="service-option" data-service="${s.id}">
      <div>
        <strong>${escapeHtml(s.name)}</strong>
        ${s.description ? `<div class="muted" style="font-size:0.85rem;">${escapeHtml(s.description)}</div>` : ''}
      </div>
      <span class="dur">${s.duration_minutes} min</span>
    </div>`
    )
    .join('');
}

pbEls.serviceList.addEventListener('click', (e) => {
  const opt = e.target.closest('.service-option');
  if (!opt) return;
  document.querySelectorAll('.service-option').forEach((el) => el.classList.remove('selected'));
  opt.classList.add('selected');
  selectedServiceId = opt.dataset.service;
  pbEls.stepDetails.classList.add('hidden');
  pbEls.stepConfirmation.classList.add('hidden');
  const svc = services.find((s) => s.id === selectedServiceId);
  pbEls.sideService.textContent = svc ? svc.name : '—';
  pbEls.sideTime.textContent = 'Not selected yet';
  pbEls.sideHint.textContent = 'Now choose a time.';
  loadSlots();
});

async function loadSlots() {
  pbEls.stepSlots.classList.remove('hidden');
  pbEls.dayTabs.innerHTML = '<span class="muted">Loading…</span>';
  pbEls.slotGrid.innerHTML = '';
  pbEls.noSlots.classList.add('hidden');
  try {
    const qs = selectedServiceId ? `?service_id=${encodeURIComponent(selectedServiceId)}` : '';
    const slots = await api(`/available-slots${qs}`);
    slotsByDay = new Map();
    for (const s of slots) {
      const key = dayKey(s.scheduled_at);
      if (!slotsByDay.has(key)) slotsByDay.set(key, []);
      slotsByDay.get(key).push(s);
    }
    const keys = Array.from(slotsByDay.keys());
    if (keys.length === 0) {
      pbEls.dayTabs.innerHTML = '';
      pbEls.noSlots.classList.remove('hidden');
      return;
    }
    selectedDayKey = keys[0];
    renderDayTabs(keys);
    renderSlotGrid();
  } catch (err) {
    pbEls.dayTabs.innerHTML = '';
    toast(err.detail || err.message, 'error');
  }
}

function renderDayTabs(keys) {
  pbEls.dayTabs.innerHTML = keys
    .map((key) => {
      const d = new Date(key);
      const dow = d.toLocaleDateString(undefined, { weekday: 'short' });
      const md = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      return `<div class="day-tab ${key === selectedDayKey ? 'selected' : ''}" data-day="${key}">
        <span class="dow">${dow}</span>${md}
      </div>`;
    })
    .join('');
}

pbEls.dayTabs.addEventListener('click', (e) => {
  const tab = e.target.closest('.day-tab');
  if (!tab) return;
  selectedDayKey = tab.dataset.day;
  document.querySelectorAll('.day-tab').forEach((t) => t.classList.remove('selected'));
  tab.classList.add('selected');
  renderSlotGrid();
});

function renderSlotGrid() {
  const slots = slotsByDay.get(selectedDayKey) || [];
  pbEls.noSlots.classList.toggle('hidden', slots.length > 0);
  pbEls.slotGrid.innerHTML = slots
    .map((s) => {
      const t = new Date(s.scheduled_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
      return `<button type="button" class="slot-btn" data-iso="${s.scheduled_at}" data-duration="${s.duration_minutes}">${t}</button>`;
    })
    .join('');
}

pbEls.slotGrid.addEventListener('click', (e) => {
  const btn = e.target.closest('.slot-btn');
  if (!btn) return;
  document.querySelectorAll('.slot-btn').forEach((b) => b.classList.remove('selected'));
  btn.classList.add('selected');
  selectedSlot = { iso: btn.dataset.iso, duration: Number(btn.dataset.duration) };
  const { date, time } = fmtDateTime(selectedSlot.iso);
  const svcName = selectedServiceId ? services.find((s) => s.id === selectedServiceId)?.name : null;
  pbEls.chosenSummary.textContent = `${svcName ? svcName + ' — ' : ''}${date} at ${time} (${selectedSlot.duration} min)`;
  pbEls.sideTime.textContent = `${date} at ${time}`;
  pbEls.sideHint.textContent = 'Fill in your details to confirm.';
  pbEls.stepDetails.classList.remove('hidden');
  pbEls.stepConfirmation.classList.add('hidden');
  pbEls.stepDetails.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

pbEls.bookingForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(pbEls.bookingBanner, null);
  if (!selectedSlot) return;
  const body = {
    service_id: selectedServiceId || null,
    customer_name: document.getElementById('b-name').value.trim(),
    customer_phone: document.getElementById('b-phone').value.trim(),
    customer_email: document.getElementById('b-email').value.trim() || null,
    scheduled_at: selectedSlot.iso,
    notes: document.getElementById('b-notes').value.trim() || null,
  };
  const { date, time } = fmtDateTime(selectedSlot.iso);
  const svcName = selectedServiceId ? services.find((s) => s.id === selectedServiceId)?.name : null;
  const confirmMsg =
    `Book this appointment?\n\n` +
    `${body.customer_name}${svcName ? ' — ' + svcName : ''}\n` +
    `${date} at ${time} (${selectedSlot.duration} min)`;
  if (!confirm(confirmMsg)) return;
  const btn = document.getElementById('b-submit');
  btn.disabled = true;
  try {
    const booking = await api('/appointments', { method: 'POST', body });
    pbEls.confRef.textContent = booking.booking_ref;
    const { date, time } = fmtDateTime(booking.scheduled_at);
    pbEls.confWhen.textContent = `${date} at ${time} — ${statusLabel(booking.status)}`;
    pbEls.confDetails.textContent = `${booking.customer_name} · ${booking.customer_phone}`;
    pbEls.stepConfirmation.classList.remove('hidden');
    pbEls.stepConfirmation.scrollIntoView({ behavior: 'smooth', block: 'start' });
    pbEls.bookingForm.reset();
    pbEls.sideHint.textContent = 'Booked — see your confirmation below.';
    loadSlots();
  } catch (err) {
    showBanner(pbEls.bookingBanner, err.detail || err.message);
  } finally {
    btn.disabled = false;
  }
});

pbEls.lookupForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const ref = document.getElementById('l-ref').value.trim();
  const phone = document.getElementById('l-phone').value.trim();
  pbEls.lookupResult.innerHTML = '<span class="muted">Looking up…</span>';
  try {
    const booking = await api(`/appointments/${encodeURIComponent(ref)}?customer_phone=${encodeURIComponent(phone)}`);
    const { date, time } = fmtDateTime(booking.scheduled_at);
    const cancellable = ['PENDING', 'CONFIRMED'].includes(booking.status);
    pbEls.lookupResult.innerHTML = `
      <div class="ledger-row" style="grid-template-columns: 1fr auto;">
        <div>
          <div class="who">${escapeHtml(booking.customer_name)} — ${date} at ${time}</div>
          <div class="meta"><span class="mono">${booking.booking_ref}</span> · ${booking.duration_minutes} min</div>
        </div>
        <div class="actions">
          <span class="stamp stamp-${booking.status.toLowerCase()}">${statusLabel(booking.status)}</span>
          ${cancellable ? `<button class="btn btn-small btn-danger" id="cancel-booking-btn">Cancel booking</button>` : ''}
        </div>
      </div>`;
    if (cancellable) {
      document.getElementById('cancel-booking-btn').addEventListener('click', async () => {
        const details =
          `Cancel this booking? This can't be undone.\n\n` +
          `${booking.customer_name} — ${date} at ${time}\n` +
          `${booking.duration_minutes} min · Ref: ${booking.booking_ref}`;
        if (!confirm(details)) return;
        try {
          await api(`/appointments/${encodeURIComponent(ref)}/cancel?customer_phone=${encodeURIComponent(phone)}`, {
            method: 'POST',
          });
          toast('Booking cancelled');
          pbEls.lookupForm.dispatchEvent(new Event('submit'));
        } catch (err) {
          toast(err.detail || err.message, 'error');
        }
      });
    }
  } catch (err) {
    const message =
      err.status === 404
        ? "No booking found with that reference and phone number — double-check both, including the country code."
        : err.detail || err.message;
    pbEls.lookupResult.innerHTML = `<div class="banner banner-error">${escapeHtml(message)}</div>`;
  }
});

async function init() {
  try {
    const info = await api('');
    pbEls.loadError.classList.add('hidden');
    pbEls.root.classList.remove('hidden');
    pbEls.bizName.textContent = info.name;
    services = info.services || [];
    if (services.length > 0) {
      renderServices();
      document.getElementById('step-service').classList.remove('hidden');
    } else {
      document.getElementById('step-service').classList.add('hidden');
      selectedServiceId = null;
      pbEls.sideService.textContent = 'Standard appointment';
      pbEls.sideHint.textContent = 'Now choose a time.';
      loadSlots();
    }
  } catch (err) {
    pbEls.root.classList.add('hidden');
    pbEls.loadError.classList.remove('hidden');
    pbEls.loadError.textContent =
      err.status === 404
        ? "We couldn't find this business, or it isn't taking online bookings right now."
        : err.detail || err.message;
  }
}

init();
