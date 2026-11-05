import os
import random
import tempfile
import unittest
from pathlib import Path

from yehu_style_go.board import GoBoard, gtp_to_xy
from yehu_style_go.sgf import parse_sgf
from yehu_style_go.storage import Library
from yehu_style_go.strength import (
    StrengthProfile,
    build_profile,
    load_profile,
    save_profile,
)
from yehu_style_go.style import StyleModel, choose_move


def sample_profile(**overrides):
    values = {"5": 1.0, "10": 2.0, "25": 4.0, "50": 6.0,
              "75": 8.0, "90": 12.0, "95": 15.0, "99": 20.0}
    values.update(overrides)
    return StrengthProfile("我", "key", 2, 200, 5.0, values)


def located_game():
    board = GoBoard(9)
    rng = random.Random(11)
    while len(board.move_history) < 80:
        legal = board.legal_moves(board.to_play, include_pass=False)
        if not legal:
            break
        x, y = rng.choice(legal)
        board.play(board.to_play, x, y)
    raw = "(;SZ[9]PB[我]PW[甲]" + "".join(
        f";{move.color}[{move.sgf_coord}]" for move in board.move_history) + ")"
    return parse_sgf(raw)


class StrengthProfileTests(unittest.TestCase):
    def test_quantile_caps_interpolate_and_clamp(self):
        profile = sample_profile()
        self.assertEqual(profile.cap_for(0.0), 1.0)
        self.assertEqual(profile.cap_for(0.5), 6.0)
        self.assertEqual(profile.cap_for(0.9), 12.0)
        self.assertEqual(profile.cap_for(1.0), 20.0)
        self.assertAlmostEqual(profile.cap_for(0.30), 4.4)

    def test_build_profile_reads_only_cached_analyses(self):
        game = located_game()
        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory))
            path = Path(directory) / "record.sgf"
            path.write_text(game.raw_sgf, encoding="utf-8")
            library.import_paths([path])
            stored = library.games("我")[0]
            positions, score = [], 0.0
            for turn in range(len(stored.moves) + 1):
                positions.append({
                    "turnNumber": turn,
                    "rootInfo": {"scoreLead": score, "winrate": 0.5},
                    "moveInfos": [{"move": "A1", "order": 0, "pv": []}],
                })
                if turn < len(stored.moves) and stored.moves[turn].color == "B":
                    score -= 2.0
            library.save_analysis(stored.database_id, "key", {"positions": positions})
            profile = build_profile([stored], library, "我", "key")
            self.assertIsNotNone(profile)
            self.assertEqual(profile.move_count, 40)
            self.assertEqual(profile.analyzed_games, 1)
            self.assertAlmostEqual(profile.average_loss, 2.0)
            self.assertAlmostEqual(profile.quantiles["50"], 2.0)
            self.assertIsNone(build_profile([stored], library, "我", "other-key"))

    def test_profile_roundtrip_and_engine_mismatch(self):
        profile = sample_profile()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strength-profile.json"
            save_profile(path, profile)
            self.assertEqual(load_profile(path, "我", "key"), profile)
            self.assertIsNone(load_profile(path, "别人", "key"))
            self.assertIsNone(load_profile(path, "我", "new-key"))
            path.write_text("{bad json", encoding="utf-8")
            self.assertIsNone(load_profile(path, "我", "key"))

    def test_limit_clamping_accepts_up_to_one_thousand(self):
        from yehu_style_go.app import Application
        self.assertEqual(Application._clamp_limit(300), 300)
        self.assertEqual(Application._clamp_limit("500"), 500)
        self.assertEqual(Application._clamp_limit(5000), 1000)
        self.assertEqual(Application._clamp_limit("nonsense"), 100)


class ProfileChoiceTests(unittest.TestCase):
    def analysis(self):
        return {
            "rootInfo": {"currentPlayer": "W", "scoreLead": -3},
            "moveInfos": [
                {"move": "Q4", "order": 0, "scoreLead": -3},
                {"move": "K10", "order": 1, "scoreLead": -1},
                {"move": "D4", "order": 2, "scoreLead": 7},
            ],
        }

    def test_tight_profile_keeps_the_move_inside_the_owner_error_band(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        model = StyleModel("我")
        profile = sample_profile(**{key: 0.5 for key in (
            "5", "10", "25", "50", "75", "90", "95", "99")})
        chosen = {choose_move(board, "W", model, self.analysis(), imitation=0.9,
                              strength=0.5, rng=random.Random(seed), profile=profile)
                  for seed in range(200)}
        self.assertEqual(chosen, {gtp_to_xy("Q4", 19)})

    def test_loose_profile_can_intentionally_match_a_larger_owner_loss(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        model = StyleModel("我")
        profile = sample_profile(**{key: 12.0 for key in (
            "5", "10", "25", "50", "75", "90", "95", "99")})
        chosen = {choose_move(board, "W", model, self.analysis(), imitation=0.9,
                              strength=0.5, rng=random.Random(seed), profile=profile)
                  for seed in range(50)}
        self.assertEqual(chosen, {gtp_to_xy("D4", 19)})

    def test_high_strength_with_tight_profile_keeps_the_best_move(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        model = StyleModel("我")
        profile = sample_profile(**{key: 0.5 for key in (
            "5", "10", "25", "50", "75", "90", "95", "99")})
        chosen = {choose_move(board, "W", model, self.analysis(), imitation=0.9,
                              strength=1.0, rng=random.Random(seed), profile=profile)
                  for seed in range(100)}
        self.assertEqual(chosen, {gtp_to_xy("Q4", 19)})

    def test_matching_never_uses_candidates_outside_the_engine_order_pool(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        analysis = {"moveInfos": [
            {"move": "Q4", "order": 0, "scoreLead": -3},
            {"move": "K10", "order": 1, "scoreLead": -1},
            {"move": "D4", "order": 30, "scoreLead": 7}]}
        profile = sample_profile(**{key: 5.0 for key in (
            "5", "10", "25", "50", "75", "90", "95", "99")})
        chosen = {choose_move(board, "W", StyleModel("我"), analysis, 0.9, 0.5,
                              random.Random(seed), profile=profile)
                  for seed in range(100)}
        self.assertNotIn(gtp_to_xy("D4", 19), chosen)

    def test_missing_candidate_scores_fall_back_to_engine_order(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        analysis = {"moveInfos": [
            {"move": "Q4", "order": 0}, {"move": "K10", "order": 1}, {"move": "D4", "order": 30}]}
        chosen = {choose_move(board, "W", StyleModel("我"), analysis, 0.9, 0.5,
                              random.Random(seed), profile=sample_profile())
                  for seed in range(100)}
        self.assertNotIn(gtp_to_xy("D4", 19), chosen)


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class AppStrengthMatchingTests(unittest.TestCase):
    def test_bot_turn_passes_the_active_profile_to_move_selection(self):
        import tkinter as tk
        from unittest.mock import patch

        from yehu_style_go.app import Application
        from yehu_style_go.storage import Library

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = Application(root, Library(Path(directory)))
            app.model = StyleModel("我")
            app.spar_board = GoBoard(19)
            app.human_color = "B"
            app.spar_active = True
            app.spar_saved = False
            app.spar_mode = "KataGo + 个人落子偏好"
            profile = sample_profile()
            app.strength_profile = profile
            app.strength_profile_key = app.settings.key
            app.match_strength_var.set(True)

            class FakeKataGo:
                def __init__(self, *args, **kwargs):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def position_request(self, *args, **kwargs):
                    return {}

                def query(self, request, cancel=None):
                    return [{"rootInfo": {"winrate": 0.5, "scoreLead": 0},
                             "moveInfos": [{"move": "Q16", "order": 0, "scoreLead": 0}]}]

            try:
                with patch("yehu_style_go.app.KataGo", FakeKataGo), \
                     patch("yehu_style_go.app.choose_move", return_value=(3, 3)) as chooser, \
                     patch("yehu_style_go.app.messagebox.showerror") as error:
                    app.bot_play()

                    def check():
                        if app.busy:
                            root.after(20, check)
                        else:
                            root.quit()
                    root.after(20, check)
                    root.after(5000, root.quit)
                    root.mainloop()
                    self.assertFalse(app.busy)
                    error.assert_not_called()
                    self.assertEqual(chooser.call_args.kwargs["profile"], profile)
                    self.assertEqual(app.spar_board.move_history[-1].x, 3)
                    self.assertEqual(app.spar_board.move_history[-1].y, 3)
            finally:
                app.closed = True
                app.cancel.set()
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.update()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
