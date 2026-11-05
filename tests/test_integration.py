import random
import tempfile
import unittest
from pathlib import Path

from yehu_style_go.board import GoBoard, gtp_to_xy
from yehu_style_go.engine import EngineSettings, move_report
from yehu_style_go.sgf import parse_sgf
from yehu_style_go.storage import Library, scan_folder
from yehu_style_go.style import StyleModel, choose_move, train_style


class IntegrationTests(unittest.TestCase):
    def test_import_deduplicate_latest_and_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = Library(root / "db")
            a = root / "a.sgf"
            b = root / "b.sgf"
            a.write_text("(;SZ[9]PB[我]PW[甲]DT[2026-10-01];B[cc])", encoding="utf-8")
            b.write_text("(;SZ[9]PB[乙]PW[我]DT[2026-10-02];B[dd])", encoding="utf-8")
            first = library.import_paths([a, b])
            self.assertEqual(first["added"], 2)
            self.assertEqual(library.import_paths([a])["duplicates"], 1)
            self.assertEqual(library.games("我", 1)[0].played_at, "2026-10-02")
            self.assertEqual(library.games("不存在"), [])
            self.assertEqual(len(scan_folder(root)), 2)
            game = library.games()[0]
            library.save_analysis(game.database_id, "key", {"positions": []})
            self.assertEqual(library.analysis(game.database_id, "key"), {"positions": []})
            library.set_setting("player", "我")
            self.assertEqual(library.setting("player"), "我")

    def test_train_only_owner_moves_and_model_roundtrip(self):
        game = parse_sgf(
            "(;SZ[9]PB[我]PW[对手]RE[B+R];B[cc];W[gg];B[cg];W[gc];B[ee])"
        )
        model = train_style([game], "我")
        self.assertEqual(model.game_count, 1)
        self.assertEqual(model.move_count, 3)
        self.assertEqual(model.statistics["wins"], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            model.save(path)
            loaded = StyleModel.load(path)
            self.assertEqual(loaded.weights, model.weights)
            self.assertEqual(loaded.player, "我")

    def test_bot_legal_and_does_not_mutate_board(self):
        board = GoBoard(9)
        board.play("B", 2, 2)
        model = StyleModel("我")
        before = board._position_key()
        point = choose_move(board, "W", model, rng=random.Random(1))
        self.assertTrue(board.is_legal("W", *point))
        self.assertEqual(board._position_key(), before)

    def test_analysis_perspective_white(self):
        game = parse_sgf("(;SZ[19]PB[对手]PW[我];B[dd];W[pp])")
        analysis = {"positions": [
            {"turnNumber": 1, "rootInfo": {"scoreLead": 2, "winrate": 0.6},
             "moveInfos": [{"move": "Q4", "order": 0, "pv": ["Q4", "D4"]}]},
            {"turnNumber": 2, "rootInfo": {"scoreLead": 7, "winrate": 0.8}},
        ]}
        rows = move_report(game, analysis, "我")
        self.assertEqual(rows[0]["loss"], 5)
        self.assertEqual(rows[0]["score"], -7)
        self.assertAlmostEqual(rows[0]["winrate"], 0.2)

    def test_engine_candidates_never_pick_a_blunder_even_at_high_imitation(self):
        board = GoBoard(19)
        board.play("B", 3, 3)
        analysis = {
            "rootInfo": {"currentPlayer": "W", "scoreLead": 0},
            "policy": [0.0] * 361,
            "moveInfos": [
                {"move": "Q4", "order": 0},
                {"move": "K10", "order": 1},
                {"move": "D4", "order": 30},
            ],
        }
        model = StyleModel("我")
        rng = random.Random(7)
        chosen = {choose_move(board, "W", model, analysis, imitation=0.9, strength=0.5, rng=rng)
                  for _ in range(200)}
        self.assertNotIn(gtp_to_xy("D4", 19), chosen)
        self.assertTrue(chosen.issubset({gtp_to_xy("Q4", 19), gtp_to_xy("K10", 19)}))

    def test_missing_engine_is_explicit(self):
        with self.assertRaisesRegex(RuntimeError, "选择 KataGo"):
            EngineSettings().validate()
