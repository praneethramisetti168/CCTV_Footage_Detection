from sqlalchemy import Column, String, Float, DateTime, Integer, Boolean, JSON
from sqlalchemy.sql import func
from database import Base


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    anomaly_id = Column(String(64), unique=True, index=True, nullable=False)
    timestamp = Column(DateTime, default=func.now(), index=True)
    anomaly_type = Column(String(64), index=True, nullable=False)
    severity = Column(String(16), nullable=False)   # LOW | MEDIUM | HIGH | CRITICAL
    description = Column(String(512), nullable=False)
    zone_id = Column(String(64), nullable=True)
    camera_id = Column(String(32), default="cam_01")
    resolved = Column(Boolean, default=False)
    meta = Column("metadata", JSON, nullable=True)
