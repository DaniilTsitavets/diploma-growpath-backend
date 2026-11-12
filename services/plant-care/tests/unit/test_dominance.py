from itertools import product

import pytest

from app.service.dominance import DominanceRule, Support, is_dominant

H, M, L = Support.HIGH, Support.MEDIUM, Support.LOW
DEFAULT = DominanceRule(H, frozenset({M, H}))


@pytest.mark.parametrize(
    ("supports", "expected"),
    [
        ([H], True),
        ([H, L], True),
        ([H, L, L, L], True),
        ([H, M], False),
        ([H, H], False),
        ([H, L, M], False),
        ([M], False),
        ([M, L], False),
        ([M, M], False),
        ([L], False),
        ([L, L], False),
        ([], False),
    ],
)
def test_the_default_rule(supports: list[Support], expected: bool):
    assert is_dominant(supports, DEFAULT) is expected


def test_every_combination_of_three_labels_follows_the_rule():
    for supports in product(Support, repeat=3):
        leader, *others = supports
        expected = leader is H and all(support is L for support in others)
        assert is_dominant(list(supports), DEFAULT) is expected


@pytest.mark.parametrize(
    ("rule", "supports", "expected"),
    [
        (DominanceRule(M, frozenset({H})), [M, L], True),
        (DominanceRule(M, frozenset({H})), [M, M], True),
        (DominanceRule(M, frozenset({H})), [M, H], False),
        (DominanceRule(M, frozenset({H})), [H, L], False),
        (DominanceRule(H, frozenset({H})), [H, M], True),
        (DominanceRule(H, frozenset({H})), [H, H], False),
        (DominanceRule(H, frozenset({M, L})), [H, H], True),
        (DominanceRule(H, frozenset({M, L})), [H, L], False),
    ],
)
def test_the_thresholds_are_configurable(
    rule: DominanceRule, supports: list[Support], expected: bool
):
    assert is_dominant(supports, rule) is expected


def test_the_labels_are_ordered_from_low_to_high():
    assert [support.level for support in (L, M, H)] == [0, 1, 2]
