import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Must set DATABASE_URL before importing app modules that load settings
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.database import Base  # noqa: E402

SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture
def db():
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
