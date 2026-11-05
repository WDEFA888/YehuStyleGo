import unittest

from yehu_style_go.board import GoBoard, IllegalMove
from yehu_style_go.trial import TrialSession


class TrialSessionTests(unittest.TestCase):
    def test_snapshot_and_trial_do_not_mutate_each_other(self):
        real = GoBoard(9)
        real.play("B", 2, 2)
        trial = TrialSession(real)
        trial.play(6, 6)
        self.assertEqual(len(real.move_history), 1)
        self.assertEqual(trial.board.move_history[-1].color, "W")
        real.play("W", 5, 5)
        self.assertEqual(trial.board.grid[5][5], 0)
        self.assertEqual(trial.base.grid[5][5], 0)
        self.assertNotIn(trial.board._position_key(), real._positions)

    def test_undo_and_redo_preserve_capture_history_and_turn(self):
        real = GoBoard(5)
        real.set_setup([(0, 1), (1, 0), (2, 1)], [(1, 1)])
        trial = TrialSession(real)
        before = real._position_key()
        trial.play(1, 2, "B")
        after = trial.board._position_key()
        self.assertEqual(trial.board.captures["B"], 1)
        self.assertTrue(trial.undo())
        self.assertEqual(trial.board._position_key(), before)
        self.assertEqual(trial.board.captures["B"], 0)
        self.assertEqual(trial.board.to_play, real.to_play)
        self.assertTrue(trial.redo())
        self.assertEqual(trial.board._position_key(), after)
        self.assertEqual(trial.board.captures["B"], 1)
        self.assertEqual(real._position_key(), before)

    def test_undo_cannot_cross_into_formal_moves(self):
        real = GoBoard(9)
        real.play("B", 2, 2)
        real.play("W", 6, 6)
        trial = TrialSession(real)
        self.assertFalse(trial.undo())
        self.assertEqual(len(trial.board.move_history), 2)
        self.assertEqual(trial.origin_turn, 2)
        self.assertFalse(trial.redo())

    def test_new_branch_discards_only_the_undone_future(self):
        trial = TrialSession(GoBoard(9))
        trial.play(2, 2)
        trial.play(6, 6)
        trial.undo()
        trial.play(5, 5)
        self.assertFalse(trial.can_redo)
        self.assertEqual(len(trial.moves), 2)
        self.assertEqual(trial.board.grid[6][6], 0)
        self.assertEqual(trial.board.grid[5][5], 2)

    def test_illegal_move_does_not_destroy_redo(self):
        trial = TrialSession(GoBoard(9))
        trial.play(2, 2)
        trial.play(6, 6)
        trial.undo()
        with self.assertRaises(IllegalMove):
            trial.play(2, 2)
        self.assertTrue(trial.can_redo)
        self.assertEqual(trial.cursor, 1)
        self.assertTrue(trial.redo())

    def test_reset_and_reload_use_independent_copies(self):
        real = GoBoard(9)
        real.play("B", 2, 2)
        trial = TrialSession(real)
        trial.play(6, 6)
        trial.reset()
        self.assertEqual(trial.board._position_key(), real._position_key())
        self.assertFalse(trial.can_undo or trial.can_redo)
        latest = GoBoard(13)
        latest.play("B", 3, 3)
        latest.play("W", 9, 9)
        trial.reload(latest)
        self.assertEqual(trial.board.size, 13)
        self.assertEqual(trial.origin_turn, 2)
        trial.play(6, 6)
        self.assertEqual(latest.grid[6][6], 0)

    def test_inherited_ko_is_enforced_after_opening_and_reset(self):
        real = GoBoard(5)
        real.set_setup([(0, 1), (1, 0), (1, 2)], [(1, 1), (2, 0), (2, 2), (3, 1)])
        real.play("B", 2, 1)
        trial = TrialSession(real)
        with self.assertRaisesRegex(IllegalMove, "打劫"):
            trial.play(1, 1)
        trial.play(None, None)
        trial.play(None, None)
        trial.play(1, 1)
        self.assertEqual(trial.board.captures["W"], 1)
        trial.reset()
        with self.assertRaisesRegex(IllegalMove, "打劫"):
            trial.play(1, 1)
        self.assertEqual(real.captures["W"], 0)

    def test_passes_and_manual_colors_remain_trial_only(self):
        real = GoBoard(9)
        trial = TrialSession(real)
        trial.play(2, 2, "W")
        trial.play(2, 3, "W")
        trial.play(None, None)
        trial.play(None, None)
        trial.play(6, 6)
        self.assertEqual([move.color for move in trial.moves[:3]], ["W", "W", "B"])
        self.assertEqual(trial.cursor, 5)
        self.assertEqual(len(real.move_history), 0)


if __name__ == "__main__":
    unittest.main()
