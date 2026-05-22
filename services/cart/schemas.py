from pydantic import BaseModel
from typing import List

class CartItem(BaseModel):
    product_id: str
    quantity: int

class CartUpdate(BaseModel):
    items: List[CartItem]

class CartResponse(BaseModel):
    user_id: str
    items: List[CartItem]