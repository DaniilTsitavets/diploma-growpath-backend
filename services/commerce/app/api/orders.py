import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.api.dependencies import current_user_id, get_purchase_service
from app.api.schemas import CreateOrderRequest, OrderResponse
from app.service.model import PlaceOrder
from app.service.purchase import PurchaseService

router = APIRouter(prefix="/v1/orders", tags=["orders"])


@router.post("", status_code=201, responses={200: {"model": OrderResponse}})
def create_order(
    body: CreateOrderRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
    service: Annotated[PurchaseService, Depends(get_purchase_service)],
) -> OrderResponse:
    """Place an order; repeating the request with the same key returns the same order (200)."""
    placed = service.place_order(PlaceOrder(user_id, body.item_id, idempotency_key))
    if not placed.created:
        response.status_code = 200
    return OrderResponse.model_validate(placed.order)
