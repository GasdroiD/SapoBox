import json
import os
import random
import struct
import unittest

from sapobox.converter.pfs import BLOCK, INODE_SIZE, PFS_MAGIC, PFS_VERSION, PfsError, PfsImage, is_pfs
from sapobox.converter.sources import DIRECTORY, pfs_entries


class MemoryImage:
    """Imagem em memória com a mesma interface de leitura do PfscReader."""

    def __init__(self, data):
        self.data = bytes(data)
        self.length = len(self.data)

    def read_at(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.length:
            raise ValueError("Leitura fora dos limites do volume.")
        return self.data[offset:offset + size]


def dirent(number, typ, name):
    raw = name.encode()
    size = 16 + len(raw)
    size += (8 - size % 8) % 8
    return struct.pack("<IIII", number, typ, len(raw), size) + raw + b"\0" * (size - 16 - len(raw))


def build_pfs(tree):
    """Monta uma imagem PFS com o layout do writer vendorizado: superraiz, fpt, raiz do jogo
    no inode 2, dados contíguos por arquivo, e cada diretório com um bloco de entradas."""
    blocks = {}
    inodes = {}
    next_block = 6
    next_inode = 3

    def alloc(data_len):
        nonlocal next_block
        start = next_block
        next_block += max(1, -(-data_len // BLOCK))
        return start

    def build_dir(number, content, parent):
        nonlocal next_inode
        ents = [dirent(number, 4, "."), dirent(parent, 5, "..")]
        pending = []
        for name, value in content.items():
            child = next_inode
            next_inode += 1
            pending.append((child, name, value))
            ents.append(None)
        block_start = alloc(BLOCK)
        blob = b"".join(ents[:2]) + b"".join(
            dirent(child, 3 if isinstance(value, dict) else 2, name) for child, name, value in pending)
        blocks[block_start] = blob
        inodes[number] = dict(mode=0x41FF, size=BLOCK, blocks=1, db0=block_start)
        for child, name, value in pending:
            if isinstance(value, dict):
                build_dir(child, value, number)
            else:
                start = alloc(len(value))
                blocks[start] = value
                inodes[child] = dict(mode=0x81FF, size=len(value),
                                     blocks=max(1, -(-len(value) // BLOCK)), db0=start)

    build_dir(2, tree, 2)
    superroot_block = alloc(BLOCK)
    blocks[superroot_block] = dirent(0, 4, ".") + dirent(0, 5, "..")
    inodes[0] = dict(mode=0x41FF, size=BLOCK, blocks=1, db0=superroot_block)
    fpt_block = alloc(8)
    blocks[fpt_block] = b"\0" * 8
    inodes[1] = dict(mode=0x81FF, size=8, blocks=1, db0=fpt_block)

    image = bytearray(next_block * BLOCK)
    header = struct.pack("<QQ", PFS_VERSION, PFS_MAGIC)
    image[0:16] = header
    struct.pack_into("<I", image, 0x20, BLOCK)
    struct.pack_into("<Q", image, 0x30, len(inodes))
    struct.pack_into("<Q", image, 0x38, next_block)
    table = bytearray(BLOCK)
    for number, info in inodes.items():
        raw = bytearray(INODE_SIZE)
        struct.pack_into("<HHI", raw, 0, info["mode"], 1, 0x10)
        struct.pack_into("<QQ", raw, 8, info["size"], info["size"])
        struct.pack_into("<I", raw, 0x60, info["blocks"])
        struct.pack_into("<12i", raw, 0x64, info["db0"], *([-1] * 11))
        struct.pack_into("<5i", raw, 0x94, *([-1] * 5))
        table[number * INODE_SIZE:(number + 1) * INODE_SIZE] = raw
    image[BLOCK:2 * BLOCK] = table
    for start, data in blocks.items():
        image[start * BLOCK:start * BLOCK + len(data)] = data
    return bytes(image), inodes


class PfsReadingTest(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(3)
        self.param = json.dumps({"titleId": "PPSA00042", "titleName": "Teste PFS"}).encode()
        self.eboot = bytes(rnd.getrandbits(8) for _ in range(BLOCK * 2 + 777))
        self.readme = b"leia-me\n" * 10
        self.tree = {"sce_sys": {"param.json": self.param}, "eboot.bin": self.eboot,
                     "readme.txt": self.readme, "empty.dat": b""}
        self.image, self.inodes = build_pfs(self.tree)

    def test_lists_the_tree_and_reads_every_file(self):
        image = MemoryImage(self.image)
        self.assertTrue(is_pfs(image))
        entries = {e.rel: e for e in PfsImage(image).entries()}
        self.assertEqual(set(entries), {"sce_sys", "sce_sys/param.json", "eboot.bin", "readme.txt", "empty.dat"})
        self.assertTrue(entries["sce_sys"].is_dir)
        self.assertEqual(entries["eboot.bin"].size, len(self.eboot))
        for rel, value in (("sce_sys/param.json", self.param), ("eboot.bin", self.eboot),
                           ("readme.txt", self.readme), ("empty.dat", b"")):
            data = b"".join(image.read_at(p, n) for _l, p, n in entries[rel].extents)
            self.assertEqual(data, value, rel)

    def test_non_pfs_volume_is_recognised_and_refused(self):
        zeros = MemoryImage(bytes(BLOCK * 8))
        self.assertFalse(is_pfs(zeros))
        with self.assertRaisesRegex(PfsError, "cabeçalho"):
            PfsImage(zeros)

    def test_fragmented_inode_is_refused_with_a_clear_error(self):
        data = bytearray(self.image)
        inode_offset = BLOCK + self.inodes_number("eboot.bin") * INODE_SIZE
        struct.pack_into("<i", data, inode_offset + 0x64 + 4, 99)  # db[1] preenchido
        with self.assertRaisesRegex(PfsError, "fragmentados"):
            [e for e in PfsImage(MemoryImage(data)).entries() if e.rel == "eboot.bin"]

    def test_block_count_mismatch_is_refused(self):
        data = bytearray(self.image)
        inode_offset = BLOCK + self.inodes_number("eboot.bin") * INODE_SIZE
        struct.pack_into("<I", data, inode_offset + 0x60, 1)
        with self.assertRaisesRegex(PfsError, "contagem de blocos"):
            PfsImage(MemoryImage(data)).entries()

    def test_pfs_volume_becomes_source_entries(self):
        image = MemoryImage(self.image)
        entries = pfs_entries(image, "/x/PPSA00042.ffpfsc")
        files = {e.rel: e for e in entries if e.size != DIRECTORY}
        self.assertEqual(files["eboot.bin"].read(limit=1 << 30), self.eboot)
        self.assertEqual(files["sce_sys/param.json"].read(), self.param)
        self.assertIs(files["eboot.bin"].reader, image)

    def inodes_number(self, rel):
        # Os inodes são numerados na ordem em que o montador os cria; localiza pelo tamanho.
        size = len(self.eboot)
        return next(n for n, info in self.inodes.items() if info["size"] == size and info["mode"] == 0x81FF)


if __name__ == "__main__":
    unittest.main()
