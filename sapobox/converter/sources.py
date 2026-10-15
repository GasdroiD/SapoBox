"""Read-only folder/exFAT sources. No mounts or full-image temporary copies."""
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import json
import os
import re
import struct

from .ffpfsc import PfscReader
from .pfs import PfsImage, is_pfs
from ..i18n import tr  # noqa: E402

DIRECTORY = (1 << 64) - 1


def pfs_entries(reader, path, check=lambda: None):
    """Entradas de um volume PFS (sistema de arquivos do console) guardado no .ffpfsc."""
    entries = []
    for item in PfsImage(reader).entries(check):
        if item.is_dir:
            entries.append(Entry(item.rel, str(path), DIRECTORY, 0, [], reader=reader))
        else:
            entries.append(Entry(item.rel, str(path), item.size, 0, item.extents, reader=reader))
    return entries


@dataclass
class Entry:
    rel: str
    source: str
    size: int
    mtime: int = 0
    extents: list[tuple[int, int, int]] = field(default_factory=list)
    # Leitor do volume quando a origem é um .ffpfsc; None para pastas e .exfat.
    reader: object = field(default=None, repr=False, compare=False)

    def read(self, limit=8 * 1024 * 1024):
        if self.size > limit:
            raise ValueError(tr('Metadado muito grande: {0}').format(self.rel))
        data = bytearray(self.size)
        if self.reader is not None:
            for logical, physical, size in self.extents:
                data[logical:logical + size] = self.reader.read_at(physical, size)
            return bytes(data)
        with open(self.source, "rb", buffering=0) as f:
            for logical, physical, size in self.extents:
                f.seek(physical)
                block = f.read(size)
                if len(block) != size:
                    raise ValueError(tr('Arquivo truncado: {0}').format(self.rel))
                data[logical:logical + size] = block
        return bytes(data)


def file_entry(path, rel):
    path = Path(path).resolve(strict=True)
    st = path.stat()
    return Entry(rel, str(path), st.st_size, int(st.st_mtime),
                 [(0, 0, st.st_size)] if st.st_size else [])


def occupied_bytes(path):
    """Bytes que um arquivo ocupa; em uma pasta, a soma de todos os arquivos dentro dela."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return 0


def validate_entries(entries):
    seen = set()
    for e in entries:
        parts = e.rel.split("/")
        if (not parts or any(p in ("", ".", "..") for p in parts)
                or len(e.rel.encode()) >= 998 or len(e.source.encode()) >= 1024):
            raise ValueError(tr('Caminho não suportado: {0}').format(e.rel))
        # Upstream's name writer supports ASCII, not UTF-8 conversion.
        if any(len(p) > 255 or any(ord(c) < 32 or ord(c) >= 127 or c in '\\:*?"<>|' for c in p)
               for p in parts):
            raise ValueError(tr('Nome interno não suportado pelo gravador exFAT: {0}').format(e.rel))
        key = e.rel.lower()
        if key in seen:
            raise ValueError(tr('Caminhos duplicados (ignorando maiúsculas): {0}').format(e.rel))
        seen.add(key)
    files = {e.rel.lower() for e in entries if e.size != DIRECTORY}
    for e in entries:
        if any(str(p).lower() in files for p in PurePosixPath(e.rel).parents if str(p) != '.'):
            raise ValueError(tr('Arquivo usado como diretório: {0}').format(e.rel))


def folder_entries(root, check=lambda: None):
    root = Path(root).resolve(strict=True)
    if not (root / "sce_sys" / "param.json").is_file() and (root / "app0" / "sce_sys" / "param.json").is_file():
        root /= "app0"
    entries = []
    def visit(directory):
        check()
        with os.scandir(directory) as children:
            for child in children:
                check()
                rel = Path(child.path).relative_to(root).as_posix()
                if child.is_symlink():
                    raise ValueError(tr('Link simbólico não suportado: {0}').format(rel))
                if child.is_dir(follow_symlinks=False):
                    entries.append(Entry(rel, str(root), DIRECTORY))
                    visit(child.path)
                elif child.is_file(follow_symlinks=False):
                    entries.append(file_entry(child.path, rel))
                else:
                    raise ValueError(tr('Arquivo especial não suportado: {0}').format(rel))
    visit(root)
    return root, entries


class Exfat:
    """Bounds-checked extent reader, including FAT chains and validDataLength.

    Reads the file at path, or the volume exposed by reader (a .ffpfsc, see PfscReader).
    """
    def __init__(self, path, check=lambda: None, reader=None):
        self.path = str(Path(path).resolve(strict=True))
        self.reader = reader
        self.f = None if reader is not None else open(self.path, "rb", buffering=0)
        self.check = check
        self.length = reader.length if reader is not None else os.fstat(self.f.fileno()).st_size
        self.base = 0
        self.fat_cache = {}
        try:
            boot = self.read_at(0, 512)
            if boot[3:11] != b"EXFAT   ":
                candidates = []
                if boot[510:512] == b"\x55\xaa":
                    for p in range(446, 510, 16):
                        if boot[p + 4]:
                            candidates.append(struct.unpack_from("<I", boot, p + 8)[0] * 512)
                if self.length >= 1024:
                    gpt = self.read_at(512, 512)
                    if gpt[:8] == b"EFI PART":
                        table, count, width = struct.unpack_from("<QII", gpt, 72)
                        if 128 <= width <= 4096 and count <= 4096:
                            for i in range(count):
                                part = self.read_at(table * 512 + i * width, width)
                                if any(part[:16]):
                                    candidates.append(struct.unpack_from("<Q", part, 32)[0] * 512)
                matches = []
                for offset in set(candidates):
                    if 0 <= offset <= self.length - 512:
                        candidate = self.read_at(offset, 512)
                        if candidate[3:11] == b"EXFAT   ":
                            matches.append((offset, candidate))
                if len(matches) != 1:
                    raise ValueError(tr("A imagem precisa conter exatamente um volume exFAT."))
                self.base, boot = matches[0]
            if boot[510:512] != b"\x55\xaa" or not 9 <= boot[108] <= 12 or boot[108] + boot[109] > 25:
                raise ValueError(tr("Cabeçalho exFAT inválido."))
            self.sector = 1 << boot[108]
            self.cluster = self.sector << boot[109]
            volume = struct.unpack_from("<Q", boot, 72)[0] * self.sector
            fat, fat_len, heap, self.clusters, self.root = struct.unpack_from("<IIIII", boot, 80)
            flags = struct.unpack_from("<H", boot, 106)[0]
            if boot[110] not in (1, 2) or (flags & 1 and boot[110] != 2):
                raise ValueError(tr("FAT ativa inválida."))
            self.fat = self.base + (fat + (flags & 1) * fat_len) * self.sector
            self.heap = self.base + heap * self.sector
            self.volume_end = self.base + volume
            if (volume > self.length - self.base or not self.clusters
                    or fat < 24 or fat_len * self.sector < (self.clusters + 2) * 4
                    or heap < fat + fat_len * boot[110]
                    or self.heap + self.clusters * self.cluster > self.volume_end):
                raise ValueError(tr("Geometria exFAT inválida ou imagem truncada."))
            region = self.read_at(self.base, 12 * self.sector)
            checksum = 0
            for i, b in enumerate(region[:11 * self.sector]):
                if i not in (106, 107, 112):
                    checksum = (((checksum >> 1) | ((checksum & 1) << 31)) + b) & 0xffffffff
            if region[11 * self.sector:] != struct.pack("<I", checksum) * (self.sector // 4):
                raise ValueError(tr("Checksum do cabeçalho exFAT inválido."))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.f is not None:
            self.f.close()

    def read_at(self, offset, size):
        self.check()
        if offset < 0 or size < 0 or offset + size > self.length:
            raise ValueError(tr("Leitura fora dos limites da imagem."))
        if self.reader is not None:
            return self.reader.read_at(offset, size)
        self.f.seek(offset)
        data = self.f.read(size)
        if len(data) != size:
            raise ValueError(tr("Imagem truncada."))
        return data

    def cluster_offset(self, cluster):
        if not 2 <= cluster < self.clusters + 2:
            raise ValueError(tr("Cluster exFAT inválido."))
        return self.heap + (cluster - 2) * self.cluster

    def next_cluster(self, cluster):
        self.cluster_offset(cluster)
        page, pos = divmod(cluster * 4, 65536)
        if page not in self.fat_cache:
            if len(self.fat_cache) >= 64:
                self.fat_cache.clear()
            end = min(65536, (self.clusters + 2) * 4 - page * 65536)
            self.fat_cache[page] = self.read_at(self.fat + page * 65536, end)
        return struct.unpack_from("<I", self.fat_cache[page], pos)[0]

    def extents(self, first, size, contiguous):
        if not size:
            return []
        if size > self.clusters * self.cluster:
            raise ValueError(tr("Arquivo maior que o volume."))
        if contiguous:
            offset = self.cluster_offset(first)
            if offset + size > self.heap + self.clusters * self.cluster:
                raise ValueError(tr("Extensão exFAT fora dos limites."))
            return [(0, offset, size)]
        result, visited = [], set()
        remaining, logical, c = size, 0, first
        while remaining:
            self.check()
            if c in visited:
                raise ValueError(tr("Ciclo na cadeia FAT."))
            visited.add(c)
            offset = self.cluster_offset(c)
            n = min(remaining, self.cluster)
            if result and result[-1][1] + result[-1][2] == offset:
                start, physical, length = result[-1]
                result[-1] = (start, physical, length + n)
            else:
                result.append((logical, offset, n))
            logical += n
            remaining -= n
            if remaining:
                c = self.next_cluster(c)
        return result

    def entries(self):
        result, visited_dirs = [], set()
        def walk(first, size, contiguous, parent, depth=0):
            self.check()
            if depth > 128 or first in visited_dirs:
                raise ValueError(tr("Árvore de diretórios inválida."))
            visited_dirs.add(first)
            if size is None:
                chunks, seen, c = [], set(), first
                while c < 0xfffffff8:
                    if c in seen or len(chunks) * self.cluster >= 64 * 1024 * 1024:
                        raise ValueError(tr("Diretório raiz inválido ou excessivamente grande."))
                    seen.add(c)
                    chunks.append(self.read_at(self.cluster_offset(c), self.cluster))
                    c = self.next_cluster(c)
                data = b"".join(chunks)
            else:
                if size > 64 * 1024 * 1024:
                    raise ValueError(tr("Diretório excessivamente grande."))
                data = b"".join(self.read_at(p, n) for _, p, n in self.extents(first, size, contiguous))
            i = 0
            while i + 32 <= len(data):
                self.check()
                typ = data[i]
                if not typ:
                    break
                if typ != 0x85:
                    if typ & 0x80 and not typ & 0x20 and typ not in (0x81, 0x82, 0x83):
                        raise ValueError(tr("Entrada exFAT crítica não suportada."))
                    i += 32
                    continue
                count = data[i + 1] + 1
                block = data[i:i + count * 32]
                if count < 3 or len(block) != count * 32 or block[32] != 0xc0:
                    raise ValueError(tr("Entrada de arquivo exFAT inválida."))
                checksum = 0
                for j, b in enumerate(block):
                    if j not in (2, 3):
                        checksum = (((checksum >> 1) | ((checksum & 1) << 15)) + b) & 65535
                if checksum != struct.unpack_from("<H", block, 2)[0]:
                    raise ValueError(tr("Checksum de diretório exFAT inválido."))
                raw_name = b"".join(block[j + 2:j + 32] for j in range(64, len(block), 32) if block[j] == 0xc1)
                name_length = block[35]
                if not name_length or len(raw_name) < name_length * 2:
                    raise ValueError(tr("Nome exFAT inválido."))
                name = raw_name[:name_length * 2].decode("utf-16le")
                if name in ("", ".", "..") or "/" in name or "\\" in name:
                    raise ValueError(tr("Caminho exFAT inválido."))
                rel = f"{parent}/{name}" if parent else name
                valid = struct.unpack_from("<Q", block, 40)[0]
                first_child = struct.unpack_from("<I", block, 52)[0]
                length = struct.unpack_from("<Q", block, 56)[0]
                is_dir = bool(struct.unpack_from("<H", block, 4)[0] & 16)
                if valid > length:
                    raise ValueError(tr("Comprimento exFAT inválido."))
                contiguous_child = bool(block[33] & 2)
                if is_dir:
                    result.append(Entry(rel, self.path, DIRECTORY))
                    walk(first_child, length, contiguous_child, rel, depth + 1)
                else:
                    result.append(Entry(rel, self.path, length, 0,
                                        self.extents(first_child, valid, contiguous_child),
                                        reader=self.reader))
                i += count * 32
        walk(self.root, None, False, "")
        return result


APR_PATHS = {f"{directory}/libsceampr.{ext}" for directory in ("fakelib", "sce_module")
             for ext in ("sprx", "prx")}


def scan_source(path, check=lambda: None):
    path = Path(path).resolve(strict=True)
    if path.is_dir():
        path, entries = folder_entries(path, check)
    elif path.suffix.lower() == ".ffpfsc":
        # O volume fica dentro do .ffpfsc: o leitor precisa continuar aberto enquanto as entradas existirem.
        reader = PfscReader(path)
        try:
            if is_pfs(reader):
                entries = pfs_entries(reader, path, check)
            else:
                entries = Exfat(path, check, reader=reader).entries()
        except BaseException:
            reader.close()
            raise
    else:
        image = Exfat(path, check)
        try:
            entries = image.entries()
        finally:
            image.close()
    validate_entries(entries)
    lookup = {e.rel.lower(): e for e in entries}
    param = lookup.get("sce_sys/param.json")
    if param is None:
        raise ValueError(tr("Não encontrei sce_sys/param.json na raiz do jogo."))
    meta = json.loads(param.read().decode("utf-8-sig"))
    code = meta.get("titleId", "").upper()
    if not re.fullmatch(r"PPSA\d{5}", code):
        match = re.search(r"PPSA\d{5}", meta.get("contentId", ""))
        if not match:
            raise ValueError(tr("Não foi possível identificar o código PPSA nos metadados."))
        code = match.group()
    localized = meta.get("localizedParameters", {})
    default = localized.get("defaultLanguage", "en-US")
    name = next((localized.get(lang, {}).get("titleName") for lang in
                 (default, "pt-BR", "pt-PT", "en-US") if isinstance(localized.get(lang), dict)
                 and localized[lang].get("titleName")), meta.get("titleName", code))
    return path, entries, {"code": code, "name": name,
                           "apr": any(p in lookup for p in APR_PATHS),
                           "size": sum(e.size for e in entries if e.size != DIRECTORY)}
