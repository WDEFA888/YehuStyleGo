import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.board import GoBoard
from yehu_style_go.storage import Library


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class TrialWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, Library(Path(self.temp.name)))
        self.root.update()
        self.app.spar_board = GoBoard(9)
        self.app.spar_board.play("B", 2, 2)
        self.app.spar_board.play("W", 6, 6)
        self.app.spar_active = True

    def tearDown(self):
        self.app.closed = True
        self.app.cancel.set()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.update()
        self.root.destroy()
        self.temp.cleanup()

    def test_trial_is_independent_and_can_open_while_opponent_thinks(self):
        real = self.app.spar_board
        before = real._position_key(), list(real.move_history), dict(real.captures), real.to_play
        self.app.busy = True
        with patch.object(self.app, "bot_play") as bot, patch.object(self.app, "_run") as run:
            self.app.open_trial()
            trial = self.app.trial_window
            trial.play(3, 3)
            trial.play(5, 5)
            self.assertEqual(trial.view.numbered, {(3, 3): 1, (5, 5): 2})
            trial.undo()
            trial.redo()
            bot.assert_not_called()
            run.assert_not_called()
        self.assertTrue(self.app.busy)
        self.assertEqual((real._position_key(), real.move_history, real.captures, real.to_play), before)
        self.assertEqual(self.app.spar_result, "")
        self.assertTrue(self.app.spar_active)
        trial.destroy()
        self.assertEqual(real._position_key(), before[0])

    def test_reopen_reuses_window_until_closed_then_copies_current_board(self):
        self.app.open_trial()
        first = self.app.trial_window
        first.play(3, 3)
        self.app.open_trial()
        self.assertIs(self.app.trial_window, first)
        first.destroy()
        self.app.spar_board.play("B", 4, 4)
        self.app.open_trial()
        self.assertIsNot(self.app.trial_window, first)
        self.assertEqual(self.app.trial_window.session.origin_turn, 3)
        self.assertEqual(self.app.trial_window.session.cursor, 0)

    def test_reload_requires_consent_and_does_not_change_formal_game(self):
        self.app.open_trial()
        trial = self.app.trial_window
        trial.play(3, 3)
        self.app.spar_board.play("B", 4, 4)
        before = self.app.spar_board._position_key()
        self.assertEqual(trial.session.board.grid[4][4], 0)
        with patch("yehu_style_go.trial_window.messagebox.askyesno", return_value=False):
            trial.reload()
        self.assertEqual(trial.session.cursor, 1)
        with patch("yehu_style_go.trial_window.messagebox.askyesno", return_value=True):
            trial.reload()
        self.assertEqual(trial.session.origin_turn, 3)
        self.assertEqual(trial.session.cursor, 0)
        self.assertEqual(trial.session.board._position_key(), before)
        trial.play(5, 5)
        with patch("yehu_style_go.trial_window.messagebox.askyesno", return_value=True):
            trial.reset()
        self.assertEqual(trial.session.board._position_key(), before)
        self.assertEqual(self.app.spar_board._position_key(), before)

    def test_manual_color_and_illegal_move_feedback(self):
        self.app.open_trial()
        trial = self.app.trial_window
        trial.color_var.set("白棋")
        trial.play(3, 3)
        self.assertEqual(trial.session.moves[-1].color, "W")
        trial.play(3, 3)
        self.assertIn("已有棋子", trial.message.get())
        self.assertEqual(trial.session.cursor, 1)
        trial.play(None, None)
        self.assertTrue(trial.session.moves[-1].is_pass)
        self.assertEqual(len(self.app.spar_board.move_history), 2)

    def test_no_game_does_not_open_trial_window(self):
        self.app.spar_board = GoBoard()
        self.app.spar_active = False
        with patch("yehu_style_go.app.messagebox.showinfo") as info:
            self.app.open_trial()
        info.assert_called_once()
        self.assertIsNone(self.app.trial_window)
