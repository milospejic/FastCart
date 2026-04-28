from pydantic import BaseModel
from uuid import UUID

class OrderCreate(BaseModel):
    product_id: str
    quantity: int = 1

class OrderResponse(BaseModel):
    id: UUID
    user_id: str
    product_id: str
    quantity: int
    status: str

    class Config:
        from_attributes = True