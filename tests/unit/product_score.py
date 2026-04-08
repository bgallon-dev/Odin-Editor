"""
Product score computation for the pattern hardening system.

Implements the four-factor multiplicative model:

  product = evidence_density
          x recency_gradient
          x consistency
          x performance_alignment

Each factor is in [0.0, 1.0]. The product is in [0.0, 1.0].
Because the factors multiply rather than add, a near-zero score
on any single dimension collapses the product regardless of the others.

This is the core guard mechanism from the HCS-inspired architecture.
Weight class transitions use coverability-style thresholds (>=),
not reachability-style (==), following the Kuijer et al. insight.
"""
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Thresholds — all configurable via system_config in production
# ---------------------------------------------------------------------------

SOFT_THRESHOLD         = 0.20
MODERATE_THRESHOLD     = 0.45
HARD_THRESHOLD         = 0.70
CRYSTALLIZED_THRESHOLD = 0.85


class WeightClass(str, Enum):
    NONE         = "none"
    SOFT         = "soft"
    MODERATE     = "moderate"
    HARD         = "hard"
    CRYSTALLIZED = "crystallized"


@dataclass
class ProductScore:
    """
    The complete product score record.

    Stores all four factors individually so the weight class
    diagnosis is always available — you can see exactly which
    factor is suppressing the product without re-running
    the computation.
    """
    evidence_density:      float
    recency_gradient:      float
    consistency:           float
    performance_alignment: float
    product:               float
    weight_class:          WeightClass

    def diagnose(self) -> str:
        """Return a human-readable explanation of the limiting factor."""
        factors = {
            "evidence_density":      self.evidence_density,
            "recency_gradient":      self.recency_gradient,
            "consistency":           self.consistency,
            "performance_alignment": self.performance_alignment,
        }
        limiting = min(factors, key=factors.__getitem__)
        limiting_value = factors[limiting]

        if limiting_value < 0.3:
            return (
                f"Suppressed by {limiting} ({limiting_value:.2f}). "
                f"Product: {self.product:.3f} -> {self.weight_class.value}"
            )
        return (
            f"Balanced factors. "
            f"Product: {self.product:.3f} -> {self.weight_class.value}"
        )


def compute_product_score(
    evidence_density:      float,
    recency_gradient:      float,
    consistency:           float,
    performance_alignment: float,
) -> ProductScore:
    """
    Compute the product score from the four independent factors.

    All factors must be in [0.0, 1.0]. Values outside this range
    are clamped silently — callers are responsible for normalization.
    """
    ed  = max(0.0, min(1.0, evidence_density))
    rg  = max(0.0, min(1.0, recency_gradient))
    con = max(0.0, min(1.0, consistency))
    pa  = max(0.0, min(1.0, performance_alignment))

    product = round(ed * rg * con * pa, 8)

    if product < SOFT_THRESHOLD:
        weight_class = WeightClass.NONE
    elif product < MODERATE_THRESHOLD:
        weight_class = WeightClass.SOFT
    elif product < HARD_THRESHOLD:
        weight_class = WeightClass.MODERATE
    elif product < CRYSTALLIZED_THRESHOLD:
        weight_class = WeightClass.HARD
    else:
        weight_class = WeightClass.CRYSTALLIZED

    return ProductScore(
        evidence_density=ed,
        recency_gradient=rg,
        consistency=con,
        performance_alignment=pa,
        product=product,
        weight_class=weight_class,
    )
