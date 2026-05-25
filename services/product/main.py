from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
from typing import List
import aio_pika
import json
import asyncio

from schemas import ProductCreate, ProductResponse
from models import Product
from database import get_db, engine, Base, AsyncSessionLocal
from config import settings
from shared.events import OrderCreatedEvent, StockReservedEvent, StockFailedEvent, OrderCompletedEvent

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError

security = HTTPBearer()

async def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[settings.algorithm])
        role: str = payload.get("role")
        if role != "admin":
            raise HTTPException(status_code=403, detail="Admin privileges required")
        return payload.get("sub")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def process_order_created(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = OrderCreatedEvent(**event_data)
        
        print(f"📦 PRODUCT SERVICE: Received order {event.order_id} to check stock!")
        
        async with AsyncSessionLocal() as db:
            item_request = event.items[0] 
            product_id_str = str(item_request.product_id)
            
            result = await db.execute(select(Product).where(Product.id == product_id_str))
            product = result.scalars().first()
            
            if not product or product.available_quantity < item_request.quantity:
                print(f"❌ PRODUCT SERVICE: Insufficient stock for {event.order_id}!")
                failure_event = StockFailedEvent(
                    order_id=event.order_id,
                    reason="Not enough stock available"
                )
                connection = await aio_pika.connect_robust(settings.rabbitmq_url)
                async with connection:
                    channel = await connection.channel()
                    await channel.default_exchange.publish(
                        aio_pika.Message(body=failure_event.model_dump_json().encode()),
                        routing_key="stock.failed" 
                    )
                return

            product.available_quantity -= item_request.quantity
            product.reserved_quantity += item_request.quantity
            await db.commit()
            print(f"🔒 PRODUCT SERVICE: Reserved {item_request.quantity} units for {event.order_id}.")
            
        success_event = StockReservedEvent(
            order_id=event.order_id,
            user_id=event.user_id,          
            total_amount=event.total_amount,
            status="RESERVED"
        )
        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            await channel.default_exchange.publish(
                aio_pika.Message(body=success_event.model_dump_json().encode()),
                routing_key="stock.reserved" 
            )

async def process_stock_failed(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = StockFailedEvent(**event_data)
        
        print(f"❌ PRODUCT SERVICE: Received stock failure for {event.order_id}. Releasing reserved stock...")
        
        async with AsyncSessionLocal() as db:
            item_request = event.items[0] 
            product_id_str = str(item_request.product_id)
            
            result = await db.execute(select(Product).where(Product.id == product_id_str))
            product = result.scalars().first()
            
            if product:
                product.available_quantity += item_request.quantity
                product.reserved_quantity -= item_request.quantity
                await db.commit()
                print(f"🔓 PRODUCT SERVICE: Released reserved stock for {event.order_id}.")
async def process_order_completed(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = OrderCompletedEvent(**event_data)
        
        print(f"✅ PRODUCT SERVICE: Received order completion for {event.order_id}. Finalizing stock...")
        
        async with AsyncSessionLocal() as db:
            item_request = event.items[0] 
            product_id_str = str(item_request.product_id)
            
            result = await db.execute(select(Product).where(Product.id == product_id_str))
            product = result.scalars().first()
            
            if product:
                product.reserved_quantity -= item_request.quantity
                await db.commit()
                print(f"✅ PRODUCT SERVICE: Finalized stock for {event.order_id}.")

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
            print(f"⏳ Waiting for RabbitMQ to wake up... (Attempt {i+1}/10)")
            await asyncio.sleep(5) 
            
    if not connection:
        raise RuntimeError("RabbitMQ never woke up!")
            
    channel = await connection.channel()

    dlx_exchange = await channel.declare_exchange("dlx", aio_pika.ExchangeType.DIRECT, durable=True)
    dlq = await channel.declare_queue("dead_letter_queue", durable=True)
    await dlq.bind(dlx_exchange, routing_key="poison_message")

    queue_args = {
        "x-dead-letter-exchange": "dlx",
        "x-dead-letter-routing-key": "poison_message"
    }

    queue = await channel.declare_queue("order.created", durable=True, arguments=queue_args)
    await queue.consume(process_order_created)

    cleanup_queue = await channel.declare_queue("order.completed", durable=True, arguments=queue_args)
    await cleanup_queue.consume(process_order_completed)

    print("🎧 Product Service is listening for events...")
    
    yield
    if connection:
        await connection.close()

app = FastAPI(title="FastCart Product API", lifespan=lifespan)
allowed_origins = [origin.strip() for origin in settings.frontend_cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/products/health")
async def health_check():
    return {"status": "Product Service is healthy"}

@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product: ProductCreate, 
    admin_id: str = Depends(require_admin), 
    db: AsyncSession = Depends(get_db)
):
    new_product = Product(
        name=product.name,
        description=product.description,
        price=product.price,
        available_quantity=product.available_quantity,
        reserved_quantity=0
    )
    db.add(new_product)
    await db.commit()
    await db.refresh(new_product)
    return new_product

@app.get("/products", response_model=List[ProductResponse])
async def list_products(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product))
    return result.scalars().all()

@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product