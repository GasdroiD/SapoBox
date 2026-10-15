import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from sapobox.converter import catalog
from sapobox.converter.sources import Entry, Exfat, occupied_bytes, scan_source, validate_entries
from sapobox.converter.workflow import output_name, prepare, run


def extract_nested(ffpfsc, target):
    """Test-only independent PFSC block decode to inspect generated exFAT."""
    with open(ffpfsc, "rb") as image, open(target, "wb") as dest:
        base = 6 * 65536
        image.seek(base)
        head = image.read(48)
        assert head[:4] == b"PFSC"
        size = struct.unpack_from("<Q", head, 40)[0]
        blocks = (size + 65535) // 65536
        image.seek(base + 0x400)
        offsets = struct.unpack("<" + "Q" * (blocks + 1), image.read((blocks + 1) * 8))
        for a, b in zip(offsets, offsets[1:]):
            image.seek(base + a)
            data = image.read(b - a)
            if len(data) < 65536:
                data = zlib.decompress(data)
            dest.write(data)
        dest.truncate(size)


class OccupiedBytesTest(unittest.TestCase):
    def test_folder_is_the_sum_of_its_files_and_missing_path_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "jogo")
            (root / "sub").mkdir(parents=True)
            (root / "a.bin").write_bytes(b"x" * 1000)
            (root / "sub" / "b.bin").write_bytes(b"y" * 24)
            self.assertEqual(occupied_bytes(root), 1024)
            self.assertEqual(occupied_bytes(root / "a.bin"), 1000)
            self.assertEqual(occupied_bytes(root / "nao-existe.vhash"), 0)


class ConversionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        (self.app / "sce_sys").mkdir(parents=True)
        (self.app / "sce_sys" / "param.json").write_text(
            json.dumps({"titleId": "PPSA12345", "titleName": "Jogo de teste"}))
        (self.app / "data").mkdir()
        (self.app / "data" / "content.bin").write_bytes(b"PS5" * 40000)
        self.old_lookup = catalog.lookup
        catalog.lookup = lambda _: "Nome / Teste"
        self.addCleanup(lambda: setattr(catalog, "lookup", self.old_lookup))

    def test_folder_and_exfat_roundtrip(self):
        output, apr = run(self.app, self.root, workers=2)
        self.assertFalse(apr)
        self.assertEqual(output.name, output_name("PPSA12345", "Nome / Teste"))
        self.assertTrue(Path(str(output) + ".vhash").is_file())
        image = self.root / "source.exfat"
        extract_nested(output, image)
        _, entries, info = scan_source(image)
        self.assertEqual(info["code"], "PPSA12345")
        self.assertEqual(next(e for e in entries if e.rel == "data/content.bin").read(), b"PS5" * 40000)
        second = self.root / "second"
        second.mkdir()
        result, _ = run(image, second, workers=2)
        second_image = self.root / "second.exfat"
        extract_nested(result, second_image)
        _, files, _ = scan_source(second_image)
        self.assertEqual(next(e for e in files if e.rel == "data/content.bin").read(), b"PS5" * 40000)

    def test_apr_module_and_index_only_in_output(self):
        (self.app / "fakelib").mkdir()
        original = self.app / "fakelib" / "libSceAmpr.sprx"
        original.write_bytes(b"original")
        module = self.root / "new.sprx"
        module.write_bytes(b"replacement")
        output, apr = run(self.app, self.root, apr_module=module, workers=2)
        self.assertTrue(apr)
        self.assertEqual(original.read_bytes(), b"original")
        self.assertFalse((self.app / "ampr_emu.index").exists())
        image = self.root / "apr.exfat"
        extract_nested(output, image)
        _, entries, _ = scan_source(image)
        lookup = {e.rel: e for e in entries}
        self.assertEqual(lookup["fakelib/libSceAmpr.sprx"].read(), b"replacement")
        index = lookup["ampr_emu.index"].read()
        self.assertEqual(index[:8], b"AMPRIDX3")
        self.assertIn(b"/app0/fakelib/libSceAmpr.sprx", index)
        self.assertIn(b"/app0/data/content.bin", index)

    def test_missing_apr_module_rejected(self):
        (self.app / "fakelib").mkdir()
        (self.app / "fakelib" / "libSceAmpr.sprx").write_bytes(b"original")
        with self.assertRaisesRegex(ValueError, "Selecione"):
            run(self.app, self.root)

    def test_empty_directories_preserved(self):
        (self.app / "unused" / "empty").mkdir(parents=True)
        output, _ = run(self.app, self.root, workers=2)
        image = self.root / "empty.exfat"
        extract_nested(output, image)
        _, entries, _ = scan_source(image)
        self.assertIn("unused/empty", {e.rel for e in entries})

    def test_interrupted_temp_not_overwritten(self):
        output = self.root / output_name("PPSA12345", "Nome / Teste")
        temp = self.root / ("." + output.name + ".gc-compress.tmp")
        temp.write_bytes(b"old unfinished data")
        with self.assertRaises(FileExistsError):
            run(self.app, self.root)
        self.assertEqual(temp.read_bytes(), b"old unfinished data")

    def test_unsafe_name_rejected(self):
        with self.assertRaises(ValueError):
            validate_entries([Entry("data/é.bin", "/tmp/file", 1)])
        with self.assertRaises(ValueError):
            validate_entries([Entry("X", "/tmp/file", 1), Entry("x", "/tmp/file", 1)])

    def test_existing_output_not_replaced(self):
        output = self.root / output_name("PPSA12345", "Nome / Teste")
        output.write_bytes(b"important")
        with self.assertRaises(FileExistsError):
            run(self.app, self.root)
        self.assertEqual(output.read_bytes(), b"important")

    def test_catalog_mapping(self):
        result = catalog.parse_catalog([["A", {"uuid": "XX-PPSA12345_00-XX", "name": "Actual Name"}]])
        self.assertEqual(result["PPSA12345"], "Actual Name")


if __name__ == "__main__":
    unittest.main()
