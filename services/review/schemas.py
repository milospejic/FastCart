from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

class ReviewCreate(BaseModel):
    product_id: str
    rating: int = Field(..., ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = None

class ReviewResponse(BaseModel):
    id: UUID
    user_id: str
    product_id: str
    rating: int
    comment: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ProductReviewsResponse(BaseModel):
    product_id: str
    average_rating: float
    total_reviews: int
    reviews: List[ReviewResponse]