import logging
import uuid

from app.service.ai_quota import TierLookup
from app.service.errors import AiQuotaUnavailable
from app.service.model import ModelEffort, Tier

logger = logging.getLogger(__name__)

EFFORT_BY_TIER = {
    Tier.STARTER: ModelEffort.MEDIUM,
    Tier.HOBBYIST: ModelEffort.HIGH,
    Tier.PREMIUM: ModelEffort.HIGH,
}


class EffortPolicy:
    """The model effort of a user: by tier, `medium` when the tier cannot be looked up."""

    def __init__(self, tiers: TierLookup) -> None:
        self._tiers = tiers

    def effort_for(self, user_id: uuid.UUID) -> ModelEffort:
        try:
            return EFFORT_BY_TIER[self._tiers.tier_of(user_id)]
        except AiQuotaUnavailable:
            logger.warning("tier lookup failed, using the medium effort")
            return ModelEffort.MEDIUM
