import uuid

import pytest

from app.service.effort import EffortPolicy
from app.service.errors import AiQuotaUnavailable
from app.service.model import ModelEffort, Tier


class FakeTiers:
    def __init__(self, tier: Tier | None) -> None:
        self._tier = tier

    def tier_of(self, user_id: uuid.UUID) -> Tier:
        if self._tier is None:
            raise AiQuotaUnavailable
        return self._tier


@pytest.mark.parametrize(
    ("tier", "effort"),
    [
        (Tier.STARTER, ModelEffort.MEDIUM),
        (Tier.HOBBYIST, ModelEffort.HIGH),
        (Tier.PREMIUM, ModelEffort.HIGH),
        (None, ModelEffort.MEDIUM),
    ],
)
def test_the_effort_follows_the_tier_and_is_medium_when_the_lookup_fails(
    tier: Tier | None, effort: ModelEffort
):
    assert EffortPolicy(FakeTiers(tier)).effort_for(uuid.uuid4()) == effort
