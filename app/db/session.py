from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL


def make_engine(url: str = DATABASE_URL) -> Engine:
    """An engine whose connections enforce foreign keys.

    SQLite leaves that pragma off, and with it off the `ON DELETE` rules of the schema
    silently do nothing, so it is switched on for every connection the pool hands out.
    """
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions that keep their objects readable after the commit that stored them."""
    return sessionmaker(engine, expire_on_commit=False)
