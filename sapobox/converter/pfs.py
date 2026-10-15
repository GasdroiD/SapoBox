"""Leitura somente leitura de um volume PFS (sistema de arquivos nativo do console) dentro de um .ffpfsc.

Alguns .ffpfsc guardam uma imagem PFS, e não um exFAT. O layout foi confirmado no código
vendorizado (vendor/ps5gc/pfs_compress.c: write_header_blob, write_inode, append_dirent,
write_outer_pfs_metadata) e num arquivo real:

- bloco 0: cabeçalho. Versão 2 em +0x00, magic 20130315 em +0x08, tamanho do bloco
  (65536) em +0x20, contagem de inodes em +0x30 e último bloco em +0x38;
- a partir do bloco 1: tabela de inodes, 0xA8 bytes cada, 390 por bloco;
- inode 2: diretório raiz do jogo (uroot);
- inode: flags em +0x04, tamanho em +0x08, contagem de blocos em +0x60, ponteiros diretos
  db[0..11] em +0x64 e indiretos em +0x94;
- diretório: entradas com inode (4), tipo (4), tamanho do nome (4), tamanho da entrada (4),
  nome e padding até múltiplo de 8. Tipos: 2 arquivo, 3 diretório, 4 ".", 5 "..".

No arquivo real cada arquivo ocupa uma sequência contínua de blocos começando em db[0]. Um
inode que use db[1..] ou ponteiros indiretos é recusado com erro claro, em vez de ler dados
errados.
"""
from collections import namedtuple
import struct
from ..i18n import tr  # noqa: E402

BLOCK = 65536
INODE_SIZE = 0xA8
INODES_PER_BLOCK = BLOCK // INODE_SIZE
PFS_VERSION = 2
PFS_MAGIC = 20130315
ROOT_INODE = 2
TYPE_FILE = 2
TYPE_DIR = 3
TYPE_DOT = 4
TYPE_DOTDOT = 5
MAX_DEPTH = 64

Inode = namedtuple("Inode", "number mode flags size blocks db ind")
# (caminho relativo, é diretório, tamanho, extensões (lógico, físico, tamanho))
PfsEntry = namedtuple("PfsEntry", "rel is_dir size extents")


class PfsError(ValueError):
    pass


def is_pfs(reader):
    """True quando o volume começa com o cabeçalho PFS."""
    if reader.length < 16:
        return False
    version, magic = struct.unpack("<QQ", reader.read_at(0, 16))
    return (version, magic) == (PFS_VERSION, PFS_MAGIC)


class PfsImage:
    """Cabeçalho validado e acesso a inodes, dados e diretórios."""

    def __init__(self, reader):
        self.reader = reader
        head = reader.read_at(0, 0x60)
        version, magic = struct.unpack_from("<QQ", head, 0)
        if (version, magic) != (PFS_VERSION, PFS_MAGIC):
            raise PfsError(tr("Volume PFS inválido: cabeçalho não encontrado."))
        block_size = struct.unpack_from("<I", head, 0x20)[0]
        if block_size != BLOCK:
            raise PfsError(tr('Volume PFS com bloco de {0} bytes não é suportado.').format(block_size))
        self.final_ndblock = struct.unpack_from("<Q", head, 0x38)[0]
        if self.final_ndblock == 0 or self.final_ndblock * BLOCK > reader.length:
            raise PfsError(tr("Volume PFS truncado ou com cabeçalho inconsistente."))

    def inode(self, number):
        block = 1 + number // INODES_PER_BLOCK
        offset = block * BLOCK + (number % INODES_PER_BLOCK) * INODE_SIZE
        raw = self.reader.read_at(offset, INODE_SIZE)
        mode, _nlink, flags = struct.unpack_from("<HHI", raw, 0)
        size = struct.unpack_from("<Q", raw, 8)[0]
        blocks = struct.unpack_from("<I", raw, 0x60)[0]
        db = struct.unpack_from("<12i", raw, 0x64)
        ind = struct.unpack_from("<5i", raw, 0x94)
        return Inode(number, mode, flags, size, blocks, db, ind)

    def extents(self, node):
        """Extensões de dados de um inode. Só o layout contíguo é suportado."""
        if node.size == 0:
            return []
        if node.db[0] < 0 or any(p >= 0 for p in node.db[1:]) or any(p >= 0 for p in node.ind):
            raise PfsError(tr('Inode {0} usa blocos fragmentados, ainda não suportado.').format(node.number))
        expected = -(-node.size // BLOCK)
        if node.blocks != expected:
            raise PfsError(tr('Inode {0}: contagem de blocos não confere com o tamanho.').format(node.number))
        start = node.db[0] * BLOCK
        if start < 0 or start + node.size > self.reader.length:
            raise PfsError(tr('Inode {0} aponta para dados fora do volume.').format(node.number))
        return [(0, start, node.size)]

    def read(self, node):
        return b"".join(self.reader.read_at(physical, size)
                        for _logical, physical, size in self.extents(node))

    def dirents(self, node):
        data = self.read(node)
        out, pos = [], 0
        while pos + 16 <= len(data):
            number, typ, name_len, entry_size = struct.unpack_from("<IIII", data, pos)
            if entry_size == 0:
                break  # o resto do bloco é preenchimento zerado
            if entry_size < 16 + name_len or entry_size % 8 or pos + entry_size > len(data):
                raise PfsError(tr("Diretório PFS corrompido."))
            name = data[pos + 16:pos + 16 + name_len].decode("utf-8", "replace")
            out.append((number, typ, name))
            pos += entry_size
        return out

    def entries(self, check=lambda: None):
        """Árvore a partir da raiz do jogo, em ordem de visita, sem ".", ".." nem a superraiz."""
        result = []
        visited = set()

        def visit(node, prefix, depth):
            check()
            if depth > MAX_DEPTH:
                raise PfsError(tr("Árvore PFS profunda demais."))
            if node.number in visited:
                raise PfsError(tr("Árvore PFS com ciclo."))
            visited.add(node.number)
            for number, typ, name in self.dirents(node):
                if typ in (TYPE_DOT, TYPE_DOTDOT) or name in (".", ".."):
                    continue
                child = self.inode(number)
                rel = prefix + name
                if typ == TYPE_DIR:
                    result.append(PfsEntry(rel, True, 0, []))
                    visit(child, rel + "/", depth + 1)
                elif typ == TYPE_FILE:
                    result.append(PfsEntry(rel, False, child.size, self.extents(child)))
                else:
                    raise PfsError(tr('Tipo de entrada PFS não suportado: {0}.').format(typ))

        visit(self.inode(ROOT_INODE), "", 0)
        return result
