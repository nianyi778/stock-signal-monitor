from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# Strip ssl_mode from URL — pymysql uses connect_args, not URL params
_url = settings.database_url
_connect_args = {}
if "ssl_mode=VERIFY_IDENTITY" in _url:
    parsed = urlparse(_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("ssl_mode", None)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    _url = urlunparse(parsed._replace(query=new_query))
    _connect_args = {"ssl_verify_cert": True, "ssl_verify_identity": True}

# SQLite (used in tests) does not support pool_size/max_overflow
_is_sqlite = _url.startswith("sqlite")
_pool_kwargs = {} if _is_sqlite else {"pool_size": 5, "max_overflow": 10}

engine = create_engine(
    _url,
    connect_args=_connect_args,
    pool_recycle=3600,
    pool_pre_ping=True,
    **_pool_kwargs,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
