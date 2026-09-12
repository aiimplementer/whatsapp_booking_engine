import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_tenant_db, require_role
from app.models.appointment import Appointment
from app.models.tenant import TenantUser
from app.routers.appointments import INACTIVE_STATUSES
from app.schemas.customer import CustomerOut, PaginatedCustomers

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])

SORT_COLUMNS = {"visits", "recent", "name"}

# Same '+'-tolerant identity as the phone search in appointments.py — two
# bookings for "+919876543210" and "919876543210" are the same customer.
# There's no customers table (this whole feature is a derived view over
# appointments, not a stored entity — see schemas/customer.py), so this
# normalized phone is the only thing that can stand in for a customer id.
_norm_phone = func.replace(Appointment.customer_phone, "+", "")


@router.get("", response_model=PaginatedCustomers)
async def list_customers(
    search: str | None = Query(default=None, description="Matches name or phone"),
    sort: str = Query(default="visits", description="visits | recent | name"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    if sort not in SORT_COLUMNS:
        sort = "visits"

    # One row per phone with its counts, built as a GROUP BY...
    agg = (
        select(
            _norm_phone.label("norm_phone"),
            func.count().label("total_appointments"),
            func.count().filter(Appointment.status == "COMPLETED").label("completed_appointments"),
            func.min(Appointment.scheduled_at).label("first_visit_at"),
            func.max(Appointment.scheduled_at).label("last_visit_at"),
        )
        .where(Appointment.tenant_id == user.tenant_id)
        .group_by(_norm_phone)
        .cte("customer_agg")
    )

    # ...joined against the single most-recent appointment per phone, for
    # whatever name/phone-formatting that booking used — picked with a
    # window function rather than MAX()/a second GROUP BY, since we need both
    # columns (phone, name) to come from the *same* row.
    ranked = select(
        _norm_phone.label("norm_phone"),
        Appointment.customer_phone.label("display_phone"),
        Appointment.customer_name.label("customer_name"),
        func.row_number()
        .over(partition_by=_norm_phone, order_by=Appointment.created_at.desc())
        .label("rn"),
    ).where(Appointment.tenant_id == user.tenant_id)
    ranked = ranked.subquery("customer_ranked")
    latest = select(ranked).where(ranked.c.rn == 1).cte("customer_latest")

    base = select(
        latest.c.display_phone.label("phone"),
        latest.c.customer_name.label("name"),
        agg.c.total_appointments,
        agg.c.completed_appointments,
        agg.c.first_visit_at,
        agg.c.last_visit_at,
    ).select_from(agg.join(latest, agg.c.norm_phone == latest.c.norm_phone))

    if search:
        like = f"%{search.strip()}%"
        base = base.where(
            or_(
                latest.c.customer_name.ilike(like),
                latest.c.display_phone.ilike(like),
            )
        )

    filtered = base.subquery("customer_filtered")
    total = await db.scalar(select(func.count()).select_from(filtered))

    order_col = {
        "visits": filtered.c.total_appointments.desc(),
        "recent": filtered.c.last_visit_at.desc(),
        "name": filtered.c.name.asc(),
    }[sort]

    page_query = (
        select(filtered)
        .order_by(order_col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(page_query)
    items = [CustomerOut.model_validate(dict(row)) for row in result.mappings().all()]

    return PaginatedCustomers(
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil((total or 0) / page_size)),
    )
