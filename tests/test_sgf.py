import tempfile
import unittest
from pathlib import Path

from yehu_style_go.sgf import SgfParseError, parse_sgf, parse_sgf_file

SAMPLE = """(;GM[1]FF[4]SZ[19]DT[2026-10-27]
PB[测试棋手]PW[V370684007]BR[10级]WR[10级]
KM[375]HA[0]RU[Chinese]AP[foxwq]RE[B+0]
;B[pd];W[dd];B[];W[tt])"""


class SgfTests(unittest.TestCase):
    def test_parse_fox_and_normalize_komi(self):
        game = parse_sgf(SAMPLE)
        self.assertEqual(game.black_player, "测试棋手")
        self.assertEqual(game.white_player, "V370684007")
        self.assertEqual(game.komi, 7.5)
        self.assertEqual(game.properties["KM"], ["375"])
        self.assertEqual(game.color_for("测试棋手"), "B")
        self.assertTrue(game.did_player_win("测试棋手"))
        self.assertEqual((game.moves[0].x, game.moves[0].y), (15, 3))
        self.assertTrue(game.moves[2].is_pass)
        self.assertTrue(game.moves[3].is_pass)

    def test_reads_utf8_and_gbk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中文棋谱.sgf"
            for encoding in ("utf-8", "gb18030"):
                path.write_bytes(SAMPLE.encode(encoding))
                game = parse_sgf_file(path)
                self.assertEqual(game.black_player, "测试棋手")
                self.assertEqual(len(game.sha256), 64)

    def test_reads_utf16_and_utf32_with_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "encoded.sgf"
            for encoding in ("utf-16", "utf-32"):
                path.write_bytes(SAMPLE.encode(encoding))
                game = parse_sgf_file(path)
                self.assertEqual(game.black_player, "测试棋手")
                self.assertEqual(len(game.moves), 4)

    def test_first_variation(self):
        game = parse_sgf("(;SZ[19];B[dd](;W[pq];B[pp])(;W[qq]))")
        self.assertEqual([move.sgf_coord for move in game.moves], ["dd", "pq", "pp"])

    def test_setup_ranges(self):
        game = parse_sgf("(;SZ[9]AB[aa:bb]AW[cc];W[dd])")
        self.assertEqual(set(game.setup_black), {(0, 0), (0, 1), (1, 0), (1, 1)})
        self.assertEqual(game.setup_white, [(2, 2)])

    def test_escaped_values_and_duplicate_ap(self):
        game = parse_sgf(r"(;SZ[19]C[a\]b\\c]AP[GNU Go]AP[foxwq]KM[375];B[aa])")
        self.assertEqual(game.first_property("C"), "a]b\\c")
        self.assertEqual(game.komi, 7.5)

    def test_standard_komi_unchanged(self):
        self.assertEqual(parse_sgf("(;KM[7.5]AP[foxwq])").komi, 7.5)

    def test_white_identity(self):
        game = parse_sgf("(;PB[其他人]PW[测试棋手]RE[B+R])")
        self.assertEqual(game.color_for("测试棋手"), "W")
        self.assertFalse(game.did_player_win("测试棋手"))

    def test_fox_handicap_setup_in_separate_initial_nodes(self):
        game = parse_sgf("(;SZ[19]HA[2]PB[我]PW[甲];AB[pd];AB[dp];W[pp];B[dd])")
        self.assertEqual(set(game.setup_black), {(15, 3), (3, 15)})
        self.assertEqual(len(game.moves), 2)
        self.assertEqual(game.moves[0].color, "W")

    def test_initial_setup_can_remove_stones_before_first_move(self):
        game = parse_sgf("(;SZ[9]AB[aa][bb];AE[aa]AW[cc];PL[W];W[ee])")
        self.assertEqual(game.setup_black, [(1, 1)])
        self.assertEqual(game.setup_white, [(2, 2)])
        self.assertEqual(game.first_property("PL"), "W")

    def test_invalid(self):
        for sgf in ("not an sgf", "(;PB[abc", "(;SZ[19]"):
            with self.subTest(sgf=sgf), self.assertRaises(SgfParseError):
                parse_sgf(sgf)

    def test_ca_property_selects_declared_encoding(self):
        name = "臺灣棋手"
        data = f"(;CA[Big5]PB[{name}]PW[甲];B[dd])".encode("big5")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big5.sgf"
            path.write_bytes(data)
            game = parse_sgf_file(path)
        self.assertEqual(game.black_player, name)

    def test_unknown_result_is_not_counted_as_a_loss(self):
        game = parse_sgf("(;PB[甲]PW[测试棋手]RE[?])")
        self.assertEqual(game.color_for("测试棋手"), "W")
        self.assertIsNone(game.did_player_win("测试棋手"))
        game = parse_sgf("(;PB[甲]PW[测试棋手]RE[B+R])")
        self.assertFalse(game.did_player_win("测试棋手"))
