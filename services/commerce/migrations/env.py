from alembic import context
from sqlalchemy import Connection, create_engine

from app.config import Settings
from app.models import Base

target_metadata = Base.metadata


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        migrate(connection)
        return
    engine = create_engine(Settings().database_url.get_secret_value())
    with engine.connect() as connection:
        migrate(connection)


run_migrations()
