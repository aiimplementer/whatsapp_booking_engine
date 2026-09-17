const slug = document.querySelector('.booking-wrap').dataset.slug;
const api = (path, opts = {}) => Api.request(`/api/v1/public/${slug}${path}`, { auth: false, ...opts });

const pbEls = {
  loadError: document.getElementById('load-error'),
  root: document.getElementById('booking-root'),
  bizName: document.getElementById('biz-name'),
  businessInfo: document.getElementById('business-info'),
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
  tabAbout: document.getElementById('tab-about'),
  tabNew: document.getElementById('tab-new'),
  tabLookup: document.getElementById('tab-lookup'),
  tabOffers: document.getElementById('tab-offers'),
  tabAnnouncements: document.getElementById('tab-announcements'),
  tabHours: document.getElementById('tab-hours'),
  bookingAppView: document.getElementById('booking-app'),
  aboutView: document.getElementById('about-view'),
  lookupView: document.getElementById('lookup-view'),
  offersView: document.getElementById('offers-view'),
  announcementsView: document.getElementById('announcements-view'),
  hoursView: document.getElementById('hours-view'),
  aboutContent: document.getElementById('about-content'),
  offersContent: document.getElementById('offers-content'),
  announcementsContent: document.getElementById('announcements-content'),
  hoursContent: document.getElementById('hours-content'),
  bookingSide: document.getElementById('booking-side'),
  sideService: document.getElementById('side-service'),
  sideTime: document.getElementById('side-time'),
  sideHint: document.getElementById('side-hint'),
};

/* ---- New booking / Look up / Offers / Announcements / Hours switch ------ */
// A segmented toggle rather than stacking every view on the page — only one
// is visible at a time, so the page doesn't grow tall just to accommodate
// occasional-use info panels underneath the main booking flow. The last
// three tabs are info-only (added on top of the original New booking / Look
// up split) and are hidden individually by init() if the tenant hasn't set
// that content, so a tenant with nothing to show there looks exactly like
// the page did before this change.
const PB_TABS = {
  about: { tab: 'tabAbout', view: 'aboutView' },
  new: { tab: 'tabNew', view: 'bookingAppView' },
  lookup: { tab: 'tabLookup', view: 'lookupView' },
  offers: { tab: 'tabOffers', view: 'offersView' },
  announcements: { tab: 'tabAnnouncements', view: 'announcementsView' },
  hours: { tab: 'tabHours', view: 'hoursView' },
};

function setMode(mode) {
  Object.entries(PB_TABS).forEach(([key, { tab, view }]) => {
    const isActive = key === mode;
    pbEls[tab].classList.toggle('active', isActive);
    pbEls[tab].setAttribute('aria-selected', String(isActive));
    pbEls[view].classList.toggle('hidden', !isActive);
  });
  // The recap sidebar only means anything for the booking flow — hide it
  // for every other tab so those views aren't left with a stale or
  // pointless "Not selected yet" card beside them.
  pbEls.bookingSide.classList.toggle('hidden', mode !== 'new');
}
pbEls.tabAbout.addEventListener('click', () => setMode('about'));
pbEls.tabNew.addEventListener('click', () => setMode('new'));
pbEls.tabLookup.addEventListener('click', () => setMode('lookup'));
pbEls.tabOffers.addEventListener('click', () => setMode('offers'));
pbEls.tabAnnouncements.addEventListener('click', () => setMode('announcements'));
pbEls.tabHours.addEventListener('click', () => setMode('hours'));

let services = [];
let selectedServiceId = null;
let slotsByDay = new Map();
let selectedDayKey = null;
let selectedSlot = null;
// Set from the business info response in init() below. Every date/time
// shown on this page — day tabs, slot buttons, the chosen-slot summary, and
// the confirmation screen — must be rendered in the *business's* timezone,
// not the customer's device/browser timezone. Otherwise a customer whose
// phone clock is set to a different zone (travelling, misconfigured, etc.)
// sees a shifted time and can show up at the wrong hour. tzDateKey() is the
// same helper the admin dashboard/calendar already use for this.
let tenantTimezone = null;

function dayKey(iso) {
  // Bucket by calendar day *in the business's timezone*, not the browser's.
  return tzDateKey(new Date(iso), tenantTimezone);
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
      // Format from an actual slot's timestamp in this group (with an
      // explicit timeZone), rather than re-parsing the "YYYY-MM-DD" key as
      // a Date — reconstructing from the key string treats it as UTC
      // midnight, which can roll to the previous calendar day once
      // formatted back out for timezones west of UTC.
      const sampleIso = slotsByDay.get(key)[0].scheduled_at;
      const d = new Date(sampleIso);
      const dow = d.toLocaleDateString(undefined, { weekday: 'short', timeZone: tenantTimezone });
      const md = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: tenantTimezone });
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
      // hour12 forced explicitly — without it, toLocaleTimeString falls back
      // to the device's clock-format setting (many Android phones default
      // to 24-hour time regardless of locale), which is why slots showed
      // "14:00" on mobile but "2:00 PM" on desktop for the same booking.
      // timeZone forced to the business's timezone for the same reason —
      // otherwise the customer's own device clock/timezone decides what
      // hour is shown, which can silently disagree with the business's
      // actual opening hours.
      const t = new Date(s.scheduled_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: tenantTimezone });
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
  const { date, time } = fmtDateTime(selectedSlot.iso, tenantTimezone);
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
  const { date, time } = fmtDateTime(selectedSlot.iso, tenantTimezone);
  const svcName = selectedServiceId ? services.find((s) => s.id === selectedServiceId)?.name : null;
  const confirmMsg =
    `Book this appointment?\n\n` +
    `${body.customer_name}${svcName ? ' — ' + svcName : ''}\n` +
    `${body.customer_phone}\n` +
    `${date} at ${time} (${selectedSlot.duration} min)`;
  if (!(await confirmDialog(confirmMsg))) return;
  const btn = document.getElementById('b-submit');
  btn.disabled = true;
  try {
    const booking = await api('/appointments', { method: 'POST', body });
    pbEls.confRef.textContent = booking.booking_ref;
    const { date, time } = fmtDateTime(booking.scheduled_at, tenantTimezone);
    pbEls.confWhen.textContent = `${date} at ${time} — ${statusLabel(booking.status)}`;
    pbEls.confDetails.textContent = `${booking.customer_name} · ${body.customer_phone}`;
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
    const { date, time } = fmtDateTime(booking.scheduled_at, tenantTimezone);
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
        if (!(await confirmDialog(details, { danger: true }))) return;
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

/* ---- Business info: Offers / Announcements / Hours tabs + Policy toggle - */
// Offers, Announcements, and Business Hours are the 3rd/4th/5th tabs next
// to "New booking" / "Look up / cancel" (see PB_TABS above). Cancellation
// Policy stays in the small collapsed toggle beneath the business name —
// it's read far less often than the other three, so it doesn't need a full
// tab of its own. Each piece — the three tabs and the toggle — is shown
// only if the tenant has actually set that content; a tenant with nothing
// filled in sees exactly the two original tabs, unchanged.
function renderWorkingHoursTable(days) {
  const rows = days
    .map((d) => {
      const value = !d.is_open
        ? 'Closed'
        : d.windows.length > 0
        ? d.windows.join(', ')
        : 'Hours not set';
      return `<div class="hours-row"><span class="hours-day">${escapeHtml(d.day)}</span><span class="hours-range">${escapeHtml(value)}</span></div>`;
    })
    .join('');
  return `<div class="hours-table">${rows}</div>`;
}

function renderTextContent(text) {
  return `<p>${escapeHtml(text).replace(/\n/g, '<br>')}</p>`;
}

function renderInfoTabs(info) {
  if (info.about) {
    pbEls.aboutContent.innerHTML = renderTextContent(info.about);
    pbEls.tabAbout.classList.remove('hidden');
  }
  if (info.offers) {
    pbEls.offersContent.innerHTML = renderTextContent(info.offers);
    pbEls.tabOffers.classList.remove('hidden');
  }
  if (info.announcements) {
    pbEls.announcementsContent.innerHTML = renderTextContent(info.announcements);
    pbEls.tabAnnouncements.classList.remove('hidden');
  }
  if (info.working_hours && info.working_hours.length > 0) {
    pbEls.hoursContent.innerHTML = renderWorkingHoursTable(info.working_hours);
    pbEls.tabHours.classList.remove('hidden');
  }
}

function renderCancellationPolicyToggle(info) {
  if (!info.cancellation_policy) {
    pbEls.businessInfo.classList.add('hidden');
    pbEls.businessInfo.innerHTML = '';
    return;
  }
  pbEls.businessInfo.innerHTML = `
    <details class="info-toggle">
      <summary>\u{1F4C4} Cancellation Policy</summary>
      <div class="info-panel">${renderTextContent(info.cancellation_policy)}</div>
    </details>`;
  pbEls.businessInfo.classList.remove('hidden');
}

async function init() {
  try {
    const info = await api('');
    // Must be set before loadSlots()/renderDayTabs()/renderSlotGrid() run,
    // since all of them format times in this zone.
    tenantTimezone = info.timezone;
    pbEls.loadError.classList.add('hidden');
    pbEls.root.classList.remove('hidden');
    pbEls.bizName.textContent = info.name;
    renderInfoTabs(info);
    renderCancellationPolicyToggle(info);
    // Open on About when the tenant has written one, so a first-time
    // visitor gets context before being asked to pick a service. With no
    // About set this resolves to 'new', which is exactly the state the
    // markup already ships in — so those tenants see no change at all.
    setMode(info.about ? 'about' : 'new');
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
