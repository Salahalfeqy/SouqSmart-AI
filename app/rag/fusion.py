from collections import defaultdict
from typing import Sequence


def reciprocal_rank_fusion(
    ranked_lists: Sequence[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Combines multiple ranked lists of document / product IDs using Reciprocal Rank Fusion (RRF).
    RRF Score(d) = sum(1 / (k + rank_i(d))) for each list where document appears.
    Rank is 1-indexed.
    """
    scores: dict[str, float] = defaultdict(float)

    for item_list in ranked_lists:
        for rank, item_id in enumerate(item_list, start=1):
            if item_id:
                scores[str(item_id)] += 1.0 / (k + rank)

    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results

