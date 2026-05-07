from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from contextlib import asynccontextmanager
import jwt
from jwt.exceptions import InvalidTokenError
import uuid

from schemas import ReviewCreate, ReviewResponse, ProductReviewsResponse
from models import Review
from database import get_db, engine, Base
from config import settings

security = HTTPBearer()

async def get_current_user_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[settings.algorithm])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return {"user_id": user_id, "role": role}
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="FastCart Review API", lifespan=lifespan)



@app.get("/reviews/health")
async def health_check():
    return {"status": "Review Service is healthy"}

@app.post("/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_review(
    review: ReviewCreate, 
    user_data: dict = Depends(get_current_user_token), 
    db: AsyncSession = Depends(get_db)
):
    new_review = Review(
        user_id=user_data["user_id"],
        product_id=review.product_id,
        rating=review.rating,
        comment=review.comment
    )
    
    db.add(new_review)
    try:
        await db.commit()
        await db.refresh(new_review)
        return new_review
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="You have already reviewed this product.")


@app.get("/reviews/product/{product_id}", response_model=ProductReviewsResponse)
async def get_product_reviews(product_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Review).where(Review.product_id == product_id))
    reviews = result.scalars().all()
    
    total_reviews = len(reviews)
    average_rating = 0.0
    
    if total_reviews > 0:
        total_stars = sum(r.rating for r in reviews)
        average_rating = round(total_stars / total_reviews, 1)

    return ProductReviewsResponse(
        product_id=product_id,
        average_rating=average_rating,
        total_reviews=total_reviews,
        reviews=reviews
    )


@app.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: uuid.UUID, 
    user_data: dict = Depends(get_current_user_token), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalars().first()
    
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
        
    if review.user_id != user_data["user_id"] and user_data["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to delete this review")
        
    await db.delete(review)
    await db.commit()
    return None