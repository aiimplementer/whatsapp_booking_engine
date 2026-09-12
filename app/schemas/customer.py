from datetime import datetime

from pydantic import BaseModel


class CustomerOut(BaseModel):
    """One row of the customer master — derived from appointments, not a
    stored entity. Name is whatever was on that phone's most recent
    booking, since a customer can be re-entered slightly differently across
    visits (a typoed name) and there's no customer record of their own to
    hold a canonical version."""

    phone: str
    name: str
    total_appointments: int
    completed_appointments: int
    first_visit_at: datetime
    last_visit_at: datetime

    model_config = {"from_attributes": True}


class PaginatedCustomers(BaseModel):
    items: list[CustomerOut]
    total: int
    page: int
    page_size: int
    pages: int
