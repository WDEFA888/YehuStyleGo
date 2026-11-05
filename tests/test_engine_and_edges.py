import io
import queue
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.engine import EngineError, EngineSettings, KataGo
from yehu_style_go.models import Move
from yehu_style_go.sgf import SgfParseError, parse_sgf
from yehu_style_go.storage import Library
from yehu_style_go.style import train_style


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = None
        self.stderr = None
        self.exited = False

    def poll(self):
        return 0 if self.exited else None

    def terminate(self):
        self.exited = True

    def wait(self, timeout):
        return 0


class EngineProtocolTests(unittest.TestCase):
    def session(self):
        engine = object.__new__(KataGo)
        engine.settings = EngineSettings(visits=25)
        engine.process = FakeProcess()
        engine.responses = queue.Queue()
        engine.errors = deque()
        engine.lock = threading.Lock()
        return engine

    def test_position_request_coordinate_and_limits(self):
        engine = self.session()
        request = engine.position_request([Move("B", 3, 3), Move("W", None, None)], 19, 7.5)
        self.assertEqual(request["moves"], [["B", "D16"], ["W", "pass"]])
        self.assertEqual(request["maxVisits"], 25)
        self.assertEqual(request["rules"], "chinese")
        self.assertTrue(request["includePolicy"])

    def test_out_of_order_positions_warnings_and_search_updates(self):
        engine = self.session()
        engine.responses.put({"id": "fixed", "warning": "info"})
        engine.responses.put({"id": "fixed", "turnNumber": 0, "rootInfo": {}, "isDuringSearch": True})
        engine.responses.put({"id": "other", "turnNumber": 0, "rootInfo": {}})
        engine.responses.put({"id": "fixed", "turnNumber": 1, "rootInfo": {}})
        engine.responses.put({"id": "fixed", "turnNumber": 0, "rootInfo": {}})
        with patch("yehu_style_go.engine.uuid.uuid4") as identifier:
            identifier.return_value.hex = "fixed"
            result = engine.query({}, expected=2)
        self.assertEqual([item["turnNumber"] for item in result], [0, 1])
        self.assertIn('"id": "fixed"', engine.process.stdin.getvalue())

    def test_engine_error_propagates(self):
        engine = self.session()
        engine.responses.put({"id": "fixed", "error": "invalid rules"})
        with patch("yehu_style_go.engine.uuid.uuid4") as identifier:
            identifier.return_value.hex = "fixed"
            with self.assertRaisesRegex(EngineError, "invalid rules"):
                engine.query({})

    def test_cancel_terminates_process(self):
        engine = self.session()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(EngineError, "取消"):
            engine.query({}, cancel=cancel)
        self.assertTrue(engine.process.exited)

    def test_analyze_game_requests_all_turns_and_handicap(self):
        engine = self.session()
        game = parse_sgf("(;SZ[9]HA[2]AB[cc][gg]KM[0.5];W[ee];B[cf])")
        captured = {}
        def fake_query(request, expected, progress, cancel=None):
            captured.update(request)
            self.assertEqual(expected, 3)
            return [{"turnNumber": turn, "rootInfo": {}} for turn in range(3)]
        engine.query = fake_query
        result = engine.analyze_game(game)
        self.assertEqual(captured["initialPlayer"], "W")
        self.assertEqual(captured["analyzeTurns"], [0, 1, 2])
        self.assertEqual(captured["initialStones"], [["B", "C7"], ["B", "G3"]])
        self.assertEqual(result["perspective"], "B")

    def test_initial_player_uses_pl_property_without_handicap(self):
        engine = self.session()
        game = parse_sgf("(;SZ[9]PL[W];W[ee];B[cf])")
        captured = {}
        def fake_query(request, expected, progress, cancel=None):
            captured.update(request)
            return [{"turnNumber": turn, "rootInfo": {}} for turn in range(expected)]
        engine.query = fake_query
        engine.analyze_game(game)
        self.assertEqual(captured["initialPlayer"], "W")
        self.assertEqual(captured["initialStones"], [])
        self.assertEqual(captured["moves"], [["W", "E5"], ["B", "C4"]])


class EdgeCaseTests(unittest.TestCase):
    def test_bad_coordinates_and_unsupported_edits_not_silently_replayed(self):
        for text in ("(;SZ[9];B[zz])", "(;SZ[19];B[a])", "(;SZ[19:13])",
                     "(;GM[4]SZ[19])", "(;SZ[9];B[cc];AB[dd])"):
            with self.subTest(text=text), self.assertRaises(SgfParseError):
                parse_sgf(text)

    def test_training_replays_simple_ko_snapback_without_dropping_moves(self):
        from yehu_style_go.board import GoBoard
        game = parse_sgf(
            "(;SZ[9]PB[我]PW[甲]RU[Chinese]AP[foxwq]"
            "AB[ah][af][bg]AW[bh][bi];B[ai];W[ag];B[ah])"
        )
        board = GoBoard.from_game(game)
        self.assertEqual(board._position_key(), GoBoard.from_game(game, 0)._position_key())
        model = train_style([game], "我")
        self.assertEqual(model.game_count, 1)
        self.assertEqual(model.move_count, 2)

    def test_training_error_identifies_full_source_and_rule_violation(self):
        game = parse_sgf("(;SZ[9]PB[我]PW[甲];B[aa];W[bb];B[aa])",
                         source_path="records/1234567890123456789/record.sgf")
        with self.assertRaisesRegex(ValueError, r"1234567890123456789.*第 3 手.*已有棋子"):
            train_style([game], "我")

    def test_no_moves_cannot_train(self):
        with self.assertRaisesRegex(ValueError, "没有本人落子"):
            train_style([parse_sgf("(;PB[我]PW[甲])")], "我")

    def test_no_data_in_phase_is_disclosed(self):
        model = train_style([parse_sgf("(;SZ[19]PB[我]PW[甲];B[dd];W[pp])")], "我")
        self.assertIn("没有该阶段的本人落子", model.report_text())

    def test_latest_100_by_play_date_not_import_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = Library(root / "db")
            paths = []
            for index in range(105):
                path = root / f"game-{index}.sgf"
                path.write_text(
                    f"(;SZ[19]PB[我]PW[甲]DT[2026-{index // 28 + 1:02}-{index % 28 + 1:02}];B[dd])",
                    encoding="utf-8",
                )
                paths.append(path)
            self.assertEqual(library.import_paths(list(reversed(paths)))["added"], 105)
            games = library.games("我", 100)
            self.assertEqual(len(games), 100)
            self.assertEqual(games[0].played_at, "2026-04-21")
            self.assertEqual(games[-1].played_at, "2026-01-06")

    def test_same_day_fox_records_use_record_id_not_import_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            newest = root / "[我]vs[甲]1793113395030012751.sgf"
            older_folder = root / "1793112863030010000"
            older_folder.mkdir()
            older = older_folder / "record.sgf"
            newest.write_text("(;SZ[19]AP[foxwq]PB[我]PW[甲]DT[2026-10-27];B[dd])", encoding="utf-8")
            older.write_text("(;SZ[19]AP[foxwq]PB[我]PW[乙]DT[2026-10-27];B[pp])", encoding="utf-8")
            library = Library(root / "db")
            library.import_paths([newest, older])
            self.assertEqual(library.games("我", 1)[0].white_player, "甲")

    def test_import_failure_does_not_drop_valid_game(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good, bad = root / "good.sgf", root / "bad.sgf"
            good.write_text("(;SZ[19]PB[我];B[dd])", encoding="utf-8")
            bad.write_text("not sgf", encoding="utf-8")
            result = Library(root / "db").import_paths([bad, good])
            self.assertEqual(result["added"], 1)
            self.assertEqual(len(result["errors"]), 1)
