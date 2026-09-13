/* Shared helpers for talking to the JSON API from server-rendered pages.
 * Tokens live in localStorage; every admin page includes this before its
 * own page script. */

const Api = (() => {
  const ACCESS_KEY = "wb_access_token";
  const REFRESH_KEY = "wb_refresh_token";
  const TENANT_SLUG_KEY = "wb_tenant_slug";
  const TENANT_NAME_KEY = "wb_tenant_name";
  const TENANT_TIMEZONE_KEY = "wb_tenant_timezone";
  const ROLE_KEY = "wb_role";
  const EMAIL_KEY = "wb_email";

  function getAccessToken() { return localStorage.getItem(ACCESS_KEY); }
  function getRefreshToken() { return localStorage.getItem(REFRESH_KEY); }
  function getTenantSlug() { return localStorage.getItem(TENANT_SLUG_KEY) || ""; }
  function getTenantName() { return localStorage.getItem(TENANT_NAME_KEY) || ""; }
  // Falls back to Asia/Kolkata (the platform default for new tenants — see
  // SignupRequest.timezone) rather than the viewer's own browser timezone,
  // so a page can never silently drift to "wherever the admin happens to be".
  function getTenantTimezone() { return localStorage.getItem(TENANT_TIMEZONE_KEY) || "Asia/Kolkata"; }
  function getRole() { return localStorage.getItem(ROLE_KEY) || ""; }
  function getEmail() { return localStorage.getItem(EMAIL_KEY) || ""; }

  function setSession({ access_token, refresh_token, tenant_slug, tenant_name, tenant_timezone, role, email }) {
    if (access_token) localStorage.setItem(ACCESS_KEY, access_token);
    if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token);
    if (tenant_slug) localStorage.setItem(TENANT_SLUG_KEY, tenant_slug);
    if (tenant_name) localStorage.setItem(TENANT_NAME_KEY, tenant_name);
    if (tenant_timezone) localStorage.setItem(TENANT_TIMEZONE_KEY, tenant_timezone);
    if (role) localStorage.setItem(ROLE_KEY, role);
    if (email) localStorage.setItem(EMAIL_KEY, email);
  }

  function clearSession() {
    [ACCESS_KEY, REFRESH_KEY, TENANT_SLUG_KEY, TENANT_NAME_KEY, TENANT_TIMEZONE_KEY, ROLE_KEY, EMAIL_KEY].forEach((k) =>
      localStorage.removeItem(k)
    );
  }

  function isLoggedIn() { return !!getAccessToken(); }

  function requireAuth() {
    if (!isLoggedIn()) {
      window.location.href = "/admin/login";
      return false;
    }
    return true;
  }

  async function refreshAccessToken() {
    const refresh_token = getRefreshToken();
    if (!refresh_token) return false;
    try {
      const res = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      setSession({ access_token: data.access_token });
      return true;
    } catch {
      return false;
    }
  }

  // Core request helper. Adds the bearer token, retries once on 401 via
  // refresh, and throws an Error with a .status and parsed .detail on
  // non-2xx so callers can show a useful message.
  async function request(path, { method = "GET", body, auth = true, retry = true } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (auth) {
      const token = getAccessToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }
    const res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (res.status === 401 && auth && retry) {
      const refreshed = await refreshAccessToken();
      if (refreshed) return request(path, { method, body, auth, retry: false });
      clearSession();
      window.location.href = "/admin/login";
      throw new Error("Session expired");
    }

    if (res.status === 204) return null;

    let data = null;
    const text = await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch { data = text; }
    }

    if (!res.ok) {
      const detail =
        (data && typeof data === "object" && "detail" in data && data.detail) ||
        (typeof data === "string" ? data : `Request failed (${res.status})`);
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return data;
  }

  const get = (path) => request(path, { method: "GET" });
  const post = (path, body) => request(path, { method: "POST", body });
  const patch = (path, body) => request(path, { method: "PATCH", body });
  const put = (path, body) => request(path, { method: "PUT", body });
  const del = (path) => request(path, { method: "DELETE" });

  return {
    getAccessToken, getRefreshToken, getTenantSlug, getTenantName, getTenantTimezone, getRole, getEmail,
    setSession, clearSession, isLoggedIn, requireAuth,
    request, get, post, patch, put, del,
  };
})();

/* ---- small shared UI helpers ------------------------------------------- */

function toast(message, kind = "ok") {
  let stack = document.getElementById("toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toast-stack";
    document.body.appendChild(stack);
  }
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function showBanner(el, message) {
  if (!el) return;
  if (!message) { el.hidden = true; el.textContent = ""; return; }
  el.textContent = message;
  el.hidden = false;
}

function fmtDateTime(iso, timeZone) {
  const d = new Date(iso);
  const opts = timeZone ? { timeZone } : {};
  return {
    date: d.toLocaleDateString(undefined, {
      weekday: "short", month: "short", day: "numeric", year: "numeric", ...opts,
    }),
    // hour12 forced explicitly — otherwise this falls back to the device's
    // clock-format setting (e.g. Android phones set to 24-hour time),
    // which is why times showed as "14:00" on some phones but "2:00 PM"
    // on desktop for the same appointment.
    time: d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", hour12: true, ...opts }),
  };
}

// YYYY-MM-DD for `d` as a calendar date *in `timeZone`* — used to bucket
// appointments by the tenant's local day rather than the viewer's, since a
// UTC instant can fall on different calendar dates in different zones.
function tzDateKey(d, timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(d);
}

// Turns a "YYYY-MM-DD" key back into a UTC-midnight Date purely so two keys
// can be subtracted to get a whole number of days apart, independent of DST.
function dateKeyToUtc(key) {
  const [y, m, d] = key.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function statusLabel(status) {
  return String(status || "").replaceAll("_", " ").toLowerCase();
}