import gc
import os
import tempfile
import unittest
from pathlib import Path

from yehu_style_go.storage import Library


@unittest.skipUnless(os.name == "nt", "Desktop startup regression requires Windows Tk")
class DesktopStartupTests(unittest.TestCase):
    def test_all_tabs_build_with_a_clean_database(self):
        import tkinter as tk

        from yehu_style_go.app import Application

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = None
            try:
                app = Application(root, Library(Path(directory)))
                root.update_idletasks()
                self.assertEqual(len(app.tabs.tabs()), 7)
                self.assertIn("联机对弈",
                              [app.tabs.tab(tab, "text") for tab in app.tabs.tabs()])
                self.assertEqual(app.tabs.tab(app.tabs.tabs()[0], "text"), "首页")
                self.assertEqual(app.player, "")
                self.assertIn("用户名", app.status.get())
                self.assertFalse(app.busy)
                self.assertIsNone(app.model)
            finally:
                if app:
                    app.closed = True
                    app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.destroy()

    def test_sync_action_imports_and_trains_using_verified_export_result(self):
        import tkinter as tk
        from unittest.mock import patch

        from yehu_style_go.app import Application
        from yehu_style_go.fox_sync import SyncResult

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / "record.sgf"
            path.write_text("(;SZ[19]PB[测试棋手]PW[甲];B[dd];W[pp])", encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            app = Application(root, Library(folder / "db"))
            app.player = "测试棋手"
            app.library.set_setting("player", "测试棋手")
            app.refresh_library()
            app.library.set_setting("fox_export_folder", str(folder / "exports"))
            expired = []
            try:
                with patch("yehu_style_go.app.messagebox.askyesno", return_value=True), \
                     patch("yehu_style_go.fox_sync.FoxExporter") as exporter:
                    exporter.return_value.sync.return_value = SyncResult([path], 1, 0, 1)
                    app.sync_fox_records()
                    def check():
                        if app.busy:
                            root.after(30, check)
                        else:
                            root.quit()
                    def timeout():
                        expired.append(True)
                        app.cancel.set()
                        root.quit()
                    root.after(30, check)
                    root.after(20000, timeout)
                    root.mainloop()
                    self.assertFalse(expired)
                    self.assertIsNotNone(app.model)
                    self.assertEqual(app.model.move_count, 1)
                    self.assertEqual(len(app.games), 1)
                    exporter.return_value.sync.assert_called_once()
                    self.assertEqual(exporter.return_value.sync.call_args.args[0], 100)
                    self.assertIn("已同步 1 盘", app.status.get())
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

    def test_game_limit_is_persisted_and_invalid_values_rejected(self):
        import tkinter as tk
        from unittest.mock import patch

        from yehu_style_go.app import Application

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = Application(root, Library(Path(directory)))
            try:
                app.player_var.set("测试棋手")
                app.limit_var.set("300")
                self.assertTrue(app.save_settings())
                self.assertEqual(app.game_limit, 300)
                self.assertEqual(app.library.setting("game_limit"), 300)
                app.limit_var.set("5000")
                with patch("yehu_style_go.app.messagebox.showerror") as error:
                    self.assertFalse(app.save_settings())
                error.assert_called_once()
                self.assertEqual(app.game_limit, 300)
                self.assertEqual(app.library.setting("game_limit"), 300)
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

    def test_dark_mode_toggle_persists_and_restyles_plain_tk_widgets(self):
        import tkinter as tk

        from yehu_style_go import theme
        from yehu_style_go.app import Application

        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory) / "db")
            root = tk.Tk()
            root.withdraw()
            app = Application(root, library)
            try:
                self.assertFalse(app.dark_mode)
                light_bg = theme.BG
                app.toggle_theme()
                self.assertTrue(app.dark_mode)
                self.assertTrue(theme.is_dark())
                self.assertEqual(library.setting("theme_mode"), "dark")
                self.assertNotEqual(theme.BG, light_bg)
                self.assertEqual(root.cget("background"), theme.BG)
                self.assertEqual(app.spar_view.cget("background"), theme.BOARD)
                self.assertEqual(app.profile_text.cget("background"), theme.SURFACE)
                self.assertEqual(app.chart.canvas.cget("background"), theme.SURFACE)
                self.assertEqual(app.theme_button_text.get(), "日间模式")
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

            root = tk.Tk()
            root.withdraw()
            reopened = Application(root, library)
            try:
                self.assertTrue(reopened.dark_mode)
                self.assertTrue(theme.is_dark())
                reopened.toggle_theme()
                self.assertFalse(reopened.dark_mode)
                self.assertFalse(theme.is_dark())
                self.assertEqual(library.setting("theme_mode"), "light")
            finally:
                reopened.closed = True
                reopened.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

    def test_bundled_katago_is_auto_configured(self):
        import tkinter as tk
        from unittest.mock import patch

        from yehu_style_go.app import Application

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine_root = folder / "引擎"
            (engine_root / "katago").mkdir(parents=True)
            (engine_root / "models").mkdir()
            executable = engine_root / "katago" / "katago.exe"
            model = engine_root / "models" / "kata1-test.bin.gz"
            executable.write_bytes(b"exe")
            model.write_bytes(b"model")
            root = tk.Tk()
            root.withdraw()
            try:
                with patch("yehu_style_go.app.bundled_engine_roots",
                           return_value=[engine_root]):
                    app = Application(root, Library(folder / "db"))
                self.assertTrue(app.bundled_engine)
                self.assertEqual(Path(app.settings.executable), executable)
                self.assertEqual(Path(app.settings.model), model)
                self.assertEqual(Path(app.engine_var.get()), executable)
                self.assertIn("随包 KataGo", app.status.get())
            finally:
                if "app" in locals():
                    app.closed = True
                    app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

    def test_theme_toggle_refreshes_an_open_trial_window(self):
        import tkinter as tk

        from yehu_style_go import theme
        from yehu_style_go.app import Application
        from yehu_style_go.board import GoBoard

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = Application(root, Library(Path(directory)))
            try:
                app.spar_board = GoBoard(9)
                app.spar_board.play("B", 2, 2)
                app.spar_active = True
                app.open_trial()
                trial = app.trial_window
                self.assertEqual(trial.view.cget("background"), theme.BOARD)
                app.toggle_theme()
                self.assertTrue(app.dark_mode)
                self.assertEqual(trial.view.cget("background"), theme.BOARD)
                self.assertEqual(trial.cget("background"), theme.SURFACE)
                app.toggle_theme()
                self.assertFalse(app.dark_mode)
                trial.destroy()
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()

    def test_saved_local_game_is_reviewable_and_trains_owner_moves(self):
        import tkinter as tk

        from yehu_style_go.app import Application
        from yehu_style_go.board import GoBoard
        from yehu_style_go.sgf import parse_sgf_file

        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory) / "db")
            library.set_setting("player", "我")
            root = tk.Tk()
            root.withdraw()
            app = Application(root, library)
            try:
                app.spar_board = GoBoard(9)
                app.human_color = "B"
                app.spar_active = True
                app.spar_result = "B+R"
                app.spar_board.play("B", 2, 2)
                app.spar_board.play("W", 6, 6)
                app.spar_board.play("B", 2, 6)
                app.save_and_review()

                self.assertTrue(app.spar_saved)
                files = list((library.directory / "local-games").glob("*.sgf"))
                self.assertEqual(len(files), 1)
                saved = library.game_by_sha(parse_sgf_file(files[0]).sha256)
                self.assertIsNotNone(saved)
                self.assertEqual(app.selected_game.database_id, saved.database_id)
                self.assertEqual(app.tabs.tab(app.tabs.select(), "text"), "复盘分析")
                self.assertEqual(len(library.games("我", 10)), 1)
                self.assertEqual(len(app.games), 1)

                expired = []
                gc.collect()
                app.train(select_tab=False)

                def check():
                    if app.busy:
                        root.after(20, check)
                    else:
                        root.quit()

                def timeout():
                    expired.append(True)
                    app.cancel.set()
                    root.quit()

                root.after(20, check)
                root.after(20000, timeout)
                root.mainloop()
                self.assertFalse(expired)
                self.assertIsNotNone(app.model)
                self.assertEqual(app.model.game_count, 1)
                self.assertEqual(app.model.move_count, 2)
                self.assertIn(saved.sha256, app.model.trained_hashes)
                self.assertTrue(app.status.get().startswith("棋风模型已更新"))
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()
