from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from undertow.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def session_factory() -> sessionmaker[Session]:
    return _sessionmaker()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with _sessionmaker()() as session:
        yield session
