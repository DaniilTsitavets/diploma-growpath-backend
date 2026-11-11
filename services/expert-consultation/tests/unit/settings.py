from app.config import Settings

UNREACHABLE_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"
ESCALATION_QUEUE_URL = "http://localstack:4566/000000000000/expert-escalation-requested"


def make_settings(**overrides) -> Settings:
    fields = {
        "database_url": UNREACHABLE_DATABASE,
        "escalation_queue_url": ESCALATION_QUEUE_URL,
    }
    return Settings(**{**fields, **overrides})
