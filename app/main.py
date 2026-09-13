from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import engine
from app.routers import appointments, auth, customers, platform_admin, public, scheduling, tenants, whatsapp
from app import web

app = FastAPI(title="WhatsApp Appointment Booking Engine")

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(scheduling.router)
app.include_router(appointments.router)
app.include_router(customers.router)
app.include_router(public.router)
app.include_router(whatsapp.router)
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


# Not yet built:
# - The nightly job that populates `available_slots` as a read cache. It
#   should call app.services.slots.compute_available_slots() rather than
#   re-implementing the logic — see that module's docstring.
