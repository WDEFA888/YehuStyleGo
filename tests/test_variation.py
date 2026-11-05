import os
import tempfile
import unittest
from pathlib import Path

from yehu_style_go import theme
from yehu_style_go.board import gtp_to_xy
from yehu_style_go.storage import Library


@unittest.skipUnless(os.name == "nt", "Requires Windows Tk")
class VariationReviewTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from yehu_style_go.app import Application

        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.library = Library(self.folder / "db")
        self.library.set_setting("player", "我")
        sgf = self.folder / "game.sgf"
        sgf.write_text(
            "(;GM[1]FF[4]SZ[19]PB[我]PW[对手];B[dd];W[pp];B[dp])",
            encoding="utf-8",
        )
        self.library.import_paths([sgf])
        self.game = self.library.games("我", 10)[0]
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, self.library)
        positions = []
        for turn in range(4):
            move_infos = []
            if turn == 0:
                move_infos = [
                    {"move": "Q16", "order": 0, "scoreLead": 0, "winrate": 0.55,
                     "visits": 120, "pv": ["Q16", "R17"]},
                    {"move": "D4", "order": 1, "scoreLead": -3, "winrate": 0.52,
                     "visits": 90, "pv": ["D4"]},
                    {"move": "Q4", "order": 2, "scoreLead": -7, "winrate": 0.45,
                     "visits": 60, "pv": ["Q4"]},
                    {"move": "C3", "order": 3, "scoreLead": -1, "winrate": 0.54,
                     "visits": 40, "pv": ["C3"]},
                ]
            positions.append({
                "turnNumber": turn,
                "rootInfo": {"winrate": 0.5, "scoreLead": 0},
                "moveInfos": move_infos,
            })
        self.library.save_analysis(
            self.game.database_id,
            self.app.settings.key,
            {"positions": positions, "perspective": "B"},
        )
        self.app.load_game(self.library.game(self.game.database_id))
        self.root.update()

    def tearDown(self):
        self.app.closed = True
        self.app.cancel.set()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.update()
        self.root.destroy()
        self.temp.cleanup()

    def test_selection_marks_best_move_and_variation_overlay_can_return(self):
        self.assertEqual([row["turn"] for row in self.app.rows], [1, 3])
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.assertEqual(self.app.turn, 1)
        self.assertEqual(self.app.review_board.marks, {best: "best"})
        self.assertIn("推荐变化", self.app.pv_text.get("1.0", "end-1c"))

        self.app._review_board_click(*best)
        self.assertEqual(self.app.turn, 0)
        self.assertIsNotNone(self.app.review_candidates)
        self.assertIsNone(self.app.review_variation)
        self.assertIn("KataGo 候选", self.app.pv_text.get("1.0", "end-1c"))
        self.assertEqual(self.app.review_board.candidates[best], "1")
        actual = gtp_to_xy("D16", 19)
        self.assertEqual(self.app.review_board.marks[actual], "actual")

        self.app._review_board_click(*best)
        self.assertIsNone(self.app.review_candidates)
        self.assertIsNotNone(self.app.review_variation)
        self.assertEqual(self.app.review_variation["step"], 1)
        self.assertIn(best, self.app.review_board.numbered)
        self.assertEqual(self.app.review_board.marks[best], "best")
        self.assertEqual(self.app.review_board.marks[actual], "actual")
        self.assertIn("红圈=实战落点", self.app.pv_text.get("1.0", "end-1c"))
        self.assertEqual(str(self.app.variation_next_button.cget("state")), "normal")
        self.app._review_navigate(1)
        self.assertEqual(self.app.review_variation["step"], 2)
        self.assertEqual(len(self.app.review_board.numbered), 2)
        self.app._review_navigate(-1)
        self.assertEqual(self.app.review_variation["step"], 1)

        self.app.return_to_actual()
        self.assertIsNone(self.app.review_variation)
        self.assertIsNone(self.app.review_candidates)
        self.assertEqual(self.app.turn, 1)
        self.assertEqual(self.app.review_board.marks, {})
        self.assertEqual(self.app.turn_text.get(), "1 / 3 手")

    def test_candidate_severity_colors_winrates_and_hover_preview(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.app._review_board_click(*best)
        state = self.app.review_candidates
        self.assertEqual(len(state["ordered"]), 4)
        self.assertEqual(state["points"][best]["move"], "Q16")
        board = self.app.review_board
        self.assertEqual(board.candidate_kinds[best], "best")
        self.assertEqual(board.candidate_kinds[gtp_to_xy("C3", 19)], "good")
        self.assertEqual(board.candidate_kinds[gtp_to_xy("D4", 19)], "warn")
        self.assertEqual(board.candidate_kinds[gtp_to_xy("Q4", 19)], "bad")
        self.assertEqual(board.candidate_subs[best], "55%")
        text = self.app.pv_text.get("1.0", "end-1c")
        self.assertIn("悬停编号预览变化", text)
        self.assertIn("访问 120", text)

        # Hovering previews the candidate's own PV without moving the position.
        self.app._review_board_hover(best)
        self.assertEqual([(color, x, y) for color, x, y in board.preview],
                         [("B", *best), ("W", *gtp_to_xy("R17", 19))])
        self.assertEqual(board.hover_candidate, best)
        self.assertEqual(self.app.turn, 0)
        # The matching side-list row is highlighted while hovering.
        self.assertTrue(self.app.pv_text.tag_ranges("cand_hover"))
        self.app._review_board_hover(None)
        self.assertEqual(board.preview, [])
        self.assertIsNone(board.hover_candidate)
        self.assertFalse(self.app.pv_text.tag_ranges("cand_hover"))

        # A queued TreeviewSelect for the same row must not cancel the overlay.
        self.app.select_report_move()
        self.assertIsNotNone(self.app.review_candidates)
        self.assertIn("KataGo 候选", self.app.pv_text.get("1.0", "end-1c"))

    def test_variation_list_wheel_and_escape(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.app._review_board_click(*best)
        # Digit shortcut enters the candidate's variation (1 = Q16).
        self.assertTrue(self.app._review_board_key("1"))
        self.assertIsNotNone(self.app.review_variation)
        text = self.app.pv_text.get("1.0", "end-1c")
        self.assertIn("变化手顺", text)
        self.assertIn("1 · 黑 Q16", text)
        self.assertIn("2 · 白 R17", text)
        # Each move line is a clickable tag, and the start too.
        self.assertTrue(self.app.pv_text.tag_ranges("varmove0"))
        self.assertTrue(self.app.pv_text.tag_ranges("varmove2"))
        self.assertIn("varmove2", self.app._pv_bind_tags)
        self.assertTrue(self.app.pv_text.tk.call(
            self.app.pv_text._w, "tag", "bind", "varmove2", "<Button-1>"))
        self.assertEqual(self.app.pv_text.cget("state"), "disabled")

        self.app._render_variation(0)
        self.assertIn("▶ 起点", self.app.pv_text.get("1.0", "end-1c"))
        # The next move is ghosted, not played.
        self.app._render_variation(1)
        self.assertEqual(len(self.app.review_board.preview), 1)
        # The next-move ghost is numbered as move 2, not restarted at 1.
        self.assertEqual(self.app.review_board.preview_offset, 1)
        self.assertIn("▶ 1 · 黑 Q16", self.app.pv_text.get("1.0", "end-1c"))
        # Mouse motion in variation mode must not erase the next-move ghost.
        self.app._review_board_hover(None)
        self.assertEqual(len(self.app.review_board.preview), 1)

        class Wheel:
            delta = -120

        self.app._review_wheel(Wheel())
        self.assertEqual(self.app.review_variation["step"], 2)
        self.assertTrue(self.app._review_board_key("Home"))
        self.assertEqual(self.app.review_variation["step"], 0)
        self.assertTrue(self.app._review_board_key("End"))
        self.assertEqual(self.app.review_variation["step"], 2)
        self.assertTrue(self.app._review_board_key("Escape"))
        self.assertIsNone(self.app.review_variation)
        self.assertEqual(self.app.turn, 1)

    def test_variation_analysis_request_perspective_and_cache(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.app._review_board_click(*best)
        self.app._review_board_key("1")
        state = self.app.review_variation
        self.assertIn("分析此变化", self.app.pv_text.get("1.0", "end-1c"))

        request, start_turn, count = self.app._variation_query(state)
        self.assertEqual(start_turn, 0)
        self.assertEqual(count, 2)
        # The variation starts before move 1, so there is no prefix move yet.
        self.assertEqual(request["moves"], [["B", "Q16"], ["W", "R17"]])
        self.assertEqual(request["analyzeTurns"], [0, 1, 2])
        self.assertEqual(request["boardXSize"], 19)
        self.assertEqual(request["maxVisits"], self.app.settings.visits)

        responses = [
            {"turnNumber": 0, "rootInfo": {"winrate": 0.40, "scoreLead": 1.0}},
            {"turnNumber": 1, "rootInfo": {"winrate": 0.55, "scoreLead": 2.0}},
            {"turnNumber": 2, "rootInfo": {"winrate": 0.35, "scoreLead": -1.0}},
        ]
        analysis = self.app._apply_variation_analysis(state, responses, start_turn)
        self.assertAlmostEqual(analysis[0]["winrate"], 0.40)
        self.assertAlmostEqual(analysis[1]["score"], 2.0)
        self.assertAlmostEqual(analysis[2]["score"], -1.0)
        self.assertEqual(self.app._variation_line_color(analysis, 1, False),
                         theme.SUCCESS)
        self.assertEqual(self.app._variation_line_color(analysis, 2, False),
                         theme.DANGER)
        # The current row keeps its severity color on top of the highlight.
        self.assertEqual(self.app._variation_line_color(analysis, 2, True),
                         theme.DANGER)

        state["analysis"] = analysis
        self.app._render_variation(1)
        text = self.app.pv_text.get("1.0", "end-1c")
        self.assertIn("本人胜率 40.0%", text)
        self.assertIn("胜率 55.0%", text)
        self.assertIn("较上步 +1.0", text)
        self.assertEqual(str(self.app.variation_analyze_button.cget("text")),
                         "重新分析此变化")

        self.app.variation_cache[self.app._variation_cache_key(state)] = analysis
        self.app.return_to_actual()
        self.app.move_table.selection_set("1")
        self.app._review_board_click(*best)
        self.app._review_board_key("1")
        self.assertIn("analysis", self.app.review_variation)
        self.assertIn("胜率 55.0%", self.app.pv_text.get("1.0", "end-1c"))

    def test_candidates_follow_current_turn_instead_of_stale_selection(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        self.app.set_turn(3)
        self.assertEqual(self.app._review_report_row()["turn"], 3)
        self.assertNotIn("第 1 手", self.app.pv_text.get("1.0", "end-1c"))

    def test_unknown_pv_token_stops_variation_instead_of_becoming_a_pass(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.app._review_board_click(*best)
        candidate = self.app.review_candidates["points"][best]
        candidate["pv"] = ["ZZ"]
        self.app.start_candidate_variation(candidate)
        self.assertIsNotNone(self.app.review_variation)
        self.assertEqual(self.app.review_variation["step"], 0)
        self.assertEqual(self.app.review_variation["moves"], [])
        self.assertEqual(self.app.review_variation["pv_coords"], [])

    def test_explicit_pass_in_pv_is_kept(self):
        self.app.move_table.selection_set("1")
        self.app.select_report_move()
        best = gtp_to_xy("Q16", 19)
        self.app._review_board_click(*best)
        candidate = self.app.review_candidates["points"][best]
        candidate["pv"] = ["Q16", "pass"]
        self.app.start_candidate_variation(candidate)
        self.assertEqual(self.app.review_variation["step"], 1)
        self.assertEqual(len(self.app.review_variation["moves"]), 2)
        self.assertEqual(self.app.review_variation["moves"][1][2], None)
        self.assertEqual(self.app.review_variation["pv_coords"], ["Q16", "pass"])
