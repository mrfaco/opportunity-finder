"""Query novelty shared by the acceptance gate and the quality metrics.

One helper, so the number the gate enforces is the number the report shows.
"""

from __future__ import annotations

import re

_TOKEN_SPLIT = re.compile(r"[\W_]+")


def query_tokens(query: str) -> frozenset[str]:
    """Casefolded tokens split on whitespace and punctuation; no stop words in 1.0."""
    return frozenset(token for token in _TOKEN_SPLIT.split(query.casefold()) if token)


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def novelty(query: str, finder_queries: list[str]) -> float:
    """``1 − max Jaccard`` against earlier Finder queries; 1.0 when there are none."""
    tokens = query_tokens(query)
    return 1.0 - max(
        (jaccard(tokens, query_tokens(other)) for other in finder_queries), default=0.0
    )
