"""Precision / recall / F1 for the classifier eval.

Pure functions — no DB, no API — so the metric maths is unit-testable in
isolation. The positive class is ``"yes"`` (the item *is* an opportunity).
"""

from __future__ import annotations

from collections import defaultdict

POSITIVE = "yes"


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def compute_metrics(pairs: list[tuple[str, str]]) -> dict[str, float | int]:
    """Compute precision / recall / F1 from (truth, predicted) label pairs.

    Each label is ``"yes"`` or ``"no"``. Returns a dict with the confusion
    matrix counts plus precision, recall, f1, accuracy. An empty input
    yields all-zero metrics rather than raising — an eval over zero items is
    a degenerate but not erroneous case (the caller decides if that matters).
    """
    tp = fp = fn = tn = 0
    for truth, predicted in pairs:
        if predicted == POSITIVE and truth == POSITIVE:
            tp += 1
        elif predicted == POSITIVE and truth != POSITIVE:
            fp += 1
        elif predicted != POSITIVE and truth == POSITIVE:
            fn += 1
        else:
            tn += 1

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else 0.0
    accuracy = _safe_div(tp + tn, tp + fp + fn + tn)

    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "n": len(pairs),
    }


def compute_metrics_by_tier(
    rows: list[tuple[str, str, str]],
) -> dict[str, dict[str, float | int]]:
    """Per-tier metrics. Each row is ``(difficulty_tier, truth, predicted)``."""
    buckets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for tier, truth, predicted in rows:
        buckets[tier].append((truth, predicted))
    return {tier: compute_metrics(pairs) for tier, pairs in sorted(buckets.items())}
