import hashlib
import importlib.util
import json
import random
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from sapobox.converter import catalog
from sapobox.converter.exfat_fuse import fuse_available
from sapobox.converter.ffpfsc import PfscReader
from sapobox.converter.lz4 import convert
from sapobox.converter.sources import scan_source
from sapobox.converter.workflow import run as make_ffpfsc
from tests.test_converter import extract_nested


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close_volume(entries):
    # Todas as entradas de um .ffpfsc compartilham o mesmo leitor.
    for entry in entries:
        if entry.reader is not None:
            entry.reader.close()
            return


class FfpfscSourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffpfsc-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        (self.app / "sce_sys").mkdir(parents=True)
        (self.app / "fakelib").mkdir()
        (self.app / "assets").mkdir()
        (self.app / "sce_sys/param.json").write_text(
            json.dumps({"titleId": "PPSA13579", "titleName": "Teste ffpfsc"}))
        (self.app / "fakelib/libSceAmpr.sprx").write_bytes(b"original")
        rnd = random.Random(7)
        (self.app / "assets/content.bin").write_bytes(bytes(rnd.getrandbits(8) for _ in range(300000)))
        (self.app / "assets/text.bin").write_bytes(b"0123456789" * 40000)
        self.module = self.root / "packed.sprx"
        self.module.write_bytes(b"test PackedStdio")

        self.old_lookup = catalog.lookup
        catalog.lookup = lambda _: None
        self.addCleanup(lambda: setattr(catalog, "lookup", self.old_lookup))

        (self.root / "out").mkdir()
        self.ffpfsc, _ = make_ffpfsc(self.app, self.root / "out", apr_module=self.module, workers=2)

    def test_reader_matches_independent_decode(self):
        reference = self.root / "reference.exfat"
        extract_nested(self.ffpfsc, reference)
        truth = reference.read_bytes()
        reader = PfscReader(self.ffpfsc)
        self.addCleanup(reader.close)
        self.assertEqual(reader.length, len(truth))
        self.assertEqual(reader.read_at(0, reader.length), truth)
        rnd = random.Random(1)
        for _ in range(200):
            offset = rnd.randrange(0, reader.length - 1)
            size = rnd.randrange(1, min(200000, reader.length - offset) + 1)
            self.assertEqual(reader.read_at(offset, size), truth[offset:offset + size])
        with self.assertRaises(ValueError):
            reader.read_at(reader.length - 4, 8)

    def test_volume_has_the_same_files_and_bytes_as_the_folder(self):
        _, folder_entries, folder_info = scan_source(self.app)
        _, image_entries, image_info = scan_source(self.ffpfsc)
        self.assertEqual(folder_info["code"], image_info["code"])
        self.assertEqual(folder_info["apr"], image_info["apr"])
        # A conversão FF-PFSC troca o módulo APR pelo escolhido e grava ampr_emu.index: isso é esperado.
        expected = {e.rel for e in folder_entries}
        found = {e.rel for e in image_entries} - {"ampr_emu.index"}
        self.assertEqual(found, expected)
        self.addCleanup(close_volume, image_entries)
        for entry in image_entries:
            if entry.rel == "ampr_emu.index" or not entry.size or entry.size == (1 << 64) - 1:
                continue
            if entry.rel == "fakelib/libSceAmpr.sprx":
                self.assertEqual(entry.read(), self.module.read_bytes())
            else:
                self.assertEqual(entry.read(), (self.app / entry.rel).read_bytes(), entry.rel)

    def test_concurrent_reads_return_the_right_bytes(self):
        # O FUSE do packer lê em várias threads ao mesmo tempo: o leitor não pode trocar bytes entre elas.
        reference = self.root / "reference_concurrent.exfat"
        extract_nested(self.ffpfsc, reference)
        truth = reference.read_bytes()
        reader = PfscReader(self.ffpfsc)
        self.addCleanup(reader.close)
        errors = []

        def worker(seed):
            rnd = random.Random(seed)
            for _ in range(400):
                offset = rnd.randrange(0, reader.length - 1)
                size = rnd.randrange(1, min(300000, reader.length - offset) + 1)
                if reader.read_at(offset, size) != truth[offset:offset + size]:
                    errors.append((seed, offset, size))
                    return

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_corrupt_file_is_rejected(self):
        bogus = self.root / "bogus.ffpfsc"
        bogus.write_bytes(b"\0" * (7 << 20))
        with self.assertRaisesRegex(ValueError, "PFSC"):
            PfscReader(bogus)

    def test_ffpfsc_cannot_be_converted_to_ffpfsc_again(self):
        with self.assertRaisesRegex(ValueError, "LZ4"):
            make_ffpfsc(self.ffpfsc, self.root / "again", workers=2)

    def test_lz4_reads_the_ffpfsc_without_extracting_it(self):
        if not (fuse_available() and Path("/dev/fuse").exists()
                and shutil.which("fusermount3")):
            self.skipTest("FUSE não disponível no ambiente")
        before = sha256(self.ffpfsc)
        with patch("sapobox.converter.lz4.validate_runtime", return_value=self.module):
            out = self.root / "lz4"
            out.mkdir()
            folder, stats = convert(self.ffpfsc, out, self.module, level=1, workers=2)
        self.assertGreaterEqual(stats["packed"], 1)
        self.assertTrue(list(folder.glob("*.pak")))
        self.assertTrue((folder / "ampr_emu.index").exists())
        self.assertEqual(sha256(self.ffpfsc), before, "o .ffpfsc de origem não pode mudar")
        self.assertEqual(sorted(p.name for p in (self.root / "out").iterdir() if p.suffix == ".ffpfsc"),
                         [self.ffpfsc.name])


if __name__ == "__main__":
    unittest.main()
