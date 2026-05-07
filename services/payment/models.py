import uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Float
from database import Base

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, completed, or failed
    stripe_session_id: Mapped[str] = mapped_column(String(255), nullable=True, index=True)