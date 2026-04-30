from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    available_quantity: int = 0

class ProductResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    price: float
    available_quantity: int
    reserved_quantity: int

    class Config:
        from_attributes = True