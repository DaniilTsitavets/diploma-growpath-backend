from dataclasses import dataclass

from app.service.model import Tier


@dataclass(frozen=True)
class TierPolicy:
    """Maps the cents paid in the last 30 days to a tier."""

    hobbyist_threshold_cents: int
    premium_threshold_cents: int

    def tier_for(self, total_cents: int) -> Tier:
        if total_cents >= self.premium_threshold_cents:
            return Tier.PREMIUM
        if total_cents >= self.hobbyist_threshold_cents:
            return Tier.HOBBYIST
        return Tier.STARTER

    def threshold_of(self, tier: Tier) -> int | None:
        """The sum that keeps `tier`; None for the lowest tier, which never ends."""
        match tier:
            case Tier.PREMIUM:
                return self.premium_threshold_cents
            case Tier.HOBBYIST:
                return self.hobbyist_threshold_cents
            case Tier.STARTER:
                return None
