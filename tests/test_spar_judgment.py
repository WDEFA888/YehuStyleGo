import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.board import GoBoard
from yehu_style_go.engine import EngineSettings
from yehu_style_go.sgf import parse_sgf_file
from yehu_style_go.storage import Library


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class SparJudgmentTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, Library(self.folder / "db"))
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

    def wait_for_task(self):
        expired = []
        def check():
            if self.app.busy:
                self.root.after(20, check)
            else:
                self.root.quit()
        def timeout():
            expired.append(True)
            self.root.quit()
        timer = self.root.after(5000, timeout)
        self.root.after(20, check)
        self.root.mainloop()
        self.root.after_cancel(timer)
        self.assertFalse(expired)

    def end_with_passes(self):
        self.app.spar_board.play("B", None, None)
        self.app.spar_board.play("W", None, None)
        self.assertTrue(self.app._check_end())
        self.assertTrue(self.app.spar_pending_score)
        self.assertFalse(self.app.spar_active)

    def test_two_passes_open_confirmation_and_export_numeric_result(self):
        self.end_with_passes()
        before = self.app.spar_board._position_key()
        self.assertEqual(self.app.spar_result, "")
        self.app.scoring_dialog.confirm()
        self.assertEqual(self.app.spar_result, "W+7.5")
        self.assertFalse(self.app.spar_pending_score)
        self.assertEqual(self.app.spar_board._position_key(), before)
        path = self.folder / "result.sgf"
        with patch("yehu_style_go.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_spar()
        game = parse_sgf_file(path)
        self.assertEqual(game.result, "W+7.5")
        self.assertEqual(len(game.moves), 4)
        self.assertIn("死子已由使用者确认", path.read_text(encoding="utf-8"))

    def test_dead_marks_toggle_whole_group_and_are_preserved_when_deferred(self):
        self.app.spar_board.play("B", 2, 3)
        self.app.spar_board.play("W", 6, 5)
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            self.app.score_spar()
        dialog = self.app.scoring_dialog
        dialog.toggle_dead(2, 2)
        self.assertEqual(dialog.score.dead_black, 2)
        self.assertEqual(dialog.view.dead_points, {(2, 2), (2, 3)})
        dialog.toggle_dead(2, 3)
        self.assertEqual(dialog.score.dead_black, 0)
        dialog.toggle_dead(2, 2)
        dialog.close()
        self.assertTrue(self.app.spar_pending_score)
        self.assertEqual(self.app.spar_result, "")
        self.assertIsNone(self.app.spar_score)
        self.app.score_spar()
        self.assertEqual(self.app.scoring_dialog.score.dead_black, 2)
        self.app.scoring_dialog.confirm()
        path = self.folder / "dead-marked.sgf"
        with patch("yehu_style_go.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_spar()
        self.assertIn("MA", path.read_text(encoding="utf-8"))
        self.assertEqual(len(parse_sgf_file(path).moves), 4)
        self.assertEqual(self.app.spar_board.grid[2][2], 1)

    def test_resuming_removes_only_the_two_ending_passes(self):
        self.end_with_passes()
        self.app.scoring_dialog.resume()
        self.assertTrue(self.app.spar_active)
        self.assertFalse(self.app.spar_pending_score)
        self.assertEqual(len(self.app.spar_board.move_history), 2)
        self.assertEqual(self.app.spar_result, "")
        self.assertEqual(self.app.spar_board.to_play, "B")

    def test_shape_judgment_requests_ownership_and_does_not_play_a_move(self):
        self.app.spar_board = GoBoard(9)
        self.app.spar_board.play("B", 2, 2)
        self.app.human_color = "W"
        before = list(self.app.spar_board.move_history)
        engines = []

        class FakeKataGo:
            def __init__(self, *args, **kwargs):
                self.request = None
                self.queries = []
                engines.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def position_request(self, *args, **kwargs):
                self.request = {}
                return self.request

            def query(self, request, cancel=None):
                self.queries.append(request)
                return [{"rootInfo": {"winrate": 0.7, "scoreLead": 4.2},
                         "ownership": [0] * 81}]

        with patch.object(self.app, "_engine_ready", return_value=True), \
             patch("yehu_style_go.app.KataGo", FakeKataGo), \
             patch("yehu_style_go.app.messagebox.showinfo") as info:
            self.app.assess_spar()
            self.wait_for_task()
            request = engines[0].queries[0]
            self.assertTrue(request["includeOwnership"])
            self.assertGreaterEqual(request["maxVisits"], 400)
            self.assertIn("30.0%", self.app.assessment_text.get())
            self.assertIn("落后 4.2", self.app.assessment_text.get())
            info.assert_called_once()
        self.assertEqual(self.app.spar_board.move_history, before)
        self.assertEqual(self.app.spar_result, "")
        with patch.object(self.app, "bot_play"):
            self.app.human_play(3, 3)
        self.assertIsNone(self.app.spar_estimate)
        self.assertIn("棋盘已变化", self.app.assessment_text.get())

    def test_missing_engine_does_not_fabricate_an_estimate(self):
        # The portable share package may contain a bundled engine; this test is
        # specifically about the no-engine path.
        self.app.settings = EngineSettings()
        with patch("yehu_style_go.app.messagebox.showinfo"), \
             patch("yehu_style_go.app.KataGo") as engine:
            self.app.assess_spar()
            engine.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.spar_estimate)

    def test_unsaved_final_result_is_protected_on_exit(self):
        self.end_with_passes()
        self.app.scoring_dialog.confirm()
        self.assertFalse(self.app.spar_saved)
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=False) as ask:
            self.app.close()
            ask.assert_called_once()
        self.assertFalse(self.app.closed)
        path = self.folder / "saved.sgf"
        with patch("yehu_style_go.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_spar()
        self.assertTrue(self.app.spar_saved)

    def test_resignation_cannot_be_overwritten_by_area_count(self):
        self.app.spar_active = False
        self.app.spar_result = "W+R"
        with patch("yehu_style_go.app.messagebox.showinfo"):
            self.app.score_spar()
        self.assertIsNone(self.app.scoring_dialog)
        self.assertEqual(self.app.spar_result, "W+R")
