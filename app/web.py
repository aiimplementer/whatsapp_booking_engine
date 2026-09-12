"""Server-rendered pages.

These routes only ever render a template shell — all real data (auth,
appointments, scheduling, tenant info) is fetched client-side from the
existing JSON API under /api/v1/* via static/js/api.js. That keeps this
router free of any DB/session dependencies of its own.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.platform_admin_auth import require_platform_admin

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})


@router.get("/admin/signup")
async def admin_signup(request: Request):
    return templates.TemplateResponse(request, "admin/signup.html", {})


@router.get("/admin/login")
async def admin_login(request: Request):
    return templates.TemplateResponse(request, "admin/login.html", {})


@router.get("/admin")
async def admin_dashboard(request: Request):
    return templates.TemplateResponse(request, "admin/dashboard.html", {"active_nav": "dashboard"})


@router.get("/admin/calendar")
async def admin_calendar(request: Request):
    return templates.TemplateResponse(request, "admin/calendar.html", {"active_nav": "calendar"})


@router.get("/admin/customers")
async def admin_customers(request: Request):
    return templates.TemplateResponse(request, "admin/customers.html", {"active_nav": "customers"})


@router.get("/admin/services")
async def admin_services(request: Request):
    return templates.TemplateResponse(request, "admin/services.html", {"active_nav": "services"})


@router.get("/admin/scheduling")
async def admin_scheduling(request: Request):
    return templates.TemplateResponse(request, "admin/scheduling.html", {"active_nav": "scheduling"})


@router.get("/admin/staff")
async def admin_staff(request: Request):
    return templates.TemplateResponse(request, "admin/staff.html", {"active_nav": "staff"})


@router.get("/admin/settings")
async def admin_settings(request: Request):
    return templates.TemplateResponse(request, "admin/settings.html", {"active_nav": "settings"})


@router.get("/platform-admin")
async def platform_admin_console(request: Request, _operator: str = Depends(require_platform_admin)):
    # Basic-auth-gated at the page level too (not just the API it calls), so
    # the console's markup itself never reaches a browser that hasn't
    # authenticated — same credentials, browser caches them for the API
    # calls the page makes afterwards.
    return templates.TemplateResponse(request, "admin/platform_console.html", {})


@router.get("/book/{tenant_slug}")
async def public_booking(request: Request, tenant_slug: str):
    return templates.TemplateResponse(request, "public/book.html", {"tenant_slug": tenant_slug})
