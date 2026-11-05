import math
import unittest

from yehu_style_go.board import BLACK, GoBoard
from yehu_style_go.scoring import PositionEstimate, score_area


class AreaScoreTests(unittest.TestCase):
    def test_empty_board_is_neutral_and_komi_is_applied_once(self):
        score = score_area(GoBoard(9))
        self.assertEqual(score.neutral, 81)
        self.assertEqual(score.black_total, 0)
        self.assertEqual(score.white_total, 7.5)
        self.assertEqual(score.result, "W+7.5")

    def test_shared_regions_are_neutral_and_draw_is_supported(self):
        board = GoBoard(3)
        board.set_setup([(0, 0), (1, 0), (0, 1)], [(2, 2), (1, 2), (2, 1)])
        score = score_area(board, komi=0)
        self.assertEqual((score.black_stones, score.white_stones, score.neutral), (3, 3, 3))
        self.assertEqual(score.result, "0")
        self.assertEqual(score_area(board, komi=0.5).result, "W+0.5")

    def test_enclosed_empty_point_is_counted_with_stones(self):
        board = GoBoard(5)
        board.set_setup([(0, 1), (1, 0), (2, 1), (1, 2)], [(4, 4)])
        score = score_area(board)
        self.assertEqual(score.territory[(1, 1)], BLACK)
        self.assertEqual(score.black_territory, 2)  # The corner (0, 0) is also enclosed.
        self.assertEqual(score.black_total, 6)
        self.assertEqual(score.result, "W+2.5")

    def test_dead_group_is_removed_and_original_board_is_unchanged(self):
        board = GoBoard(7)
        ring = {(x, y) for x in range(1, 6) for y in range(1, 6)
                if x in (1, 5) or y in (1, 5)}
        board.set_setup(ring, [(3, 3), (3, 4), (0, 0)])
        before = board._position_key()
        score = score_area(board, [(3, 3)])
        self.assertEqual(score.dead, frozenset({(3, 3), (3, 4)}))
        self.assertEqual(score.dead_white, 2)
        self.assertEqual(score.white_stones, 1)
        self.assertEqual(score.black_territory, 9)
        self.assertEqual(score.result, "B+16.5")
        self.assertEqual(board._position_key(), before)
        self.assertEqual(score.black_total + score.white_total - score.komi + score.neutral, 49)

    def test_prisoners_do_not_get_double_counted_in_area_scoring(self):
        board = GoBoard(9)
        board.set_setup([(2, 2)], [(6, 6)])
        expected = score_area(board).result
        board.captures = {"B": 100, "W": 200}
        self.assertEqual(score_area(board).result, expected)

    def test_no_automatic_dead_group_guess(self):
        board = GoBoard(5)
        board.set_setup([(0, 1), (1, 0), (2, 1), (1, 2)], [(1, 1), (4, 4)])
        score = score_area(board)
        self.assertEqual(score.dead_white, 0)
        self.assertEqual(score.white_stones, 2)

    def test_invalid_dead_marks_and_nonfinite_komi_are_rejected(self):
        board = GoBoard(9)
        for point in ((-1, 0), (9, 0), (0, 0)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                score_area(board, [point])
        for komi in (math.nan, math.inf):
            with self.assertRaises(ValueError):
                score_area(board, komi=komi)


class PositionEstimateTests(unittest.TestCase):
    def test_black_based_response_is_converted_for_white_player(self):
        estimate = PositionEstimate.from_response(
            {"rootInfo": {"winrate": 0.7, "scoreLead": 4.2},
             "ownership": [1, -1, 0, 0.9]}, 2)
        text = estimate.summary("W", 30)
        self.assertIn("胜率 30.0%", text)
        self.assertIn("落后 4.2", text)
        self.assertEqual((estimate.black_points, estimate.white_points, estimate.uncertain_points), (2, 1, 1))
        self.assertIn("不是终局判决", text)

    def test_optional_ownership_does_not_fabricate_counts(self):
        estimate = PositionEstimate.from_response({"rootInfo": {"winrate": 0.4, "scoreLead": -5}}, 9)
        self.assertIsNone(estimate.black_points)
        self.assertIn("领先 5.0", estimate.summary("W", 10))
        self.assertNotIn("高置信度归属", estimate.summary("B", 10))

    def test_bad_engine_data_is_rejected(self):
        for root in ({}, {"winrate": 2, "scoreLead": 0},
                     {"winrate": 0.5, "scoreLead": math.nan}):
            with self.assertRaises(ValueError):
                PositionEstimate.from_response({"rootInfo": root}, 9)
        with self.assertRaises(ValueError):
            PositionEstimate.from_response({"rootInfo": {"winrate": 0.5, "scoreLead": 0},
                                            "ownership": [1]}, 9)


if __name__ == "__main__":
    unittest.main()
