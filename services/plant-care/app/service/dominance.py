from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum


class Support(StrEnum):
    """How strongly the model backs a cause; a coarse label, never a number."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def level(self) -> int:
        return tuple(Support).index(self)


@dataclass(frozen=True)
class DominanceRule:
    """The leader dominates when it has `dominant` support and no other has a `rivals` one."""

    dominant: Support
    rivals: Collection[Support]


def is_dominant(supports: Sequence[Support], rule: DominanceRule) -> bool:
    """`supports` are in rank order, the first one belongs to the leading candidate."""
    if not supports:
        return False
    leader, *others = supports
    return leader is rule.dominant and not any(support in rule.rivals for support in others)
