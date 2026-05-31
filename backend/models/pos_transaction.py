from sqlalchemy import Column, String, Float, DateTime, Integer
from sqlalchemy.sql import func
from database import Base


class POSTransaction(Base):
    __tablename__ = "pos_transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    transaction_id = Column(String(64), unique=True, index=True, nullable=False)
    store_id = Column(String(32), index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    basket_value_inr = Column(Float, nullable=True)
    created_at = Column(DateTime, default=func.now())
