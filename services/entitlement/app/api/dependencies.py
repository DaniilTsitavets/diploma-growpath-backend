from fastapi import Request

from app.service.entitlements import EntitlementService
from app.service.quota import QuotaService


def get_entitlement_service(request: Request) -> EntitlementService:
    return request.app.state.entitlement_service


def get_ai_quota(request: Request) -> QuotaService:
    return request.app.state.ai_quota


def get_expert_quota(request: Request) -> QuotaService:
    return request.app.state.expert_quota
