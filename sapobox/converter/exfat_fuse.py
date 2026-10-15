"""Read-only FUSE view of an exFAT image for the upstream LZ4 packer.

Runs in a separate process; the packing engine sees ordinary files but
reads them directly from the source image without staging the whole game.
"""
import argparse
import errno
import importlib
import importlib.util
import os
import stat
import sys

from .sources import DIRECTORY, scan_source
from ..i18n import tr  # noqa: E402

# python3-fusepy (Debian) instala o módulo como "fusepy"; o pacote do pip, como "fuse".
FUSE_MODULES = ("fuse", "fusepy")
FUSE_HINT = tr("FUSE não instalado. Instale python3-fusepy e fuse3 (sh instalar_dependencias.sh).")


def fuse_available():
    return any(importlib.util.find_spec(name) for name in FUSE_MODULES)


def load_fuse():
    for name in FUSE_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        return module.FUSE, module.FuseOSError, module.Operations
    raise SystemExit(FUSE_HINT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("mount")
    args = parser.parse_args()
    FUSE, FuseOSError, Operations = load_fuse()

    _, entries, _ = scan_source(args.image)
    # Para .ffpfsc, o conteúdo vem do leitor PFSC; para .exfat, do próprio arquivo.
    volume = next((e.reader for e in entries if e.reader is not None), None)
    by_path = {"/": None}
    children = {"/": [".", ".."]}
    for e in entries:
        key = "/" + e.rel
        by_path[key] = e
        parent, name = key.rsplit("/", 1)
        parent = parent or "/"
        children.setdefault(parent, [".", ".."]).append(name)
        if e.size == DIRECTORY:
            children.setdefault(key, [".", ".."])

    class ReadOnlyExfat(Operations):
        def __init__(self):
            self.fd = os.open(args.image, os.O_RDONLY)

        def getattr(self, path, fh=None):
            if path not in by_path:
                raise FuseOSError(errno.ENOENT)
            e = by_path[path]
            directory = e is None or e.size == DIRECTORY
            return {"st_mode": (stat.S_IFDIR | 0o555) if directory else
                    (stat.S_IFREG | 0o444), "st_nlink": 2 if directory else 1,
                    "st_size": 0 if directory else e.size, "st_mtime": 0 if directory else e.mtime,
                    "st_ctime": 0, "st_atime": 0, "st_uid": os.getuid(), "st_gid": os.getgid()}

        def readdir(self, path, fh):
            if path not in children:
                raise FuseOSError(errno.ENOTDIR)
            return children[path]

        def open(self, path, flags):
            e = by_path.get(path)
            if e is None or e.size == DIRECTORY:
                raise FuseOSError(errno.ENOENT)
            if flags & os.O_ACCMODE != os.O_RDONLY:
                raise FuseOSError(errno.EROFS)
            return 0

        def read(self, path, size, offset, fh):
            e = by_path.get(path)
            if e is None or e.size == DIRECTORY:
                raise FuseOSError(errno.ENOENT)
            if offset >= e.size:
                return b""
            end = min(e.size, offset + size)
            result = bytearray(end - offset)
            for logical, physical, length in e.extents:
                start = max(offset, logical)
                stop = min(end, logical + length)
                if start < stop:
                    try:
                        if volume is not None:
                            data = volume.read_at(physical + start - logical, stop - start)
                        else:
                            data = os.pread(self.fd, stop - start, physical + start - logical)
                    except ValueError as exc:
                        raise FuseOSError(errno.EIO) from exc
                    if len(data) != stop - start:
                        raise FuseOSError(errno.EIO)
                    result[start - offset:stop - offset] = data
            return bytes(result)

        def statfs(self, path):
            return {"f_bsize": 65536, "f_frsize": 65536, "f_blocks": 1,
                    "f_bfree": 0, "f_bavail": 0, "f_files": len(entries), "f_ffree": 0}

        def destroy(self, path):
            os.close(self.fd)

    # Only the caller's UID can see this mount; never allow writing.
    FUSE(ReadOnlyExfat(), args.mount, foreground=True, nothreads=False, ro=True,
         fsname="sapobox-exfat", subtype="sapobox")


if __name__ == "__main__":
    sys.exit(main())
