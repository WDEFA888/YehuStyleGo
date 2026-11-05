import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.legends import (
    SELF_STYLE, collect_games, load_index, load_model, register, remove,
)
from yehu_style_go.storage import Library
from yehu_style_go.style import StyleModel


class LegendStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / "data"
        self.directory.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_sgf(self, name: str, black: str, white: str) -> Path:
        path = self.root / name
        path.write_text(
            f"(;GM[1]FF[4]SZ[19]PB[{black}]PW[{white}];B[dd];W[pp])",
            encoding="utf-8",
        )
        return path

    def test_collect_filters_player_and_deduplicates(self):
        first = self.write_sgf("one.sgf", "名手甲", "对手")
        duplicate = self.root / "copy.sgf"
        duplicate.write_bytes(first.read_bytes())
        other = self.write_sgf("two.sgf", "别人", "对手")
        games, errors = collect_games([first, duplicate, other], "名手甲")
        self.assertEqual(len(games), 1)
        self.assertEqual(errors, [])

    def test_register_load_and_remove(self):
        model = StyleModel("名手甲", game_count=25, move_count=300)
        entries = register(self.directory, model, self.root, [])
        self.assertEqual(entries[0]["name"], "名手甲")
        self.assertEqual(load_index(self.directory)[0]["game_count"], 25)
        loaded = load_model(self.directory, entries[0])
        self.assertEqual(loaded.player, "名手甲")
        self.assertEqual(loaded.move_count, 300)
        remaining = remove(self.directory, entries[0], entries)
        self.assertEqual(remaining, [])
        self.assertEqual(load_index(self.directory), [])

    def test_corrupt_index_is_ignored(self):
        (self.directory / "legend-styles.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(load_index(self.directory), [])


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class LegendDesktopTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.library.set_setting("player", "我")
        model = StyleModel("名手甲", game_count=25, move_count=300)
        register(self.library.directory, model, self.root, [])
        self.root_window = tk.Tk()
        self.root_window.withdraw()
        self.app = Application(self.root_window, self.library)
        self.root_window.update()

    def tearDown(self):
        self.app.closed = True
        self.app.cancel.set()
        for timer in self.root_window.tk.call("after", "info"):
            self.root_window.after_cancel(timer)
        self.root_window.update()
        self.root_window.destroy()
        self.temp.cleanup()

    def test_selecting_a_legend_updates_the_next_game(self):
        self.assertEqual(self.app.opponent_var.get(), SELF_STYLE)
        self.app.opponent_var.set("名手甲")
        self.app._apply_opponent_selection()
        self.assertIsNotNone(self.app.legend_model)
        self.assertIn("名手甲", self.app.opponent_title.get())
        self.assertEqual(str(self.app.match_strength_check.cget("state")), "disabled")

        with patch.object(self.app, "bot_play") as bot:
            self.app.new_spar()
            bot.assert_not_called()
        self.assertEqual(self.app.spar_legend_name, "名手甲")
        self.assertIs(self.app.spar_model, self.app.legend_model)
        self.assertEqual(self.app.spar_opponent_label, "影棋-名手甲")
        self.assertIn("名手风格", self.app.spar_mode)

    def test_importing_a_legend_trains_and_selects_it(self):
        folder = self.root / "legend-sgf"
        folder.mkdir()
        (folder / "game.sgf").write_text(
            "(;GM[1]FF[4]SZ[19]PB[名手乙]PW[对手];B[dd];W[pp];B[pd])",
            encoding="utf-8",
        )
        with patch("yehu_style_go.app.simpledialog.askstring", return_value="名手乙"),              patch("yehu_style_go.app.filedialog.askdirectory", return_value=str(folder)):
            self.app.import_legend()
            expired = []

            def check():
                if self.app.busy:
                    self.root_window.after(20, check)
                else:
                    self.root_window.quit()

            def timeout():
                expired.append(True)
                self.app.cancel.set()
                self.root_window.quit()

            self.root_window.after(20, check)
            self.root_window.after(10000, timeout)
            self.root_window.mainloop()
            self.assertFalse(expired)
        self.assertEqual(self.app.opponent_var.get(), "名手乙")
        self.assertIsNotNone(self.app.legend_model)
        self.assertEqual(self.app.legend_model.player, "名手乙")
        self.assertIn("名手乙", self.app._legend_choices())
        self.assertIn("名手乙", self.app.status.get())

    def test_batch_import_trains_multiple_legends(self):
        folder = self.root / "batch-sgf"
        folder.mkdir()
        for index, name in enumerate(("名手乙", "名手丙")):
            (folder / f"game-{index}.sgf").write_text(
                f"(;GM[1]FF[4]SZ[19]PB[{name}]PW[对手];B[dd];W[pp];B[pd])",
                encoding="utf-8",
            )
        with patch("yehu_style_go.app.simpledialog.askstring",
                   return_value="名手乙、名手丙"),              patch("yehu_style_go.app.filedialog.askdirectory", return_value=str(folder)):
            self.app.import_legends_batch()
            expired = []

            def check():
                if self.app.busy:
                    self.root_window.after(20, check)
                else:
                    self.root_window.quit()

            def timeout():
                expired.append(True)
                self.app.cancel.set()
                self.root_window.quit()

            self.root_window.after(20, check)
            self.root_window.after(10000, timeout)
            self.root_window.mainloop()
            self.assertFalse(expired)
        choices = self.app._legend_choices()
        self.assertIn("名手乙", choices)
        self.assertIn("名手丙", choices)
        self.assertEqual(self.app.opponent_var.get(), "名手乙")
        self.assertIsNotNone(self.app.legend_model)
        self.assertIn("批量训练完成", self.app.status.get())

    def test_batch_import_deduplicates_identical_sgf_files(self):
        folder = self.root / "batch-dup"
        folder.mkdir()
        sgf = "(;GM[1]FF[4]SZ[19]PB[名手丁]PW[对手];B[dd];W[pp];B[pd])"
        for index in range(3):
            (folder / f"dup-{index}.sgf").write_text(sgf, encoding="utf-8")
        with patch("yehu_style_go.app.simpledialog.askstring",
                   return_value="名手丁"),              patch("yehu_style_go.app.filedialog.askdirectory", return_value=str(folder)):
            self.app.import_legends_batch()
            expired = []

            def check():
                if self.app.busy:
                    self.root_window.after(20, check)
                else:
                    self.root_window.quit()

            def timeout():
                expired.append(True)
                self.app.cancel.set()
                self.root_window.quit()

            self.root_window.after(20, check)
            self.root_window.after(10000, timeout)
            self.root_window.mainloop()
            self.assertFalse(expired)
        self.assertIsNotNone(self.app.legend_model)
        self.assertEqual(self.app.legend_model.player, "名手丁")
        self.assertEqual(self.app.legend_model.game_count, 1)
        self.assertEqual(self.app.legend_model.move_count, 2)

    def test_deleting_a_legend_resets_the_selector(self):
        self.app.opponent_var.set("名手甲")
        self.app._apply_opponent_selection()
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            self.app.delete_current_legend()
        self.assertEqual(self.app.opponent_var.get(), SELF_STYLE)
        self.assertIsNone(self.app.legend_model)
        self.assertEqual(self.app._legend_choices(), (SELF_STYLE,))
        model_files = [
            path for path in self.library.directory.glob("legend-*.json")
            if path.name != "legend-styles.json"
        ]
        self.assertEqual(model_files, [])
        self.assertEqual(load_index(self.library.directory), [])
