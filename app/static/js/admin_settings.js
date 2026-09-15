const setEls = {
  skeleton: document.getElementById('skeleton'),
  form: document.getElementById('settings-form'),
  banner: document.getElementById('settings-banner'),
  ok: document.getElementById('settings-ok'),
  name: document.getElementById('s-name'),
  slug: document.getElementById('s-slug'),
  phone: document.getElementById('s-phone'),
  timezone: document.getElementById('s-timezone'),
  whatsapp: document.getElementById('s-whatsapp'),
  webBooking: document.getElementById('s-web-booking'),
  cancellationPolicy: document.getElementById('s-cancellation-policy'),
  offers: document.getElementById('s-offers'),
};

async function loadTenant() {
  try {
    const tenant = await Api.get('/api/v1/tenants/me');
    setEls.skeleton.hidden = true;
    setEls.form.classList.remove('hidden');
    setEls.name.value = tenant.name;
    setEls.slug.value = tenant.slug;
    setEls.phone.value = tenant.phone;
    populateTimezoneSelect(setEls.timezone, tenant.timezone);
    setEls.whatsapp.value = tenant.whatsapp_number || '';
    setEls.webBooking.checked = tenant.web_booking_enabled;
    setEls.cancellationPolicy.value = tenant.cancellation_policy || '';
    setEls.offers.value = tenant.offers || '';
    Api.setSession({ tenant_name: tenant.name });
  } catch (err) {
    setEls.skeleton.hidden = true;
    showBanner(setEls.banner, err.detail || err.message);
  }
}

setEls.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(setEls.banner, null);
  showBanner(setEls.ok, null);
  const body = {
    name: setEls.name.value.trim(),
    phone: setEls.phone.value.trim(),
    timezone: setEls.timezone.value.trim(),
    whatsapp_number: setEls.whatsapp.value.trim() || null,
    web_booking_enabled: setEls.webBooking.checked,
    cancellation_policy: setEls.cancellationPolicy.value.trim() || null,
    offers: setEls.offers.value.trim() || null,
  };
  const btn = document.getElementById('settings-submit');
  btn.disabled = true;
  try {
    const tenant = await Api.patch('/api/v1/tenants/me', body);
    Api.setSession({ tenant_name: tenant.name });
    showBanner(setEls.ok, 'Saved.');
  } catch (err) {
    showBanner(setEls.banner, err.detail || err.message);
  } finally {
    btn.disabled = false;
  }
});

loadTenant();
