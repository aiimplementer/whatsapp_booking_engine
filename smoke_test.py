import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

BASE = "http://127.0.0.1:8000"
FAILS = []


def step(name):
    print(f"\n=== {name} ===")


def check(cond, msg):
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        FAILS.append(msg)


def req(method, path, token=None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.request(method, f"{BASE}{path}", headers=headers, **kw)
    return r


slug = f"nimbus-{uuid.uuid4().hex[:6]}"

# ---- 1. Signup -------------------------------------------------------------
step("Admin signup")
r = req(
    "POST",
    "/api/v1/auth/signup",
    json={
        "business_name": "Nimbus Hair Studio",
        "tenant_slug": slug,
        "business_email": f"hello-{slug}@example.com",
        "business_phone": "+919876500000",
        "admin_email": "owner@example.com",
        "admin_password": "supersecret123",
    },
)
check(r.status_code == 201, f"signup -> {r.status_code}: {r.text[:300]}")
tokens = r.json()
access = tokens["access_token"]
refresh = tokens["refresh_token"]

# ---- 2. /auth/me and /tenants/me -------------------------------------------
step("me + tenant")
r = req("GET", "/api/v1/auth/me", token=access)
check(r.status_code == 200 and r.json()["role"] == "admin", f"/auth/me -> {r.status_code} {r.text[:200]}")

r = req("GET", "/api/v1/tenants/me", token=access)
check(r.status_code == 200 and r.json()["slug"] == slug, f"/tenants/me -> {r.status_code} {r.text[:200]}")

# ---- 3. Refresh token --------------------------------------------------------
step("Refresh token")
r = req("POST", "/api/v1/auth/refresh", json={"refresh_token": refresh})
check(r.status_code == 200 and "access_token" in r.json(), f"refresh -> {r.status_code} {r.text[:200]}")

# ---- 4. Create a service -----------------------------------------------------
step("Create service")
r = req(
    "POST",
    "/api/v1/scheduling/services",
    token=access,
    json={"name": "Haircut", "duration_minutes": 30, "description": "Wash, cut, style"},
)
check(r.status_code == 201, f"create service -> {r.status_code}: {r.text[:300]}")
service = r.json()
service_id = service["id"]

# ---- 5. Set working hours for that service -----------------------------------
step("Upsert scheduling config")
body = {
    "service_id": service_id,
    "working_days": 62,  # Mon-Fri
    "time_slots": [
        {"day": 1, "start": "09:00", "end": "17:00"},
        {"day": 2, "start": "09:00", "end": "17:00"},
        {"day": 3, "start": "09:00", "end": "17:00"},
        {"day": 4, "start": "09:00", "end": "17:00"},
        {"day": 5, "start": "09:00", "end": "17:00"},
    ],
    "appointment_duration_minutes": 30,
    "buffer_minutes": 5,
    "advance_booking_days": 30,
}
r = req("PUT", "/api/v1/scheduling/configs", token=access, json=body)
check(r.status_code == 200, f"upsert config -> {r.status_code}: {r.text[:400]}")

r = req("GET", "/api/v1/scheduling/configs", token=access)
check(r.status_code == 200 and len(r.json()) == 1, f"list configs -> {r.status_code} {r.text[:300]}")

# ---- 6. Holiday + blocked time ------------------------------------------------
step("Holidays + blocked times")
future_holiday = (datetime.now(timezone.utc) + timedelta(days=200)).date().isoformat()
r = req("POST", "/api/v1/scheduling/holidays", token=access, json={"date": future_holiday, "reason": "Test holiday"})
check(r.status_code == 201, f"create holiday -> {r.status_code}: {r.text[:300]}")
holiday_id = r.json()["id"]

r = req("GET", "/api/v1/scheduling/holidays", token=access)
check(r.status_code == 200 and len(r.json()) == 1, f"list holidays -> {r.status_code} {r.text[:300]}")

blk_start = (datetime.now(timezone.utc) + timedelta(days=2, hours=1)).isoformat()
blk_end = (datetime.now(timezone.utc) + timedelta(days=2, hours=2)).isoformat()
r = req(
    "POST",
    "/api/v1/scheduling/blocked-times",
    token=access,
    json={"start_datetime": blk_start, "end_datetime": blk_end, "reason": "Staff break"},
)
check(r.status_code == 201, f"create blocked time -> {r.status_code}: {r.text[:300]}")

r = req("DELETE", f"/api/v1/scheduling/holidays/{holiday_id}", token=access)
check(r.status_code == 204, f"delete holiday -> {r.status_code}")

# ---- 7. Public business info + available slots --------------------------------
step("Public business info + slots (no auth)")
r = req("GET", f"/api/v1/public/{slug}")
check(r.status_code == 200 and len(r.json()["services"]) == 1, f"public info -> {r.status_code} {r.text[:300]}")

r = req("GET", f"/api/v1/public/{slug}/available-slots", params={"service_id": service_id})
check(r.status_code == 200, f"available-slots -> {r.status_code}: {r.text[:300]}")
slots = r.json()
check(len(slots) > 0, f"available-slots returned {len(slots)} slots")
if not slots:
    print("No slots available — cannot continue booking flow, dumping response:")
    print(json.dumps(slots, indent=2))
    sys.exit(1)
first_slot = slots[0]
print(f"  first slot: {first_slot}")

# ---- 8. Public booking ----------------------------------------------------------
step("Public booking (customer books)")
customer_phone = "+919876512345"
r = req(
    "POST",
    f"/api/v1/public/{slug}/appointments",
    json={
        "service_id": service_id,
        "customer_name": "Asha Patel",
        "customer_phone": customer_phone,
        "customer_email": "asha@example.com",
        "scheduled_at": first_slot["scheduled_at"],
        "notes": "First time customer",
    },
)
check(r.status_code == 201, f"public booking -> {r.status_code}: {r.text[:400]}")
booking = r.json()
booking_ref = booking["booking_ref"]
check(booking["status"] == "CONFIRMED", f"booking status is {booking['status']}")
print(f"  booking_ref: {booking_ref}")

# Double-booking the same slot should now fail with 409
step("Double-booking the same slot is rejected")
r2 = req(
    "POST",
    f"/api/v1/public/{slug}/appointments",
    json={
        "service_id": service_id,
        "customer_name": "Someone Else",
        "customer_phone": "+919876500001",
        "scheduled_at": first_slot["scheduled_at"],
    },
)
check(r2.status_code == 409, f"double booking -> {r2.status_code} (expected 409): {r2.text[:300]}")

# ---- 9. Admin sees the appointment ------------------------------------------------
step("Admin appointment list")
r = req("GET", "/api/v1/appointments", token=access)
check(r.status_code == 200 and len(r.json()) == 1, f"list appointments -> {r.status_code} {r.text[:300]}")
appt = r.json()[0]
appt_id = appt["id"]
check(appt["status"] == "CONFIRMED", f"appt status {appt['status']}")

# Filter by status + phone, like the dashboard filter form does
r = req("GET", "/api/v1/appointments", token=access, params={"status": "CONFIRMED", "customer_phone": customer_phone})
check(r.status_code == 200 and len(r.json()) == 1, f"filtered list -> {r.status_code} {r.text[:300]}")

# ---- 10. Status transitions (dashboard action buttons) ------------------------------
step("Status transitions")
r = req("PATCH", f"/api/v1/appointments/{appt_id}", token=access, json={"status": "CHECKED_IN"})
check(r.status_code == 200 and r.json()["status"] == "CHECKED_IN", f"-> CHECKED_IN: {r.status_code} {r.text[:300]}")

r = req("PATCH", f"/api/v1/appointments/{appt_id}", token=access, json={"status": "COMPLETED"})
check(r.status_code == 200 and r.json()["status"] == "COMPLETED", f"-> COMPLETED: {r.status_code} {r.text[:300]}")

# Illegal transition should 409
r = req("PATCH", f"/api/v1/appointments/{appt_id}", token=access, json={"status": "PENDING"})
check(r.status_code == 409, f"illegal transition -> {r.status_code} (expected 409)")

# ---- 11. Manual appointment creation (dashboard "New appointment" form) --------------
step("Manual appointment creation")
manual_when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
r = req(
    "POST",
    "/api/v1/appointments",
    token=access,
    json={
        "customer_name": "Walk-in Customer",
        "customer_phone": "+919876599999",
        "service_id": service_id,
        "duration_minutes": 30,
        "scheduled_at": manual_when.isoformat(),
        "notes": None,
        "status": "CONFIRMED",
    },
)
check(r.status_code == 201, f"manual create -> {r.status_code}: {r.text[:400]}")

# ---- 12. Public lookup + cancel -----------------------------------------------------
step("Public lookup + cancel")
# Book a fresh one to cancel (the first slot's appointment is already COMPLETED)
r = req("GET", f"/api/v1/public/{slug}/available-slots", params={"service_id": service_id})
slots2 = r.json()
check(len(slots2) > 0, f"second available-slots call returned {len(slots2)} slots")
slot2 = slots2[0]
r = req(
    "POST",
    f"/api/v1/public/{slug}/appointments",
    json={
        "service_id": service_id,
        "customer_name": "Cancel Test",
        "customer_phone": "+919876511111",
        "scheduled_at": slot2["scheduled_at"],
    },
)
check(r.status_code == 201, f"booking to cancel -> {r.status_code}: {r.text[:300]}")
cancel_ref = r.json()["booking_ref"]

r = req("GET", f"/api/v1/public/{slug}/appointments/{cancel_ref}", params={"customer_phone": "+919876511111"})
check(r.status_code == 200, f"lookup -> {r.status_code}: {r.text[:300]}")

# Wrong phone should 404, not leak the booking
r = req("GET", f"/api/v1/public/{slug}/appointments/{cancel_ref}", params={"customer_phone": "+910000000000"})
check(r.status_code == 404, f"lookup wrong phone -> {r.status_code} (expected 404)")

r = req("POST", f"/api/v1/public/{slug}/appointments/{cancel_ref}/cancel", params={"customer_phone": "+919876511111"})
check(r.status_code == 200 and r.json()["status"] == "CANCELLED", f"cancel -> {r.status_code}: {r.text[:300]}")

# ---- 13. Staff management ----------------------------------------------------------
step("Staff management")
r = req(
    "POST",
    "/api/v1/tenants/me/users",
    token=access,
    json={"email": "frontdesk@example.com", "role": "staff", "password": "anotherpass123"},
)
check(r.status_code == 201, f"add staff -> {r.status_code}: {r.text[:300]}")
staff_id = r.json()["id"]

r = req("GET", "/api/v1/tenants/me/users", token=access)
check(r.status_code == 200 and len(r.json()) == 2, f"list staff -> {r.status_code} {r.text[:300]}")

r = req("PATCH", f"/api/v1/tenants/me/users/{staff_id}", token=access, json={"role": "viewer"})
check(r.status_code == 200 and r.json()["role"] == "viewer", f"update role -> {r.status_code} {r.text[:300]}")

r = req("DELETE", f"/api/v1/tenants/me/users/{staff_id}", token=access)
check(r.status_code == 204, f"remove staff -> {r.status_code}")

# ---- 14. Business settings update ---------------------------------------------------
step("Business settings update")
r = req(
    "PATCH",
    "/api/v1/tenants/me",
    token=access,
    json={"name": "Nimbus Hair Studio & Spa", "web_booking_enabled": True, "whatsapp_number": "+919876500000"},
)
check(r.status_code == 200 and r.json()["name"] == "Nimbus Hair Studio & Spa", f"update tenant -> {r.status_code}: {r.text[:300]}")

# ---- 15. Login (separate from signup session) ----------------------------------------
step("Login flow")
r = req("POST", "/api/v1/auth/login", json={"tenant_slug": slug, "email": "owner@example.com", "password": "supersecret123"})
check(r.status_code == 200 and "access_token" in r.json(), f"login -> {r.status_code}: {r.text[:300]}")

r = req("POST", "/api/v1/auth/login", json={"tenant_slug": slug, "email": "owner@example.com", "password": "wrongpass"})
check(r.status_code == 401, f"login wrong password -> {r.status_code} (expected 401)")

# ---- summary ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
