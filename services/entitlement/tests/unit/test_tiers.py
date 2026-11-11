import pytest

from app.service.model import Tier
from app.service.tiers import TierPolicy

POLICY = TierPolicy(hobbyist_threshold_cents=4000, premium_threshold_cents=9000)


@pytest.mark.parametrize(
    ("total_cents", "tier"),
    [
        (0, Tier.STARTER),
        (3999, Tier.STARTER),
        (4000, Tier.HOBBYIST),
        (8999, Tier.HOBBYIST),
        (9000, Tier.PREMIUM),
        (100_000, Tier.PREMIUM),
    ],
)
def test_tier_follows_the_sum_paid(total_cents: int, tier: Tier):
    assert POLICY.tier_for(total_cents) is tier


@pytest.mark.parametrize(
    ("tier", "threshold"),
    [(Tier.STARTER, None), (Tier.HOBBYIST, 4000), (Tier.PREMIUM, 9000)],
)
def test_threshold_is_the_sum_that_keeps_the_tier(tier: Tier, threshold: int | None):
    assert POLICY.threshold_of(tier) == threshold
