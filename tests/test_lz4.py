import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapobox.converter import catalog
from sapobox.converter.exfat_fuse import fuse_available
from sapobox.converter.lz4 import convert, validate_runtime
from sapobox.converter.sources import scan_source
from sapobox.converter.workflow import run as make_ffpfsc
from tests.test_converter import extract_nested


class LZ4Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lz4-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        (self.app / "sce_sys").mkdir(parents=True)
        (self.app / "fakelib").mkdir()
        (self.app / "assets").mkdir()
        (self.app / "sce_sys/param.json").write_text(json.dumps({"titleId": "PPSA54321", "titleName": "Test"}))
        (self.app / "fakelib/libSceAmpr.sprx").write_bytes(b"original")
        (self.app / "assets/content.bin").write_bytes(b"0123456789" * 30000)
        self.module = self.root / "packed.sprx"
        self.module.write_bytes(b"test PackedStdio")
        self.catalog = catalog.lookup
        catalog.lookup = lambda _: None
        self.addCleanup(lambda: setattr(catalog, "lookup", self.catalog))
        self.runtime = patch("sapobox.converter.lz4.validate_runtime", return_value=self.module)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)

    def test_folder_packs_and_keeps_source(self):
        folder, stats = convert(self.app, self.root, self.module, level=1, workers=2)
        self.assertEqual(stats["packed"], 1)
        self.assertTrue(stats["runtime"])
        self.assertTrue(list(folder.glob("*.pak")))
        self.assertTrue((folder / "ampr_assets.index.runtime").exists())
        self.assertTrue((folder / "ampr_assets.index.crc").exists())
        self.assertFalse((folder / "assets/content.bin").exists())
        self.assertEqual((self.app / "fakelib/libSceAmpr.sprx").read_bytes(), b"original")
        verify = subprocess.run([str(Path(__file__).parent.parent / "sapobox/converter/ampr_pack_cli"),
                                 "verify", str(folder), str(self.app)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_exfat_without_full_extraction(self):
        if not (fuse_available() and Path("/dev/fuse").exists()
                and shutil.which("fusermount3")):
            self.skipTest("FUSE não disponível no ambiente")
        ff, _ = make_ffpfsc(self.app, self.root, apr_module=self.module, workers=2)
        image = self.root / "source.exfat"
        extract_nested(ff, image)
        folder, stats = convert(image, self.root, self.module, level=1, workers=2)
        self.assertEqual(stats["packed"], 1)
        self.assertTrue((folder / "ampr_emu.index").exists())
        self.assertTrue(image.exists())

    def test_unpinned_runtime_rejected(self):
        self.runtime.stop()
        with self.assertRaisesRegex(ValueError, "PackedStdio"):
            validate_runtime(self.module)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            convert(self.app, self.root, self.module)


if __name__ == "__main__":
    unittest.main()
