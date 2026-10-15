"""Leitura somente leitura do volume exFAT guardado dentro de um .ffpfsc.

O arquivo tem o cabeçalho PFSC em 0x60000, o tamanho do volume em +40, uma tabela de
offsets em +0x400 e blocos de 64 KiB (comprimidos com zlib quando menores que 64 KiB).
Os blocos são decodificados sob demanda e guardados num cache pequeno, então o volume
inteiro nunca precisa ser extraído para o disco.

É usado por várias threads ao mesmo tempo (o FUSE do packer atende leituras em paralelo),
então as leituras usam os.pread, que não depende de posição compartilhada, e o cache é
protegido por lock.
"""
from collections import OrderedDict
import os
import struct
import threading
import zlib
from ..i18n import tr  # noqa: E402

BASE = 6 * 65536
BLOCK = 65536
TABLE = BASE + 0x400
CACHE_BLOCKS = 64


class PfscReader:
    """Expõe o volume interno como se fosse um arquivo: length e read_at(offset, size)."""

    def __init__(self, path):
        self.path = str(path)
        self.fd = os.open(self.path, os.O_RDONLY)
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        try:
            head = self.raw(BASE, 48)
            if head[:4] != b"PFSC":
                raise ValueError(tr("Arquivo .ffpfsc inválido: cabeçalho PFSC não encontrado."))
            self.length = struct.unpack_from("<Q", head, 40)[0]
            blocks = (self.length + BLOCK - 1) // BLOCK
            table = self.raw(TABLE, (blocks + 1) * 8)
            self.offsets = struct.unpack("<" + "Q" * (blocks + 1), table)
            file_size = os.fstat(self.fd).st_size
            if any(b < a for a, b in zip(self.offsets, self.offsets[1:])) or \
                    BASE + self.offsets[-1] > file_size:
                raise ValueError(tr("Arquivo .ffpfsc inválido: tabela de blocos corrompida."))
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def raw(self, offset, size):
        data = os.pread(self.fd, size, offset)
        if len(data) != size:
            raise ValueError(tr("Arquivo .ffpfsc truncado."))
        return data

    def block(self, index):
        with self.lock:
            if index in self.cache:
                self.cache.move_to_end(index)
                return self.cache[index]
        start, end = self.offsets[index], self.offsets[index + 1]
        expected = min(BLOCK, self.length - index * BLOCK)
        data = self.raw(BASE + start, end - start)
        if len(data) < BLOCK:
            try:
                data = zlib.decompress(data)
            except zlib.error as exc:
                raise ValueError(tr('Bloco {0} do .ffpfsc não pode ser descomprimido.').format(index)) from exc
        if len(data) < expected:
            raise ValueError(tr('Bloco {0} do .ffpfsc é menor que o esperado.').format(index))
        data = data[:expected]
        with self.lock:
            self.cache[index] = data
            self.cache.move_to_end(index)
            while len(self.cache) > CACHE_BLOCKS:
                self.cache.popitem(last=False)
        return data

    def read_at(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.length:
            raise ValueError(tr("Leitura fora dos limites do volume."))
        out = bytearray()
        position = offset
        end = offset + size
        while position < end:
            index, within = divmod(position, BLOCK)
            chunk = self.block(index)[within:within + (end - position)]
            out += chunk
            position += len(chunk)
        return bytes(out)
