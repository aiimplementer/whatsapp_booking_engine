const staffEls = {
  newBtn: document.getElementById('new-staff-btn'),
  panel: document.getElementById('new-staff-panel'),
  form: document.getElementById('staff-form'),
  cancel: document.getElementById('staff-cancel'),
  banner: document.getElementById('staff-banner'),
  skeleton: document.getElementById('staff-skeleton'),
  table: document.getElementById('staff-table'),
  tbody: document.getElementById('staff-tbody'),
};

const myUserId = (() => {
  // Decode the JWT payload just to know "which row is me" for UI purposes —
  // not used for anything security-sensitive, the server checks that itself.
  try {
    const token = Api.getAccessToken();
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.sub;
  } catch {
    return null;
  }
})();

staffEls.newBtn.addEventListener('click', () => {
  staffEls.panel.hidden = false;
});
staffEls.cancel.addEventListener('click', () => {
  staffEls.panel.hidden = true;
  staffEls.form.reset();
});

async function loadStaff() {
  staffEls.skeleton.hidden = false;
  staffEls.table.classList.add('hidden');
  try {
    const staff = await Api.get('/api/v1/tenants/me/users');
    staffEls.skeleton.hidden = true;
    staffEls.tbody.innerHTML = staff
      .map(
        (u) => `
      <tr data-id="${u.id}">
        <td>
          ${escapeHtml(u.email)}${u.id === myUserId ? ' <span class="muted">(you)</span>' : ''}
          ${u.is_owner ? ' <span class="stamp" title="Account owner — role is locked">Owner</span>' : ''}
        </td>
        <td>
          ${
            u.is_owner
              ? `<span class="stamp stamp-role-admin">Admin</span>`
              : `<select class="role-select" data-id="${u.id}" style="width:auto;">
              <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option>
              <option value="staff" ${u.role === 'staff' ? 'selected' : ''}>Staff</option>
              <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>Viewer</option>
            </select>`
          }
        </td>
        <td class="muted">${new Date(u.created_at).toLocaleDateString()}</td>
        <td>${
          u.is_owner
            ? ''
            : `<button class="btn btn-small btn-danger" data-remove="${u.id}">Remove</button>`
        }</td>
      </tr>`
      )
      .join('');
    staffEls.table.classList.remove('hidden');
  } catch (err) {
    staffEls.skeleton.hidden = true;
    toast(err.detail || err.message, 'error');
  }
}

staffEls.tbody.addEventListener('change', async (e) => {
  const select = e.target.closest('.role-select');
  if (!select) return;
  try {
    await Api.patch(`/api/v1/tenants/me/users/${select.dataset.id}`, { role: select.value });
    toast('Role updated');
  } catch (err) {
    toast(err.detail || err.message, 'error');
    loadStaff();
  }
});

staffEls.tbody.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-remove]');
  if (!btn) return;
  if (!confirm('Remove this staff member? They will no longer be able to log in.')) return;
  try {
    await Api.del(`/api/v1/tenants/me/users/${btn.dataset.remove}`);
    toast('Staff member removed');
    loadStaff();
  } catch (err) {
    toast(err.detail || err.message, 'error');
  }
});

staffEls.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner(staffEls.banner, null);
  const body = {
    email: document.getElementById('staff-email').value.trim(),
    role: document.getElementById('staff-role').value,
    password: document.getElementById('staff-password').value,
  };
  const btn = document.getElementById('staff-submit');
  btn.disabled = true;
  try {
    await Api.post('/api/v1/tenants/me/users', body);
    toast('Staff member added');
    staffEls.form.reset();
    staffEls.panel.hidden = true;
    loadStaff();
  } catch (err) {
    showBanner(staffEls.banner, err.detail || err.message);
  } finally {
    btn.disabled = false;
  }
});

loadStaff();
