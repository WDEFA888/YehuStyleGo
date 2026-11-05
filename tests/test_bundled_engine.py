import tempfile
import unittest
from pathlib import Path

from yehu_style_go.engine import find_bundled_engine


class BundledEngineTests(unittest.TestCase):
    def test_finds_engine_and_model_in_a_portable_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "引擎"
            (root / "katago").mkdir(parents=True)
            (root / "models").mkdir()
            executable = root / "katago" / "katago.exe"
            model = root / "models" / "kata1-test.bin.gz"
            executable.write_bytes(b"exe")
            model.write_bytes(b"model")
            self.assertEqual(find_bundled_engine([root]), (executable, model))
            self.assertEqual(find_bundled_engine([Path(directory) / "missing", root]),
                             (executable, model))

    def test_missing_model_or_engine_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "katago.exe").write_bytes(b"exe")
            self.assertIsNone(find_bundled_engine([root]))
            (root / "model.bin.gz").write_bytes(b"model")
            self.assertIsNotNone(find_bundled_engine([root]))


if __name__ == "__main__":
    unittest.main()
