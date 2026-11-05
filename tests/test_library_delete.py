import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.storage import Library


class LibraryDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "db")
        for index, coordinate in enumerate(("dd", "pp", "dp")):
            path = self.root / f"game-{index}.sgf"
            path.write_text(
                f"(;GM[1]FF[4]SZ[19]PB[我]PW[甲];B[{coordinate}];W[qq])", encoding="utf-8")
            self.library.import_paths([path])
        self.games = self.library.games("我", 10)

    def tearDown(self):
        self.temp.cleanup()

    def test_analyzed_game_ids_are_filtered_by_engine_key(self):
        first = self.games[0]
        self.library.save_analysis(first.database_id, "engine-a", {"positions": []})
        self.assertEqual(self.library.analyzed_game_ids("engine-a"), {first.database_id})
        self.assertEqual(self.library.analyzed_game_ids("engine-b"), set())

    def test_delete_games_removes_selected_games_and_their_analysis(self):
        selected = self.games[:2]
        keep = self.games[2]
        for game in self.games:
            self.library.save_analysis(
                game.database_id, "engine", {"positions": [], "marker": game.database_id})
        deleted = self.library.delete_games([game.database_id for game in selected])
        self.assertEqual(deleted, 2)
        self.assertEqual(self.library.game_count(), 1)
        self.assertIsNone(self.library.analysis(selected[0].database_id))
        self.assertIsNone(self.library.analysis(selected[1].database_id))
        self.assertEqual(
            self.library.analysis(keep.database_id)["marker"], keep.database_id)

    def test_clear_games_removes_all_games_and_analysis_but_keeps_settings(self):
        self.library.set_setting("player", "我")
        for game in self.games:
            self.library.save_analysis(game.database_id, "engine", {"positions": []})
        removed = self.library.clear_games()
        self.assertEqual(removed, {"games": 3, "analyses": 3})
        self.assertEqual(self.library.game_count(), 0)
        self.assertEqual(self.library.setting("player"), "我")


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class LibraryDeletionDesktopTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "db")
        self.library.set_setting("player", "我")
        self.library.set_setting("watch_enabled", True)
        self.library.set_setting("watch_folder", str(self.root))
        for index, coordinate in enumerate(("dd", "pp")):
            path = self.root / f"game-{index}.sgf"
            path.write_text(
                f"(;GM[1]FF[4]SZ[19]PB[我]PW[甲];B[{coordinate}];W[qq])",
                encoding="utf-8",
            )
            self.library.import_paths([path])
        self.root_window = tk.Tk()
        self.root_window.withdraw()
        self.app = Application(self.root_window, self.library)
        self.app.player = "我"
        self.app.refresh_library()
        self.root_window.update()

    def tearDown(self):
        self.app.closed = True
        self.app.cancel.set()
        for timer in self.root_window.tk.call("after", "info"):
            self.root_window.after_cancel(timer)
        self.root_window.update()
        self.root_window.destroy()
        self.temp.cleanup()

    def test_delete_selected_uses_the_selected_rows(self):
        self.assertEqual(self.library.game_count(), 2)
        iid = self.app.game_table.get_children()[0]
        self.app.game_table.selection_set(iid)
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            self.app.delete_selected_games()
        self.assertEqual(self.library.game_count(), 1)
        self.assertTrue(self.app.status.get().startswith("已从棋谱库删除 1 盘"))

    def test_clear_library_can_remove_models_for_handover(self):
        model_file = self.library.directory / "style-deadbeef.json"
        model_file.write_text("{}", encoding="utf-8")
        strength_file = self.library.directory / "strength-profile.json"
        strength_file.write_text("{}", encoding="utf-8")
        legend_model = self.library.directory / "legend-test.json"
        legend_model.write_text("{}", encoding="utf-8")
        legend_index = self.library.directory / "legend-styles.json"
        legend_index.write_text("[]", encoding="utf-8")
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            self.app.clear_library()
        self.assertEqual(self.library.game_count(), 0)
        self.assertFalse(model_file.exists())
        self.assertFalse(strength_file.exists())
        self.assertFalse(legend_model.exists())
        self.assertFalse(legend_index.exists())
        self.assertFalse(self.library.setting("watch_enabled"))
        self.assertEqual(self.library.setting("watch_folder"), "")
        self.assertFalse(self.app.watch_var.get())
        self.assertEqual(self.app.folder_var.get(), "")
        self.assertEqual(self.app.game_table.get_children(), ("__empty__",))
        self.assertIn("棋谱库已清空", self.app.status.get())
