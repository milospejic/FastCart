from fastapi import FastAPI, status, Depends, HTTPException
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
from shared.events import OrderCreatedEvent, StockReservedEvent, StockFailedEvent

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
            status="RESERVED"
        )
        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            await channel.default_exchange.publish(
                aio_pika.Message(body=success_event.model_dump_json().encode()),
                routing_key="stock.reserved" 
            )

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
        raise RuntimeError("RabbitMQ never woke up! Please check your docker containers.")
            
    channel = await connection.channel()
    queue = await channel.declare_queue("order.created", durable=True)
    await queue.consume(process_order_created)
    print("🎧 Product Service is listening for events...")
    
    yield
    if connection:
        await connection.close()

app = FastAPI(title="FastCart Product API", lifespan=lifespan)


@app.get("/products/health")
async def health_check():
    return {"status": "Product Service is healthy"}

@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(product: ProductCreate, db: AsyncSession = Depends(get_db)):
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