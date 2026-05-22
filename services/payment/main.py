from fastapi import FastAPI, status, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
import aio_pika
import json
import asyncio
import stripe
from sqlalchemy.future import select
from schemas import PaymentResponse, CheckoutUrlResponse

from database import engine, Base, AsyncSessionLocal, get_db
from models import Payment
from schemas import PaymentResponse
from config import settings
import uuid
from shared.events import StockReservedEvent, PaymentProcessedEvent

stripe.api_key = settings.stripe_secret_key

async def process_stock_reserved(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():
        event_data = json.loads(message.body.decode())
        event = StockReservedEvent(**event_data)
        
        print(f"💳 PAYMENT SERVICE: Stock reserved for order {event.order_id}. Creating payment record...")
        
        async with AsyncSessionLocal() as db:
            new_payment = Payment(
                order_id=str(event.order_id),
                user_id=str(event.user_id),
                amount=event.total_amount,
                status="pending"
            )
            db.add(new_payment)
            await db.commit()
            print(f"✅ PAYMENT SERVICE: Pending payment created for ${event.total_amount}!")

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
    queue = await channel.declare_queue("stock.reserved", durable=True)
    await queue.consume(process_stock_reserved)
    print("🎧 Payment Service is listening for 'stock.reserved' events...")
    
    yield
    if connection:
        await connection.close()

app = FastAPI(title="FastCart Payment API", lifespan=lifespan)
allowed_origins = [origin.strip() for origin in settings.frontend_cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/payments/{order_id}/checkout", response_model=CheckoutUrlResponse)
async def create_checkout_session(order_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Payment).where(Payment.order_id == order_id))
    payment = result.scalars().first()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found for this order")
    
    if payment.status == "completed":
        raise HTTPException(status_code=400, detail="This order is already paid for!")

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'FastCart Order {order_id}',
                        },
                        'unit_amount': int(payment.amount * 100), 
                    },
                    'quantity': 1,
                }
            ],
            mode='payment',
            success_url=f'{settings.frontend_url}/docs', 
            cancel_url=f'{settings.frontend_url}/docs',
            metadata={'order_id': order_id} 
        )
        
        payment.stripe_session_id = checkout_session.id
        await db.commit()

        return CheckoutUrlResponse(checkout_url=checkout_session.url)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        order_id_str = session.get('metadata', {}).get('order_id')
        stripe_session_id = session.get('id')

        print(f"💰 STRIPE WEBHOOK: Payment success for Order {order_id_str}!")

        result = await db.execute(select(Payment).where(Payment.order_id == order_id_str))
        payment = result.scalars().first()
        
        if payment:
            payment.status = "completed"
            await db.commit()
            
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                
                payment_event = PaymentProcessedEvent(
                    order_id=uuid.UUID(order_id_str),
                    status="COMPLETED",
                    transaction_id=stripe_session_id
                )
                
                await channel.default_exchange.publish(
                    aio_pika.Message(body=payment_event.model_dump_json().encode()),
                    routing_key="payment.processed"
                )
                print(f"📣 PAYMENT SERVICE: Event 'payment.processed' published!")

    return {"status": "success"}
@app.get("/payments/health")
async def health_check():
    return {"status": "Payment Service is healthy"}
