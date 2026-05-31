import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from config import settings

# Allow DATABASE_URL env var to override settings (useful for tests)
_db_url = os.environ.get("DATABASE_URL", settings.database_url)

engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False},
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yield a DB session, close on done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables on startup."""
    # Import models so Base knows about them
    import models.event  # noqa: F401
    import models.person  # noqa: F401
    import models.anomaly  # noqa: F401
    import models.pos_transaction  # noqa: F401
    Base.metadata.create_all(bind=engine)

