from app.config import Settings

UNREACHABLE_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"
ORDER_QUEUE_URL = "http://localstack:4566/000000000000/plant-care-order-placed"


def make_settings(**overrides) -> Settings:
    fields = {"database_url": UNREACHABLE_DATABASE, "order_placed_queue_url": ORDER_QUEUE_URL}
    return Settings(**{**fields, **overrides})
