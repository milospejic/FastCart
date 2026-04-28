from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
import jwt
import httpx
from jwt.exceptions import InvalidTokenError

from .schemas import OrderCreate, OrderResponse
from .models import Order
from .database import get_db, engine, Base
from .config import settings

security = HTTPBearer()

async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[settings.algorithm])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return user_id
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="FastCart Order API", description="Manages customer orders", lifespan=lifespan)

@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order: OrderCreate, 
    user_id: str = Depends(get_current_user_id), 
    db: AsyncSession = Depends(get_db)
):
    product_url = f"http://127.0.0.1:8002/products/{order.product_id}"
    deduct_url = f"http://127.0.0.1:8002/products/{order.product_id}/deduct"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(product_url)
        
        if response.status_code == 404:
            raise HTTPException(status_code=400, detail="Product does not exist!")
        elif response.status_code != 200:
            raise HTTPException(status_code=500, detail="Could not communicate with Product Service")
            
        product_data = response.json()
        if product_data["stock"] < order.quantity:
            raise HTTPException(status_code=400, detail="Not enough stock available!")

        deduct_response = await client.patch(deduct_url, json={"quantity": order.quantity})
        if deduct_response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to deduct stock")

    new_order = Order(
        user_id=user_id,
        product_id=order.product_id,
        quantity=order.quantity
    )
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    return new_order