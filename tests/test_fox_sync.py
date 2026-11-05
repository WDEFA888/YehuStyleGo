import base64
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock

from yehu_style_go.fox_sync import (
    _SAVE_TEMPLATE,
    FoxExporter,
    FoxSyncError,
    matches_save_template,
    record_id_from_filename,
    validate_export,
)


class FoxExportTests(unittest.TestCase):
    def test_extract_record_id_from_native_filename(self):
        self.assertEqual(
            record_id_from_filename("[测试棋手]vs[V370684007]1793113395030012751.sgf"),
            "1793113395030012751",
        )

    def test_reject_filename_without_unambiguous_record_id(self):
        for filename in ("record.sgf", "../../../record.sgf", "123.sgf", "1793113395030012751.sgf.exe"):
            with self.subTest(filename=filename), self.assertRaises(FoxSyncError):
                record_id_from_filename(filename)

    def test_verified_save_template_matches_itself(self):
        rgb = zlib.decompress(base64.b64decode(_SAVE_TEMPLATE))
        self.assertEqual(len(rgb), 36 * 22 * 3)
        self.assertTrue(matches_save_template(rgb))

    def test_changed_or_blank_button_is_not_clicked(self):
        self.assertFalse(matches_save_template(bytes(36 * 22 * 3)))
        self.assertFalse(matches_save_template(b"not a button"))
        self.assertFalse(matches_save_template(bytes([255]) * (36 * 22 * 3)))

    def test_export_must_belong_to_owner_and_match_move_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.sgf"
            path.write_text("(;SZ[19]PB[我]PW[对手];B[dd];W[pp])", encoding="utf-8")
            self.assertEqual(len(validate_export(path, "我", 2).moves), 2)
            with self.assertRaises(FoxSyncError):
                validate_export(path, "别人", 2)
            with self.assertRaises(FoxSyncError):
                validate_export(path, "我", 3)

    def test_invalid_limits_fail_before_any_desktop_access(self):
        exporter = object.__new__(FoxExporter)
        for limit in (0, -1, 1001):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                exporter.sync(limit)

    def test_cancel_prevents_any_native_input(self):
        exporter = object.__new__(FoxExporter)
        exporter.cancel = threading.Event()
        exporter.cancel.set()
        exporter.user = Mock()
        with self.assertRaises(FoxSyncError):
            exporter._verify_target(123)
        exporter.user.IsWindow.assert_not_called()

    def test_stale_window_prevents_native_input(self):
        exporter = object.__new__(FoxExporter)
        exporter.cancel = threading.Event()
        exporter.user = Mock()
        exporter.user.IsWindow.return_value = False
        with self.assertRaises(FoxSyncError):
            exporter._verify_target(123)
        exporter.user.SendMessageTimeoutW.assert_not_called()
