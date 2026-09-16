/**
 * Shared slot picker component for both public booking page and admin dashboard.
 * 
 * Both the public and staff interfaces use the same slot calculation logic
 * (services/slots.py::compute_available_slots), so this component ensures
 * they show identical available times.
 * 
 * Usage (admin dashboard):
 *   const picker = new SlotPicker({
 *     container: '#slot-picker-container',
 *     apiEndpoint: '/api/v1/appointments/available-slots',
 *     tenantTimezone: 'Asia/Kolkata',
 *     onSlotSelected: (slot) => { ... },
 *     onError: (err) => { ... }
 *   });
 * 
 * Usage (public booking page):
 *   const picker = new SlotPicker({
 *     container: '#slot-picker-container',
 *     apiEndpoint: '/api/v1/public/{tenant_slug}/available-slots',
 *     tenantTimezone: 'Asia/Kolkata',
 *     onSlotSelected: (slot) => { ... }
 *   });
 */

class SlotPicker {
  constructor(opts) {
    this.container = typeof opts.container === 'string'
      ? document.querySelector(opts.container)
      : opts.container;
    
    if (!this.container) {
      throw new Error('SlotPicker: container not found');
    }
    
    this.apiEndpoint = opts.apiEndpoint;
    this.tenantTimezone = opts.tenantTimezone;
    // Admin dashboard hits a staff-only endpoint that requires a bearer
    // token; the public booking page hits an anonymous endpoint and must
    // NOT send one. Callers opt in explicitly rather than this component
    // guessing from the URL.
    this.auth = !!opts.auth;
    this.onSlotSelected = opts.onSlotSelected || (() => {});
    this.onError = opts.onError || (() => {});
    this.onLoadingChange = opts.onLoadingChange || (() => {});
    
    // State
    this.slotsByDay = new Map();
    this.selectedDayKey = null;
    this.selectedSlot = null;
    this.isLoading = false;
    
    // DOM elements (created in render())
    this.els = {};
    
    // Create the UI
    this.render();
  }
  
  render() {
    this.container.innerHTML = `
      <div class="slot-picker" data-timezone="${this.tenantTimezone}">
        <!-- Day tabs -->
        <div class="day-tabs-wrap">
          <button type="button" class="day-tabs-nav day-tabs-prev" 
                  id="slot-picker-prev" aria-label="Show earlier days">&lsaquo;</button>
          <div class="day-tabs" id="slot-picker-day-tabs"></div>
          <button type="button" class="day-tabs-nav day-tabs-next"
                  id="slot-picker-next" aria-label="Show later days">&rsaquo;</button>
        </div>
        
        <!-- Slot grid -->
        <div class="slot-grid" id="slot-picker-grid"></div>
        <div id="slot-picker-no-slots" class="muted hidden">No times available on this day — try another.</div>
        <div id="slot-picker-loading" class="muted hidden">Loading available times…</div>
      </div>
    `;
    
    this.els = {
      container: this.container.querySelector('.slot-picker'),
      dayTabs: this.container.querySelector('#slot-picker-day-tabs'),
      dayTabsPrev: this.container.querySelector('#slot-picker-prev'),
      dayTabsNext: this.container.querySelector('#slot-picker-next'),
      slotGrid: this.container.querySelector('#slot-picker-grid'),
      noSlots: this.container.querySelector('#slot-picker-no-slots'),
      loading: this.container.querySelector('#slot-picker-loading'),
    };
    
    this.setupEventListeners();
  }
  
  setupEventListeners() {
    // Day tabs navigation
    this.els.dayTabs.addEventListener('click', (e) => {
      const tab = e.target.closest('.day-tab');
      if (!tab) return;
      this.selectedDayKey = tab.dataset.day;
      this.renderDayTabs();
      this.renderSlotGrid();
    });
    
    // Prev/next buttons for day tabs
    this.els.dayTabsPrev.addEventListener('click', () => this.scrollDayTabs(-1));
    this.els.dayTabsNext.addEventListener('click', () => this.scrollDayTabs(1));
    
    // Slot selection
    this.els.slotGrid.addEventListener('click', (e) => {
      const btn = e.target.closest('.slot-btn');
      if (!btn) return;
      this.selectSlot(btn);
    });
    
    // Update day tabs nav state on scroll
    this.els.dayTabs.addEventListener('scroll', () => this.updateDayTabsNavState(), { passive: true });
    window.addEventListener('resize', () => this.updateDayTabsNavState());
    
    // Watch for day tabs changes (when slots are reloaded)
    new MutationObserver(() => this.updateDayTabsNavState()).observe(
      this.els.dayTabs,
      { childList: true }
    );
  }
  
  scrollDayTabs(direction) {
    const distance = this.els.dayTabs.clientWidth * 0.85;
    this.els.dayTabs.scrollBy({
      left: direction * distance,
      behavior: 'smooth'
    });
  }
  
  updateDayTabsNavState() {
    const maxScroll = this.els.dayTabs.scrollWidth - this.els.dayTabs.clientWidth;
    const atStart = this.els.dayTabs.scrollLeft <= 1;
    const atEnd = this.els.dayTabs.scrollLeft >= maxScroll - 1;
    const noOverflow = maxScroll <= 1;
    
    this.els.dayTabsPrev.disabled = atStart || noOverflow;
    this.els.dayTabsNext.disabled = atEnd || noOverflow;
  }
  
  setLoading(isLoading) {
    this.isLoading = isLoading;
    this.els.loading.classList.toggle('hidden', !isLoading);
    this.onLoadingChange(isLoading);
  }
  
  async loadSlots(serviceId = null) {
    this.setLoading(true);
    this.els.noSlots.classList.add('hidden');
    this.els.slotGrid.innerHTML = '';
    this.els.dayTabs.innerHTML = '';
    this.slotsByDay.clear();
    this.selectedDayKey = null;
    
    try {
      const qs = serviceId ? `?service_id=${encodeURIComponent(serviceId)}` : '';
      const headers = { 'Content-Type': 'application/json' };
      // Attach the staff bearer token for authenticated callers (admin
      // dashboard). Reuses Api's own getter so it always reads the same
      // localStorage key api.js writes to, and stays in sync if that
      // storage key ever changes. The public booking page passes no
      // `auth` option, so this stays anonymous there as before.
      if (this.auth && typeof Api !== 'undefined') {
        const token = Api.getAccessToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
      }
      const slots = await fetch(this.apiEndpoint + qs, {
        method: 'GET',
        headers,
      })
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        });
      
      // Group slots by day
      for (const s of slots) {
        const key = dayKeyFromIso(s.scheduled_at, this.tenantTimezone);
        if (!this.slotsByDay.has(key)) {
          this.slotsByDay.set(key, []);
        }
        this.slotsByDay.get(key).push(s);
      }
      
      if (this.slotsByDay.size === 0) {
        this.els.noSlots.classList.remove('hidden');
        this.setLoading(false);
        return;
      }
      
      // Select first day by default
      const keys = Array.from(this.slotsByDay.keys());
      this.selectedDayKey = keys[0];
      
      this.renderDayTabs();
      this.renderSlotGrid();
      this.updateDayTabsNavState();
    } catch (err) {
      this.onError(err);
    } finally {
      this.setLoading(false);
    }
  }
  
  renderDayTabs() {
    const keys = Array.from(this.slotsByDay.keys());
    
    this.els.dayTabs.innerHTML = keys
      .map((key) => {
        // Get a sample slot to format the day
        const sampleIso = this.slotsByDay.get(key)[0].scheduled_at;
        const d = new Date(sampleIso);
        const dow = d.toLocaleDateString(undefined, {
          weekday: 'short',
          timeZone: this.tenantTimezone
        });
        const md = d.toLocaleDateString(undefined, {
          month: 'short',
          day: 'numeric',
          timeZone: this.tenantTimezone
        });
        
        const isSelected = key === this.selectedDayKey ? 'selected' : '';
        
        return `<div class="day-tab ${isSelected}" data-day="${key}">
          <span class="dow">${dow}</span>${md}
        </div>`;
      })
      .join('');
  }
  
  renderSlotGrid() {
    const slots = this.slotsByDay.get(this.selectedDayKey) || [];
    
    this.els.noSlots.classList.toggle('hidden', slots.length > 0);
    
    this.els.slotGrid.innerHTML = slots
      .map((s) => {
        const t = new Date(s.scheduled_at).toLocaleTimeString(undefined, {
          hour: 'numeric',
          minute: '2-digit',
          hour12: true,
          timeZone: this.tenantTimezone
        });
        
        const isSelected = this.selectedSlot?.iso === s.scheduled_at ? 'selected' : '';
        
        return `<button type="button" class="slot-btn ${isSelected}"
                        data-iso="${s.scheduled_at}"
                        data-duration="${s.duration_minutes}">
          ${t}
        </button>`;
      })
      .join('');
  }
  
  selectSlot(btnEl) {
    document.querySelectorAll('.slot-btn').forEach((b) => b.classList.remove('selected'));
    btnEl.classList.add('selected');
    
    this.selectedSlot = {
      iso: btnEl.dataset.iso,
      duration: Number(btnEl.dataset.duration)
    };
    
    this.onSlotSelected(this.selectedSlot);
  }
  
  // Public API
  getSelectedSlot() {
    return this.selectedSlot;
  }
  
  clearSelection() {
    this.selectedSlot = null;
    document.querySelectorAll('.slot-btn').forEach((b) => b.classList.remove('selected'));
  }
}

/**
 * Helper to create a day key from an ISO datetime string.
 * Buckets slots by calendar day in the business's timezone.
 */
function dayKeyFromIso(iso, timezone) {
  return tzDateKey(new Date(iso), timezone);
}