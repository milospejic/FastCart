from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
import jwt
import httpx
from jwt.exceptions import InvalidTokenError

from schemas import OrderCreate, OrderResponse
from models import Order
from database import get_db, engine, Base
from config import settings

import aio_pika
import uuid
import json
import asyncio
from database import AsyncSessionLocal
from shared.events import OrderCreatedEvent, EventOrderItem, StockFailedEvent, PaymentProcessedEvent, OrderCompletedEvent

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

async def process_stock_failed(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = StockFailedEvent(**event_data)
        
        print(f"❌ ORDER SERVICE: Received stock failure for {event.order_id}. Cancelling order...")
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Order).where(Order.id == event.order_id))
            order = result.scalars().first()
            if order:
                order.status = "cancelled"
                await db.commit()
                print(f"🚫 ORDER SERVICE: Order {event.order_id} has been cancelled!")

async def process_payment_processed(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = PaymentProcessedEvent(**event_data)
        
        print(f"✅ ORDER SERVICE: Payment received for {event.order_id}! Fulfilling order...")
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Order).where(Order.id == event.order_id))
            order = result.scalars().first()
            
            if order:
                order.status = "completed"
                await db.commit()
                
                connection = await aio_pika.connect_robust(settings.rabbitmq_url)
                async with connection:
                    channel = await connection.channel()
                    completed_event = OrderCompletedEvent(
                        order_id=order.id,
                        items=[EventOrderItem(product_id=uuid.UUID(order.product_id), quantity=order.quantity)]
                    )
                    await channel.default_exchange.publish(
                        aio_pika.Message(body=completed_event.model_dump_json().encode()),
                        routing_key="order.completed"
                    )
                print(f"📦 ORDER SERVICE: Order {event.order_id} is complete. 'order.completed' event sent!")
        
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    connection = None
    for i in range(10):
        try:
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            break
        except Exception:
            print(f"⏳ Waiting for RabbitMQ... (Attempt {i+1}/10)")
            await asyncio.sleep(5)
            
    if not connection:
        raise RuntimeError("RabbitMQ never woke up! Please check your docker containers.")
            
    channel = await connection.channel()
    queue = await channel.declare_queue("stock.failed", durable=True)
    await queue.consume(process_stock_failed)

    payment_queue = await channel.declare_queue("payment.processed", durable=True)
    await payment_queue.consume(process_payment_processed)
    print("🎧 Order Service is listening for events...")
    
    yield
    if connection:
        await connection.close()

app = FastAPI(title="FastCart Order API", description="Manages customer orders", lifespan=lifespan)
allowed_origins = [origin.strip() for origin in settings.frontend_cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/orders/health")
async def health_check():
    return {"status": "Order Service is healthy"}

@app.get("/orders", response_model=list[OrderResponse])
async def list_orders(user_data: dict = Depends(get_current_user_token), db: AsyncSession = Depends(get_db)):
    if user_data["role"] == "admin":
        result = await db.execute(select(Order))
    else:
        result = await db.execute(select(Order).where(Order.user_id == user_data["user_id"]))
        
    return result.scalars().all()

@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order: OrderCreate, 
    user_data: dict = Depends(get_current_user_token), 
    db: AsyncSession = Depends(get_db)
):
    user_id = user_data["user_id"]
    product_url = f"http://fastcart-product:8002/products/{order.product_id}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(product_url)
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail="Product not found in catalog")
            response.raise_for_status()
            product_data = response.json()
            item_price = product_data.get("price", 0.0)
            
            if product_data.get("available_quantity", 0) < order.quantity:
                raise HTTPException(status_code=400, detail="Not enough stock available!")

        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Could not communicate with Product Catalog")

    calculated_total = float(item_price) * order.quantity

    new_order = Order(
        user_id=user_id,
        product_id=order.product_id,
        quantity=order.quantity,
        status="pending"
    )
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        
        event = OrderCreatedEvent(
            order_id=new_order.id,
            user_id=uuid.UUID(user_id),
            items=[EventOrderItem(product_id=uuid.UUID(order.product_id), quantity=order.quantity)],
            total_amount=calculated_total  
        )
        
        await channel.default_exchange.publish(
            aio_pika.Message(body=event.model_dump_json().encode()),
            routing_key="order.created"
        )
        print(f"📣 ORDER SERVICE: Order {new_order.id} saved for ${calculated_total}. Event published!")

    return new_order