import uuid


class ItemNotFound(Exception):
    def __init__(self, item_id: uuid.UUID) -> None:
        super().__init__(f"item {item_id} is not in the catalog or is not active")
        self.item_id = item_id


class ItemSlugNotFound(Exception):
    """No active item has this slug."""


class InvalidCursor(Exception):
    """The pagination cursor was not issued by this service."""


class CatalogUnavailable(Exception):
    """The catalog database cannot be reached; raised by the catalog repository."""


class IdempotencyKeyReused(Exception):
    """The key already belongs to an order for a different item."""


class PaymentFailed(Exception):
    pass


class PublishFailed(Exception):
    """The broker did not accept an event."""


class DiscountAlreadyUsed(Exception):
    """The user already has an order with a discount; raised by the order repository."""
