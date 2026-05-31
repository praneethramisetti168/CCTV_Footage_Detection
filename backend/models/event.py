from sqlalchemy import Column, String, Float, DateTime, JSON, Integer, Boolean, BigInteger
from sqlalchemy.sql import func
from database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_id = Column(String(64), unique=True, index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=func.now(), index=True)
    store_id = Column(String(32), index=True, nullable=True, default="STORE_BLR_001")
    camera_id = Column(String(32), index=True, default="cam_01")
    event_type = Column(String(64), index=True, nullable=False)
    # Internal pipeline field
    person_id = Column(String(64), index=True, nullable=True)
    # Challenge-spec field (Re-ID token)
    visitor_id = Column(String(64), index=True, nullable=True)
    zone_id = Column(String(64), nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    is_staff = Column(Boolean, default=False, nullable=False)
    dwell_ms = Column(BigInteger, default=0, nullable=False)
    session_seq = Column(Integer, nullable=True)
    bounding_box = Column(JSON, nullable=True)
    meta = Column("metadata", JSON, nullable=True)
