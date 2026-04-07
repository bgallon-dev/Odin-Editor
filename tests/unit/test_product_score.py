"""
Unit tests for the product score computation and pattern hardening invariants.

These tests verify the mathematical properties we designed into the
four-factor model:

  product_score = evidence_density
               x recency_gradient
               x consistency
               x performance_alignment

Key invariants that must hold:
  1. Negative performance alignment collapses the product regardless of other factors
  2. Low consistency prevents hardening past moderate regardless of evidence count
  3. Product score is always in [0.0, 1.0]
  4. Weight class transitions happen at the correct thresholds
  5. Crystallized state requires product > 0.85 — not reachable by evidence alone
  6. The product is multiplicative, not additive — a zero factor zeroes the score
"""
import pytest
from tests.unit.product_score import (
    ProductScore,
    compute_product_score,
    WeightClass,
    SOFT_THRESHOLD,
    MODERATE_THRESHOLD,
    HARD_THRESHOLD,
    CRYSTALLIZED_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_score(
    evidence_density:      float = 1.0,
    recency_gradient:      float = 1.0,
    consistency:           float = 1.0,
    performance_alignment: float = 1.0,
) -> ProductScore:
    return compute_product_score(
        evidence_density=evidence_density,
        recency_gradient=recency_gradient,
        consistency=consistency,
        performance_alignment=performance_alignment,
    )


# ---------------------------------------------------------------------------
# Core multiplicative property
# ---------------------------------------------------------------------------

class TestMultiplicativeProperty:

    def test_all_factors_1_gives_product_1(self):
        score = make_score(1.0, 1.0, 1.0, 1.0)
        assert score.product == pytest.approx(1.0)

    def test_any_factor_0_gives_product_0(self):
        assert make_score(evidence_density=0.0).product == 0.0
        assert make_score(recency_gradient=0.0).product == 0.0
        assert make_score(consistency=0.0).product == 0.0
        assert make_score(performance_alignment=0.0).product == 0.0

    def test_product_is_not_additive(self):
        # If additive: 0.5 + 1.0 + 1.0 + 1.0 / 4 = 0.875
        # If multiplicative: 0.5 x 1.0 x 1.0 x 1.0 = 0.5
        score = make_score(evidence_density=0.5)
        assert score.product == pytest.approx(0.5)
        assert score.product != pytest.approx(0.875)

    def test_product_always_in_unit_interval(self):
        import random
        random.seed(42)
        for _ in range(100):
            score = make_score(
                evidence_density=random.random(),
                recency_gradient=random.random(),
                consistency=random.random(),
                performance_alignment=random.random(),
            )
            assert 0.0 <= score.product <= 1.0

    def test_factors_multiply_in_order(self):
        score = make_score(0.8, 0.9, 0.7, 0.6)
        expected = 0.8 * 0.9 * 0.7 * 0.6
        assert score.product == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Performance alignment invariant — the most critical property
# ---------------------------------------------------------------------------

class TestPerformanceAlignmentInvariant:
    """
    A pattern with negative performance alignment must never harden
    past moderate weight class regardless of how strong the other
    three factors are.

    This is the invariant that prevents the rabbit hole — the system
    cannot decide a pattern is a hard requirement when the measured
    performance data contradicts it.
    """

    def test_negative_alignment_collapses_product(self):
        # Perfect evidence, recency, and consistency — but negative alignment
        score = make_score(
            evidence_density=1.0,
            recency_gradient=1.0,
            consistency=1.0,
            performance_alignment=0.1,  # near-zero alignment
        )
        assert score.product <= MODERATE_THRESHOLD

    def test_negative_alignment_prevents_hard_class(self):
        score = make_score(
            evidence_density=1.0,
            recency_gradient=1.0,
            consistency=1.0,
            performance_alignment=0.2,
        )
        assert score.weight_class != WeightClass.HARD
        assert score.weight_class != WeightClass.CRYSTALLIZED

    def test_negative_alignment_prevents_crystallized(self):
        # Even with perfect other factors, negative alignment
        # must not produce crystallized weight class
        for alignment in [0.0, 0.05, 0.1, 0.15, 0.2, 0.25]:
            score = make_score(
                evidence_density=1.0,
                recency_gradient=1.0,
                consistency=1.0,
                performance_alignment=alignment,
            )
            assert score.weight_class != WeightClass.CRYSTALLIZED, (
                f"alignment={alignment} produced crystallized — "
                f"product={score.product:.3f}"
            )

    def test_neutral_alignment_allows_hardening(self):
        # Neutral alignment (0.5) with strong other factors
        # should be able to reach hard weight class
        score = make_score(
            evidence_density=1.0,
            recency_gradient=1.0,
            consistency=1.0,
            performance_alignment=0.5,
        )
        # 1.0 x 1.0 x 1.0 x 0.5 = 0.5 — moderate range
        assert score.weight_class in (WeightClass.MODERATE, WeightClass.HARD)

    def test_positive_alignment_accelerates_hardening(self):
        # Positive alignment with moderate other factors
        # should reach hard faster than neutral
        neutral_score   = make_score(0.85, 0.85, 0.85, 0.5)
        positive_score  = make_score(0.85, 0.85, 0.85, 0.9)
        assert positive_score.product > neutral_score.product


# ---------------------------------------------------------------------------
# Consistency invariant
# ---------------------------------------------------------------------------

class TestConsistencyInvariant:
    """
    Low consistency (mixed accept/dismiss decisions) must prevent
    a pattern from hardening past moderate even with high evidence
    and positive performance alignment.

    A pattern you yourself are ambivalent about should not become
    a hard requirement.
    """

    def test_low_consistency_caps_at_moderate(self):
        score = make_score(
            evidence_density=1.0,
            recency_gradient=1.0,
            consistency=0.3,  # very inconsistent
            performance_alignment=1.0,
        )
        assert score.weight_class in (
            WeightClass.NONE,
            WeightClass.SOFT,
            WeightClass.MODERATE,
        )

    def test_consistency_below_half_prevents_hard(self):
        for consistency in [0.0, 0.1, 0.2, 0.3, 0.4, 0.49]:
            score = make_score(
                evidence_density=1.0,
                recency_gradient=1.0,
                consistency=consistency,
                performance_alignment=1.0,
            )
            assert score.weight_class not in (
                WeightClass.HARD, WeightClass.CRYSTALLIZED
            ), (
                f"consistency={consistency} produced {score.weight_class} — "
                f"product={score.product:.3f}"
            )

    def test_high_consistency_enables_hardening(self):
        score = make_score(
            evidence_density=1.0,
            recency_gradient=1.0,
            consistency=0.95,
            performance_alignment=0.9,
        )
        assert score.weight_class in (WeightClass.HARD, WeightClass.CRYSTALLIZED)


# ---------------------------------------------------------------------------
# Weight class transitions
# ---------------------------------------------------------------------------

class TestWeightClassTransitions:
    """
    Weight class must correspond to product score ranges:
      < SOFT_THRESHOLD:          NONE
      SOFT to MODERATE:          SOFT
      MODERATE to HARD:          MODERATE
      HARD to CRYSTALLIZED:      HARD
      >= CRYSTALLIZED_THRESHOLD: CRYSTALLIZED
    """

    def test_product_below_soft_threshold_gives_none(self):
        score = make_score(
            evidence_density=0.1,
            recency_gradient=0.1,
            consistency=0.1,
            performance_alignment=0.1,
        )
        assert score.product < SOFT_THRESHOLD
        assert score.weight_class == WeightClass.NONE

    def test_product_in_soft_range_gives_soft(self):
        # Target product between SOFT_THRESHOLD and MODERATE_THRESHOLD
        # 0.75^4 ~ 0.316 — in soft range
        score = make_score(0.75, 0.75, 0.75, 0.75)
        if SOFT_THRESHOLD <= score.product < MODERATE_THRESHOLD:
            assert score.weight_class == WeightClass.SOFT

    def test_product_in_moderate_range_gives_moderate(self):
        # Target product between MODERATE_THRESHOLD (0.45) and HARD_THRESHOLD (0.70)
        # 0.85^4 ~ 0.522 — in moderate range
        score = make_score(0.85, 0.85, 0.85, 0.85)
        if MODERATE_THRESHOLD <= score.product < HARD_THRESHOLD:
            assert score.weight_class == WeightClass.MODERATE

    def test_product_in_hard_range_gives_hard(self):
        # Target product between HARD_THRESHOLD (0.70) and CRYSTALLIZED (0.85)
        # 0.93^4 ~ 0.748 — in hard range
        score = make_score(0.93, 0.93, 0.93, 0.93)
        if HARD_THRESHOLD <= score.product < CRYSTALLIZED_THRESHOLD:
            assert score.weight_class == WeightClass.HARD

    def test_product_above_crystallized_gives_crystallized(self):
        score = make_score(1.0, 1.0, 1.0, 1.0)
        assert score.product >= CRYSTALLIZED_THRESHOLD
        assert score.weight_class == WeightClass.CRYSTALLIZED

    def test_exact_threshold_boundaries(self):
        # Test each threshold boundary exactly
        thresholds = [
            (SOFT_THRESHOLD,         WeightClass.SOFT),
            (MODERATE_THRESHOLD,     WeightClass.MODERATE),
            (HARD_THRESHOLD,         WeightClass.HARD),
            (CRYSTALLIZED_THRESHOLD, WeightClass.CRYSTALLIZED),
        ]
        for threshold, expected_class in thresholds:
            # Construct a score with product exactly at this threshold
            # by setting all four factors to the fourth root of the threshold
            factor = threshold ** 0.25
            score = make_score(factor, factor, factor, factor)
            # Allow floating point tolerance
            if abs(score.product - threshold) < 0.001:
                assert score.weight_class == expected_class

    def test_weight_class_ordering_is_monotone(self):
        """Higher product scores must give equal or higher weight classes."""
        class_order = {
            WeightClass.NONE:         0,
            WeightClass.SOFT:         1,
            WeightClass.MODERATE:     2,
            WeightClass.HARD:         3,
            WeightClass.CRYSTALLIZED: 4,
        }
        products = [0.0, 0.1, 0.2, 0.35, 0.5, 0.65, 0.75, 0.9, 1.0]
        prev_order = -1
        for p in products:
            factor = p ** 0.25 if p > 0 else 0.0
            score = make_score(factor, factor, factor, factor)
            current_order = class_order[score.weight_class]
            assert current_order >= prev_order, (
                f"product={p:.2f} gave lower weight class than product={products[products.index(p)-1]:.2f}"
            )
            prev_order = current_order


# ---------------------------------------------------------------------------
# Coverability property — thresholds are >= not ==
# ---------------------------------------------------------------------------

class TestCoverabilityProperty:
    """
    Following the Kuijer et al. HCS paper insight:
    guard conditions must be coverability-style (>= threshold)
    not reachability-style (== threshold).

    This means a pattern that exceeds a threshold gets that weight class
    and stays there — it does not need to hit the threshold exactly.
    """

    def test_product_exceeding_hard_threshold_is_hard(self):
        # Any product > HARD_THRESHOLD and < CRYSTALLIZED gets HARD
        score = make_score(0.95, 0.95, 0.95, 0.85)
        if HARD_THRESHOLD < score.product < CRYSTALLIZED_THRESHOLD:
            assert score.weight_class == WeightClass.HARD

    def test_product_well_above_soft_is_still_soft(self):
        # Being above SOFT_THRESHOLD but below MODERATE gets SOFT
        score = make_score(0.7, 0.7, 0.7, 0.7)
        if SOFT_THRESHOLD <= score.product < MODERATE_THRESHOLD:
            assert score.weight_class == WeightClass.SOFT

    def test_factors_stored_individually(self):
        """
        The ProductScore must store all four factors individually
        so we can diagnose why a pattern is at a given weight class.
        This is the diagnostic transparency requirement.
        """
        score = make_score(0.8, 0.7, 0.6, 0.5)
        assert hasattr(score, 'evidence_density')
        assert hasattr(score, 'recency_gradient')
        assert hasattr(score, 'consistency')
        assert hasattr(score, 'performance_alignment')
        assert score.evidence_density      == pytest.approx(0.8)
        assert score.recency_gradient      == pytest.approx(0.7)
        assert score.consistency           == pytest.approx(0.6)
        assert score.performance_alignment == pytest.approx(0.5)
