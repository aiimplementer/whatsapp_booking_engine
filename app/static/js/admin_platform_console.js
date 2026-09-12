/* Platform admin console. Talks to /api/v1/platform-admin/* which is
 * guarded by HTTP Basic auth (not the tenant JWT system in api.js) — the
 * browser already holds those credentials from loading this page, and
 * resends them automatically for same-origin requests. */

const state = { search: "", page: 1, pageSize: 20 };

const els = {
  banner: document.getElementById("banner"),
  skeleton: document.getElementById("tenants-skeleton"),
  table: document.getElementById("tenants-table"),
  tbody: document.getElementById("tenants-tbody"),
  pagination: document.getElementById("pagination"),
  resultCount: document.getElementById("result-count"),
  search: document.getElementById("tenant-search"),
};

function buildUrl() {
  const params = new URLSearchParams({
    page: state.page,
    page_size: state.pageSize,
  });
  if (state.search) params.set("search", state.search);
  return `/api/v1/platform-admin/tenants?${params.toString()}`;
}

async function fetchTenants() {
  const res = await fetch(buildUrl(), { headers: { Accept: "application/json" } });
  if (res.status === 401) {
    // Cached Basic auth credentials were rejected/missing — reload so the
    // browser re-prompts rather than silently showing an empty console.
    window.location.reload();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error((body && body.detail) || `Request failed (${res.status})`);
  }
  return res.json();
}

function renderRow(tenant) {
  const tr = document.createElement("tr");

  const subLabel = tenant.subscription_enabled ? "Enabled" : "Disabled";
  const subClass = tenant.subscription_enabled ? "stamp-enabled" : "stamp-disabled";
  const toggleLabel = tenant.subscription_enabled ? "Disable" : "Enable";
  const toggleClass = tenant.subscription_enabled ? "btn-danger" : "btn";

  tr.innerHTML = `
    <td>${escapeHtml(tenant.name)}<div class="muted mono" style="font-size:0.78rem;">/${escapeHtml(tenant.slug)}</div></td>
    <td>${escapeHtml(tenant.email)}</td>
    <td>${escapeHtml(tenant.timezone)}</td>
    <td>${tenant.admin_email ? escapeHtml(tenant.admin_email) : '<span class="muted">— none —</span>'}</td>
    <td>${tenant.web_booking_enabled ? "Yes" : "No"}</td>
    <td><span class="stamp stamp-${escapeHtml(tenant.status)}">${statusLabel(tenant.status)}</span></td>
    <td><span class="stamp ${subClass}">${subLabel}</span></td>
    <td><button class="btn ${toggleClass} btn-small" data-tenant-id="${tenant.id}" data-next="${!tenant.subscription_enabled}">${toggleLabel}</button></td>
  `;

  tr.querySelector("button").addEventListener("click", onToggleSubscription);
  return tr;
}

async function onToggleSubscription(e) {
  const btn = e.currentTarget;
  const tenantId = btn.dataset.tenantId;
  const nextEnabled = btn.dataset.next === "true";
  const action = nextEnabled ? "enable" : "disable";

  if (!window.confirm(`${action === "enable" ? "Enable" : "Disable"} the subscription for this tenant?`)) {
    return;
  }

  btn.disabled = true;
  try {
    const res = await fetch(`/api/v1/platform-admin/tenants/${tenantId}/subscription`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: nextEnabled }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error((body && body.detail) || `Request failed (${res.status})`);
    }
    toast(`Subscription ${action}d.`, "ok");
    await loadTenants();
  } catch (err) {
    toast(err.message || "Something went wrong", "error");
    btn.disabled = false;
  }
}

function renderPagination(meta) {
  els.pagination.innerHTML = "";
  if (meta.pages <= 1) return;

  const mkBtn = (label, page, disabled, active) => {
    const b = document.createElement("button");
    b.className = `btn btn-small ${active ? "" : "btn-secondary"}`;
    b.textContent = label;
    b.disabled = disabled;
    b.addEventListener("click", () => {
      state.page = page;
      loadTenants();
    });
    return b;
  };

  els.pagination.appendChild(mkBtn("Prev", meta.page - 1, meta.page <= 1, false));
  const span = document.createElement("span");
  span.className = "muted mono";
  span.style.padding = "0 0.5rem";
  span.textContent = `Page ${meta.page} of ${meta.pages}`;
  els.pagination.appendChild(span);
  els.pagination.appendChild(mkBtn("Next", meta.page + 1, meta.page >= meta.pages, false));
}

async function loadTenants() {
  showBanner(els.banner, "");
  els.skeleton.hidden = false;
  els.table.classList.add("hidden");

  try {
    const data = await fetchTenants();
    els.tbody.innerHTML = "";
    data.items.forEach((t) => els.tbody.appendChild(renderRow(t)));
    els.resultCount.textContent = `${data.total} tenant${data.total === 1 ? "" : "s"}`;
    renderPagination(data);
    els.skeleton.hidden = true;
    els.table.classList.remove("hidden");
  } catch (err) {
    els.skeleton.hidden = true;
    showBanner(els.banner, err.message || "Couldn't load tenants");
  }
}

let searchDebounce;
els.search.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.search = els.search.value.trim();
    state.page = 1;
    loadTenants();
  }, 300);
});

loadTenants();
