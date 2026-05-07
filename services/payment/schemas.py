from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class PaymentResponse(BaseModel):
    id: UUID
    order_id: str
    user_id: str
    amount: float
    status: str
    stripe_session_id: Optional[str] = None

    class Config:
        from_attributes = True

class CheckoutUrlResponse(BaseModel):
    checkout_url: str