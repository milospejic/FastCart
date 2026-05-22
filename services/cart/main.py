from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import jwt
from jwt.exceptions import InvalidTokenError
import redis.asyncio as aioredis
import json
from contextlib import asynccontextmanager

from config import settings
from schemas import CartUpdate, CartResponse

security = HTTPBearer()
redis_client: aioredis.Redis | None = None

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
    global redis_client
    redis_client = aioredis.from_url(
        f"redis://{settings.redis_host}:{settings.redis_port}",
        password=settings.redis_password,
        decode_responses=True
    )
    print("🚀 Cart Service connected to Redis cache successfully!")
    yield
    if redis_client:
        await redis_client.close()

app = FastAPI(title="FastCart Cart API", description="Service for managing shopping carts", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, 
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/cart/health")
async def health_check():
    return {"status": "Cart Service is healthy"}

@app.get("/cart", response_model=CartResponse)
async def get_cart(user_id: str = Depends(get_current_user_id)):
    cart_data = await redis_client.get(f"cart:{user_id}")
    if not cart_data:
        return CartResponse(user_id=user_id, items=[])
    return CartResponse(user_id=user_id, items=json.loads(cart_data))

@app.post("/cart", response_model=CartResponse)
async def update_cart(cart_update: CartUpdate, user_id: str = Depends(get_current_user_id)):
    items_dict = [item.model_dump() for item in cart_update.items]
    await redis_client.set(f"cart:{user_id}", json.dumps(items_dict), ex=60*60*24*7)
    return CartResponse(user_id=user_id, items=cart_update.items)

@app.delete("/cart", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cart(user_id: str = Depends(get_current_user_id)):
    await redis_client.delete(f"cart:{user_id}")
    return None