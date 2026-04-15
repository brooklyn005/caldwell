from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import settings
from database.models import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,  # prevents DetachedInstanceError in concurrent async scenes
)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
