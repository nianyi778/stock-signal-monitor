from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# Strip ssl_mode from URL — pymysql uses connect_args, not URL params
_url = settings.database_url
_connect_args = {}
if "ssl_mode=VERIFY_IDENTITY" in _url:
    _url = _url.replace("?ssl_mode=VERIFY_IDENTITY", "").replace("&ssl_mode=VERIFY_IDENTITY", "")
    _connect_args = {"ssl_verify_cert": True, "ssl_verify_identity": True}

engine = create_engine(
    _url,
    connect_args=_connect_args,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
