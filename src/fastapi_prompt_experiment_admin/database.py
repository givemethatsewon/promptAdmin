from collections.abc import Generator

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def init_db(engine: Engine) -> None:
    """Create PromptAdmin tables for simple installs and examples."""
    Base.metadata.create_all(bind=engine)


def make_session_dependency(engine: Engine):
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return get_session
