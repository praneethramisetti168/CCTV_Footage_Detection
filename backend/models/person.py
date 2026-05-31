from sqlalchemy import Column, String, Float, DateTime, Integer, JSON
from sqlalchemy.sql import func
from database import Base


class PersonTrack(Base):
    __tablename__ = "person_tracks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    track_id = Column(String(64), unique=True, index=True, nullable=False)
    first_seen = Column(DateTime, default=func.now())
    last_seen = Column(DateTime, default=func.now())
    zones_visited = Column(JSON, default=list)
    total_dwell_seconds = Column(Float, default=0.0)
    entry_camera = Column(String(32), nullable=True)
