from app.restrooms.scoring import ScoredCandidate
from app.restrooms.similarity import route_overlap_ratio
from app.routing.provider import RoutePoint


# A candidate whose geometry overlaps too heavily with an
# already-labeled route isn't offering a genuinely distinct option --
# skip it for a secondary label (smoothest/most_scenic) rather than
# hand out two archetypes that are really the same route.
MAX_ARCHETYPE_OVERLAP = 0.7


def _overlaps_any_labeled(
    geometry: tuple[RoutePoint, ...],
    labeled_geometries: list[tuple[RoutePoint, ...]],
) -> bool:
    return any(
        route_overlap_ratio(geometry, labeled_geometry) > MAX_ARCHETYPE_OVERLAP  # noqa: E501
        for labeled_geometry in labeled_geometries
    )


def assign_archetypes(
    scored: list[ScoredCandidate],
) -> list[str | None]:
    """Labels a subset of matched candidates in `scored` (an
    already-ranked, already-sliced result list) with a human-facing
    archetype -- "best_overall", "smoothest", "most_scenic" -- so the
    frontend can badge routes beyond a bare rank number. Unmatched
    (fallback) candidates never get a label. Returns a list aligned
    1:1 with `scored`."""
    labels: list[str | None] = [None] * len(scored)

    matched_indices = [
        index
        for index, candidate in enumerate(scored)
        if candidate.matched
    ]

    if not matched_indices:
        return labels

    labeled_geometries: list[tuple[RoutePoint, ...]] = []

    best_index = matched_indices[0]
    labels[best_index] = "best_overall"
    labeled_geometries.append(scored[best_index].candidate.geometry)

    remaining_indices = matched_indices[1:]

    def eligible(indices: list[int]) -> list[int]:
        return [
            index
            for index in indices
            if not _overlaps_any_labeled(
                scored[index].candidate.geometry,
                labeled_geometries,
            )
        ]

    smoothest_candidates = eligible(remaining_indices)

    if smoothest_candidates:
        smoothest_index = min(
            smoothest_candidates,
            key=lambda index: (
                scored[index].signals_per_km,
                -scored[index].longest_uninterrupted_m,
            ),
        )
        labels[smoothest_index] = "smoothest"
        labeled_geometries.append(
            scored[smoothest_index].candidate.geometry
        )
        remaining_indices = [
            index
            for index in remaining_indices
            if index != smoothest_index
        ]

    scenic_candidates = eligible(remaining_indices)

    if scenic_candidates:
        scenic_index = max(
            scenic_candidates,
            key=lambda index: scored[index].pedestrian_path_ratio,
        )
        labels[scenic_index] = "most_scenic"

    return labels
