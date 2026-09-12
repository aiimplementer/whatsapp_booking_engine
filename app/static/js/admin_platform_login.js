/* Platform admin login. Deliberately not using the `Api` helper in api.js —
 * that object's session lives under wb_access_token/wb_refresh_token/etc,
 * scoped to a tenant login. The platform operator isn't a tenant, so its
 * token lives under its own key, set here and read by
 * admin_platform_console.js. */

const PLATFORM_ADMIN_TOKEN_KEY = "wb_platform_admin_token";
const PLATFORM_ADMIN_USERNAME_KEY = "wb_platform_admin_username";

// If already logged in, skip straight to the console.
if (localStorage.getItem(PLATFORM_ADMIN_TOKEN_KEY)) {
  window.location.href = "/platform-admin";
}

document.getElementById("platform-login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const banner = document.getElementById("banner");
  showBanner(banner, null);
  const btn = document.getElementById("submit-btn");
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  btn.disabled = true;
  btn.textContent = "Logging in…";
  try {
    const res = await fetch("/api/v1/platform-admin/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      throw new Error((data && data.detail) || `Login failed (${res.status})`);
    }
    localStorage.setItem(PLATFORM_ADMIN_TOKEN_KEY, data.access_token);
    localStorage.setItem(PLATFORM_ADMIN_USERNAME_KEY, data.username || username);
    window.location.href = "/platform-admin";
  } catch (err) {
    showBanner(banner, err.message || "Something went wrong");
    btn.disabled = false;
    btn.textContent = "Log in";
  }
});
