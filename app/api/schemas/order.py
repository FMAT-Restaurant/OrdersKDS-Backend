"""Request schemas of order endpoints.

Satisfies: RNF-03, RF-15 (Guidelines §5.7: no mass assignment)
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.order_status import OrderStatus


class MarkReadyRequest(BaseModel):
    """Optional body of the "mark as ready" endpoints.

    The endpoints take no input from the body: ``user_id`` and ``role`` come from the JWT
    and the target status is decided by the backend. Any field sent (``userId``,
    ``status``, ...) is rejected with ``422`` instead of silently ignored.
    """

    model_config = ConfigDict(extra="forbid")


class ConfirmOrderResponse(BaseModel):
    """Current order state returned by the idempotent confirmation endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    order_id: UUID = Field(alias="orderId")
    status: OrderStatus
    confirmed_at: datetime | None = Field(alias="confirmedAt")
