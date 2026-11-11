from app.config import Settings

UNREACHABLE_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"
ESCALATION_QUEUE_URL = "http://localstack:4566/000000000000/expert-escalation-requested"
TICKET_TOPIC_ARN = "arn:aws:sns:us-east-1:000000000000:ticket-events"


def make_settings(**overrides) -> Settings:
    fields = {
        "database_url": UNREACHABLE_DATABASE,
        "escalation_queue_url": ESCALATION_QUEUE_URL,
        "ticket_events_topic_arn": TICKET_TOPIC_ARN,
    }
    return Settings(**{**fields, **overrides})
