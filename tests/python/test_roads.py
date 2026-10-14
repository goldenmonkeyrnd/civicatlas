"""Graph reduction must preserve travel permissions and protected attachments."""
from __future__ import annotations

import unittest

from tools.build_roads import PROJECTION, LineString, contract_segments, prune_empty_branches


def segment(u: int, v: int, direction: int = 0, miles: float = 1.0) -> tuple:
    points = [(-76.6 + u * 0.001, 39.3), (-76.6 + v * 0.001, 39.3)]
    return (u, v, miles, points, f'Road {u}-{v}', direction,
            LineString([PROJECTION.transform(*point) for point in points]))


def endpoints(segments: list[tuple]) -> set[int]:
    return {node for edge in segments for node in edge[:2]}


class RoadReductionTests(unittest.TestCase):
    def test_degree_two_chain_preserves_direction_length_and_geometry(self) -> None:
        original = [segment(0, 1, 1, 0.12345678), segment(1, 2, 1, 0.87654322)]
        reduced = contract_segments(original, {0, 2})
        assert len(reduced) == 1
        start, end, miles, points, _, direction, _ = reduced[0]
        assert (start, end, direction) == (0, 2, 1)
        assert miles == 1.0
        assert points == [original[0][3][0], original[0][3][1], original[1][3][1]]
        assert endpoints(reduced) == {0, 2}


    def test_reversed_orientation_keeps_the_only_allowed_travel_direction(self) -> None:
        reduced = contract_segments([segment(1, 0, 1), segment(2, 1, 1)], {0, 2})
        assert len(reduced) == 1
        assert reduced[0][:3] == (0, 2, 2.0)
        assert reduced[0][5] == -1


    def test_two_way_continuation_remains_two_way(self) -> None:
        reduced = contract_segments([segment(0, 1), segment(1, 2)], {0, 2})
        assert len(reduced) == 1
        assert reduced[0][5] == 0


    def test_opposing_oneways_preserve_protected_disconnected_attachments(self) -> None:
        # Neither endpoint can reach the other. Dropping both roads would turn the
        # known disconnected endpoints into missing road matches in publication.
        original = [segment(0, 1, 1), segment(2, 1, 1)]
        reduced = contract_segments(original, {0, 2})
        assert len(reduced) == 2
        assert endpoints(reduced) >= {0, 2}
        assert [(edge[0], edge[1], edge[5]) for edge in reduced] == [(0, 1, 1), (2, 1, 1)]


    def test_protected_cycle_and_unattached_cycle_retain_real_road_context(self) -> None:
        original = [segment(0, 1), segment(1, 2), segment(2, 0)]
        assert contract_segments(original, {0}) == original
        assert contract_segments(original, set()) == original


    def test_protected_internal_point_remains_a_junction(self) -> None:
        original = [segment(0, 1), segment(1, 2)]
        reduced = contract_segments(original, {0, 1, 2})
        assert len(reduced) == 2
        assert endpoints(reduced) == {0, 1, 2}


    def test_no_demand_leaf_pruning_preserves_all_attachment_routes(self) -> None:
        # 0 and 2 represent an area and facility. The 1->3->4 twig has no demand;
        # pruning it cannot change the protected route 0->1->2.
        original = [segment(0, 1, 1), segment(1, 2, 1), segment(1, 3), segment(3, 4)]
        pruned = prune_empty_branches(original, {0, 2})
        assert pruned == original[:2]
        reduced = contract_segments(pruned, {0, 2})
        assert len(reduced) == 1
        assert (reduced[0][0], reduced[0][1], reduced[0][2], reduced[0][5]) == (0, 2, 2.0, 1)


    def test_leaf_pruning_never_removes_a_protected_leaf(self) -> None:
        original = [segment(0, 1), segment(1, 2), segment(1, 3)]
        assert prune_empty_branches(original, {0, 2, 3}) == original


if __name__ == "__main__":
    unittest.main()
