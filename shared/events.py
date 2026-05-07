import uuid
from datetime import datetime, timezone
from typing import List
from pydantic import BaseModel, Field

class EventOrderItem(BaseModel):
    product_id: uuid.UUID
    quantity: int
    
class OrderCreatedEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    order_id: uuid.UUID
    user_id: uuid.UUID
    items: List[EventOrderItem]
    total_amount: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StockReservedEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    order_id: uuid.UUID
    user_id: uuid.UUID     
    total_amount: float
    status: str

class StockFailedEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    order_id: uuid.UUID
    reason: str

class PaymentProcessedEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    order_id: uuid.UUID
    status: str 
    transaction_id: str | None = None

class OrderCompletedEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    order_id: uuid.UUID
    items: List[EventOrderItem]