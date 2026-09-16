from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import engine
from app.routers import appointments, appointments_slots_endpoint, auth, customers, platform_admin, public, scheduling, telegram, tenants, whatsapp
from app import web

app = FastAPI(title="WhatsApp Appointment Booking Engine")

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(scheduling.router)
# appointments_slots_endpoint MUST be included before appointments: both
# routers share the "/api/v1/appointments" prefix, and appointments.py
# defines a catch-all "GET /{appointment_id}". Routes are matched in
# registration order, so if that catch-all is registered first, a request
# to "/api/v1/appointments/available-slots" matches it before ever reaching
# the real "/available-slots" route below — Starlette treats
# "available-slots" as the appointment_id, which then fails UUID
# validation with a 422. Registering the literal route first avoids the
# collision entirely.
app.include_router(appointments_slots_endpoint.router)
app.include_router(appointments.router)
app.include_router(customers.router)
app.include_router(public.router)
app.include_router(whatsapp.router)
app.include_router(telegram.router)
app.include_router(platform_admin.auth_router)
app.include_router(platform_admin.router)

# Server-rendered UI (landing page, admin dashboard, public booking page).
# Pure template shells — they call the JSON API above from the browser.
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web.router)


@app.get("/health")
async def health_check():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}

@app.head("/health")
async def health_head():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

# Not yet built:
# - The nightly job that populates `available_slots` as a read cache. It
#   should call app.services.slots.compute_available_slots() rather than
#   re-implementing the logic — see that module's docstring.