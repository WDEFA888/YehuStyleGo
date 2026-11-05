import unittest

from yehu_style_go.board import BLACK, EMPTY, WHITE, GoBoard, IllegalMove, gtp_to_xy, xy_to_gtp


class BoardTests(unittest.TestCase):
    def test_capture(self):
        board = GoBoard(5)
        board.play("W", 1, 1)
        for point in ((0, 1), (1, 0), (2, 1)):
            board.play("B", *point)
        result = board.play("B", 1, 2)
        self.assertEqual(result.captured, 1)
        self.assertEqual(board.grid[1][1], EMPTY)
        self.assertEqual(board.captures["B"], 1)

    def test_suicide_rolls_back(self):
        board = GoBoard(5)
        for point in ((0, 1), (1, 0), (2, 1), (1, 2)):
            board.play("B", *point)
        before = board._position_key()
        with self.assertRaises(IllegalMove):
            board.play("W", 1, 1)
        self.assertEqual(board._position_key(), before)

    def test_occupied(self):
        board = GoBoard(5)
        board.play("B", 2, 2)
        with self.assertRaises(IllegalMove):
            board.play("W", 2, 2)
        self.assertEqual(len(board.move_history), 1)

    def test_ko(self):
        board = GoBoard(5)
        board.set_setup([(0, 1), (1, 0), (1, 2)], [(1, 1), (2, 0), (2, 2), (3, 1)])
        board.play("B", 2, 1)
        with self.assertRaises(IllegalMove):
            board.play("W", 1, 1)

    def test_snapback_repeating_an_older_position_is_legal_under_simple_ko(self):
        board = GoBoard(5)
        board.set_setup([(0, 3), (0, 1), (1, 2)], [(1, 3), (1, 4)])
        original = board._position_key()
        board.play("B", 0, 4)
        self.assertEqual(board.play("W", 0, 2).captured, 2)
        clone = board.clone()
        self.assertEqual(clone.play("B", 0, 3).captured, 1)
        self.assertEqual(clone._position_key(), original)
        self.assertNotEqual(board._position_key(), original)

    def test_positional_superko_is_still_available(self):
        board = GoBoard(5, ko_rule="POSITIONAL")
        board.set_setup([(0, 3), (0, 1), (1, 2)], [(1, 3), (1, 4)])
        board.play("B", 0, 4)
        board.play("W", 0, 2)
        with self.assertRaisesRegex(IllegalMove, "全局同形"):
            board.clone().play("B", 0, 3)

    def test_simple_ko_pass_releases_ban_and_clone_keeps_it(self):
        board = GoBoard(5)
        board.set_setup([(0, 1), (1, 0), (1, 2)], [(1, 1), (2, 0), (2, 2), (3, 1)])
        board.play("B", 2, 1)
        before = board._position_key()
        with self.assertRaisesRegex(IllegalMove, "打劫"):
            board.clone().play("W", 1, 1)
        self.assertEqual(board._position_key(), before)
        board.play("W", None, None)
        board.play("B", None, None)
        self.assertEqual(board.play("W", 1, 1).captured, 1)
        board.clear()
        self.assertIsNone(board._previous_position)

    def test_clone(self):
        board = GoBoard(5)
        clone = board.clone()
        clone.play("B", 1, 1)
        self.assertEqual(board.grid[1][1], EMPTY)
        self.assertEqual(clone.grid[1][1], BLACK)

    def test_coordinates(self):
        for size in (9, 13, 19):
            for x in range(size):
                for y in range(size):
                    self.assertEqual(gtp_to_xy(xy_to_gtp(x, y, size), size), (x, y))
        self.assertEqual(xy_to_gtp(8, 18), "J1")
        self.assertEqual(gtp_to_xy("pass"), (None, None))
        with self.assertRaises(ValueError):
            gtp_to_xy("I4")

    def test_setup_and_passes(self):
        board = GoBoard(9)
        board.set_setup([(2, 2)], [(4, 4)])
        self.assertEqual(board.grid[2][2], BLACK)
        self.assertEqual(board.grid[4][4], WHITE)
        board.play("W", None, None)
        board.play("B", None, None)
        self.assertEqual(len(board.move_history), 2)
