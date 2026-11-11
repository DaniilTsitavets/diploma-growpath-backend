import logging
import uuid

logger = logging.getLogger(__name__)


class MockPaymentGateway:
    """Stands in for a payment provider: every charge is approved."""

    def charge(self, user_id: uuid.UUID, order_id: uuid.UUID, amount_cents: int) -> None:
        logger.info(
            "payment approved", extra={"order_id": str(order_id), "amount_cents": amount_cents}
        )
