import json
from pathlib import Path
import tempfile
import unittest

from sapobox.converter.probe import inspect


class APRDetectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name) / "game"
        (self.game / "sce_sys").mkdir(parents=True)
        (self.game / "sce_sys/param.json").write_text(
            json.dumps({"titleId": "PPSA12345", "titleName": "Exemplo"}))

    def test_detects_installed_emulator(self):
        (self.game / "fakelib").mkdir()
        (self.game / "fakelib/libSceAmpr.sprx").write_bytes(b"module")
        result = inspect(self.game)
        self.assertEqual(result["kind"], "emulator")
        self.assertTrue(result["apr"])

    def test_detects_import_without_emulator(self):
        (self.game / "eboot.bin").write_bytes(b"binary\0libSceAmpr\0")
        result = inspect(self.game)
        self.assertEqual(result["kind"], "import")
        self.assertFalse(result["apr"])

    def test_import_is_found_in_large_executable(self):
        # The marker sits past 64 MiB: the scan must read the whole file, not skip it.
        with (self.game / "eboot.bin").open("wb") as stream:
            stream.write(b"\0" * (70 * 1024 * 1024))
            stream.write(b"libSceAmpr")
        result = inspect(self.game)
        self.assertEqual(result["kind"], "import")
        self.assertTrue(result["imports"])

    def test_missing_signal_says_what_was_checked(self):
        result = inspect(self.game)
        self.assertEqual(result["kind"], "unknown")
        self.assertIn("eboot.bin", result["text"])

    def test_module_in_sce_module_is_not_assumed_to_be_emulator(self):
        (self.game / "sce_module").mkdir()
        (self.game / "sce_module/libSceAmpr.sprx").write_bytes(b"module")
        result = inspect(self.game)
        self.assertEqual(result["kind"], "module")
        self.assertIn("não dá para confirmar", result["text"])


if __name__ == "__main__":
    unittest.main()
