import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.engine import EngineSettings
from yehu_style_go.storage import Library


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class ReviewAnalysisTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application

        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.executable = self.folder / "katago.exe"
        self.model = self.folder / "model.bin.gz"
        self.executable.write_bytes(b"exe")
        self.model.write_bytes(b"model")
        sgf = self.folder / "game.sgf"
        sgf.write_text(
            "(;GM[1]FF[4]SZ[19]PB[我]PW[甲];B[dd];W[pp];B[pd];W[dp])",
            encoding="utf-8",
        )
        self.library = Library(self.folder / "db")
        self.library.set_setting("player", "我")
        self.library.import_paths([sgf])
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, self.library)
        self.app.settings = EngineSettings(str(self.executable), str(self.model), 120)
        self.app.player = "我"
        self.app.refresh_library()
        self.root.update()

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

    def test_review_button_analyzes_the_selected_game(self):
        self.assertIsNotNone(self.app.selected_game)
        self.assertIn("尚未分析", self.app.analysis_summary.get())
        self.assertIn("分析这盘棋", self.app.analysis_summary.get())
        self.assertEqual(str(self.app.analyze_current_button.cget("state")), "normal")
        self.assertEqual(self.app.analyze_current_button.cget("text"), "分析这盘棋")

        app = self.app

        class FakeKataGo:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def analyze_game(self, game, progress=None, cancel=None):
                positions = [
                    {
                        "turnNumber": turn,
                        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0},
                        "moveInfos": [],
                    }
                    for turn in range(len(game.moves) + 1)
                ]
                return {
                    "engine_key": app.settings.key,
                    "perspective": "B",
                    "positions": positions,
                }

        gc.collect()
        with patch("yehu_style_go.app.KataGo", FakeKataGo):
            self.app.analyze_current_button.invoke()
            self.wait_for_task()

        game = self.app.selected_game
        self.assertIsNotNone(
            self.library.analysis(game.database_id, self.app.settings.key)
        )
        self.assertEqual(len(self.app.rows), 2)
        self.assertIn("平均损失", self.app.analysis_summary.get())
        self.assertEqual(self.app.analyze_current_button.cget("text"), "重新分析")
        self.assertFalse(self.app.busy)

    def test_review_explains_when_katago_is_not_configured(self):
        self.app.settings = EngineSettings()
        self.app.load_game(self.library.game(self.app.selected_game.database_id))
        self.assertIn("尚未分析", self.app.analysis_summary.get())
        self.assertIn("配置 KataGo", self.app.analysis_summary.get())
        self.assertEqual(str(self.app.analyze_current_button.cget("state")), "normal")
