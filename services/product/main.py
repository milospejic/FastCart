from fastapi import FastAPI, status, Depends, HTTPException
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
from typing import List

from schemas import ProductCreate, ProductResponse, ProductUpdateStock
from models import Product
from database import get_db, engine, Base

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="FastCart Product API", 
    description="Manages the product catalog",
    lifespan=lifespan
)

@app.get("/products/health")
async def health_check():
    return {"status": "Product Service is healthy"}

@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(product: ProductCreate, db: AsyncSession = Depends(get_db)):
    new_product = Product(
        name=product.name,
        description=product.description,
        price=product.price,
        stock=product.stock
    )
    db.add(new_product)
    await db.commit()
    await db.refresh(new_product)
    return new_product

@app.get("/products", response_model=List[ProductResponse])
async def list_products(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product))
    products = result.scalars().all()
    return products

@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return product

@app.patch("/products/{product_id}/deduct")
async def deduct_product_stock(product_id: uuid.UUID, deduction: ProductUpdateStock, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    if product.stock < deduction.quantity:
        raise HTTPException(status_code=400, detail="Not enough stock available!")
        
    product.stock -= deduction.quantity
    await db.commit()
    await db.refresh(product)
    
    return {"message": "Stock updated successfully", "remaining_stock": product.stock}